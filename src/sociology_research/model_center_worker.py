"""One-shot Docling model download / offline verification worker.

This module can execute in a normal Python interpreter or be dispatched by a
PyInstaller runtime hook, before the bundled desktop server entrypoint runs.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def _minimal_pdf() -> bytes:
    content = b"BT /F1 16 Tf 35 145 Td (SRA test page for offline Docling) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 220] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(pdf)


def download(output_dir: Path, *, formula: bool) -> None:
    from docling.utils.model_downloader import download_models
    import threading
    import time
    print("SRA stage: downloading layout, table, RapidOCR and optional formula models", flush=True)
    stop = threading.Event()
    started = time.monotonic()

    def heartbeat() -> None:
        while not stop.wait(30):
            elapsed = int(time.monotonic() - started)
            print(f"SRA stage: model download is active ({elapsed}s elapsed)", flush=True)

    monitor = threading.Thread(target=heartbeat, daemon=True)
    monitor.start()
    try:
        download_models(
            output_dir=output_dir, force=False, progress=False,
            with_layout=True, with_tableformer=True, with_rapidocr=True,
            rapidocr_models=["onnxruntime:ch"], with_code_formula=formula,
            with_picture_classifier=False,
        )
    finally:
        stop.set()
        monitor.join(timeout=1)
    print("SRA stage: model download finished", flush=True)


def verify(output_dir: Path, *, formula: bool) -> None:
    from tempfile import TemporaryDirectory
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, OcrMode, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption
    import os

    if os.getenv("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("Offline verification is not enabled")
    options = PdfPipelineOptions(artifacts_path=output_dir)
    options.do_ocr = True
    options.ocr_options = RapidOcrOptions(backend="onnxruntime", lang=["ch"], mode=OcrMode.FULL_PAGE)
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    if formula:
        options.do_formula_enrichment = True
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    print("SRA stage: offline Docling PDF/OCR verification", flush=True)
    with TemporaryDirectory(prefix="sra-model-check-") as folder:
        path = Path(folder) / "verification.pdf"
        path.write_bytes(_minimal_pdf())
        result = converter.convert(path)
        if len(getattr(result.document, "pages", {}) or {}) != 1:
            raise RuntimeError("Docling offline verification did not return the expected PDF page")
        status = str(result.status).lower()
        if "success" not in status:
            raise RuntimeError("Docling offline conversion did not report success")
    print("SRA stage: OFFLINE_VERIFIED", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("download", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--formula", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "download":
        download(args.directory, formula=args.formula)
    else:
        verify(args.directory, formula=args.formula)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
