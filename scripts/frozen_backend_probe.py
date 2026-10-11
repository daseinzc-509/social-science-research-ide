"""Offline PDF + image + model-import checks inside the *frozen* backend.

Never reads a user's papers, environment secrets, or downloads model weights.
This confirms basic PDF/image libraries AND the Hugging Face image processor
used by Docling's Heron layout model. Does not download model weights.
Real Docling OCR/layout inference remains a separate acceptance gate.
"""
from __future__ import annotations

import argparse
import io
import os


def _synthetic_pdf() -> bytes:
    """Generate a tiny one-page PDF with no external PDF-writing package."""
    content = b"BT /F1 11 Tf 30 170 Td (SRA frozen backend PDF smoke check) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 220] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode("ascii") + b"\n%%EOF\n"
    )
    return bytes(pdf)


def pdf_roundtrip() -> bytes:
    """Test PDF parsing and rasterization through Docling's PDFium dependency."""
    from io import BytesIO
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(_synthetic_pdf()) as document:
        if len(document) != 1:
            raise AssertionError("PDFium failed to open the synthetic PDF")
        page = document[0]
        text_page = page.get_textpage()
        try:
            if "SRA frozen backend PDF smoke check" not in text_page.get_text_range():
                raise AssertionError("PDFium failed to extract synthetic PDF text")
        finally:
            text_page.close()
        image = page.render(scale=1).to_pil()
        output = BytesIO()
        image.save(output, format="PNG")
        page.close()
        png = output.getvalue()
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("PDFium failed to rasterize the synthetic PDF")
    return png


def image_decode_roundtrip(png_bytes: bytes, *, required: bool) -> None:
    import importlib.util

    if not required and importlib.util.find_spec("cv2") is None:
        print("cv2 not installed in core profile; image probe skipped")
        return
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] < 1 or image.shape[1] < 1:
        raise AssertionError("OpenCV failed to decode a rasterized PDF page")
    if cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).size == 0:
        raise AssertionError("OpenCV failed on an image conversion operation")


# This mirrors the PUBLIC 444-byte preprocessor_config.json for
# docling-project/docling-layout-heron (RT-DETR v2). Using a temporary local
# directory avoids network calls and tests the exact dynamic import path that
# previously failed only after installation on Windows.
HERON_IMAGE_PROCESSOR_CONFIG = {
    "image_processor_type": "RTDetrImageProcessor",
    "do_convert_annotations": True,
    "do_normalize": False,
    "do_pad": False,
    "do_rescale": True,
    "do_resize": True,
    "format": "coco_detection",
    "image_mean": [0.485, 0.456, 0.406],
    "image_std": [0.229, 0.224, 0.225],
    "pad_size": None,
    "resample": 2,
    "rescale_factor": 0.00392156862745098,
    "size": {"height": 640, "width": 640},
}


def exercise_heron_image_processor() -> None:
    """Verify Transformers' lazy vision imports in the *frozen* executable.

    A simple `import transformers` or `AutoTokenizer` import misses this.
    This also exercises the selected processor on a synthetic image, not
    merely finding its class name.
    """
    import json
    import tempfile
    from pathlib import Path
    from PIL import Image

    try:
        from transformers import AutoImageProcessor
        with tempfile.TemporaryDirectory(prefix="sra-heron-processor-check-") as folder:
            path = Path(folder)
            (path / "preprocessor_config.json").write_text(
                json.dumps(HERON_IMAGE_PROCESSOR_CONFIG), encoding="utf-8"
            )
            processor = AutoImageProcessor.from_pretrained(str(path), local_files_only=True)
            result = processor(images=Image.new("RGB", (32, 32), "white"), return_tensors="pt")
            if "pixel_values" not in result or result["pixel_values"].shape[0] != 1:
                raise AssertionError("Heron image processor did not produce a pixel batch")
            print(f"Heron image processor OK: {type(processor).__name__}", flush=True)
    except Exception as exc:
        import traceback
        # Lazy Transformers import hides the real exception behind generic
        # 'Could not import module AutoImageProcessor'. Show the entire chain
        # in Actions logs so packaging failures are actionable.
        print("Frozen Heron image processor verification FAILED", flush=True)
        traceback.print_exc()
        raise RuntimeError(
            "Frozen backend cannot load Docling Heron's RT-DETR image processor. "
            "Inspect the preceding Python traceback for missing torchvision, "
            "Transformers or native-library dependencies."
        ) from exc


def verify_rapidocr_model_manifest() -> None:
    """Verify OCR config inside the *actual frozen backend* before release.

    RapidOCR resolves this file next to rapidocr/__init__.py. A successful
    `import rapidocr` alone is not enough: PyInstaller may freeze Python
    modules while omitting the YAML data file required to initialize OCR.
    Does not load models, fetch weights, or process a user's PDF.
    """
    from pathlib import Path

    try:
        import rapidocr
    except Exception as exc:
        raise RuntimeError(
            "Frozen backend cannot import rapidocr; check the "
            "PyInstaller --collect-data rapidocr rule."
        ) from exc

    location = getattr(rapidocr, "__file__", None)
    if not location:
        raise RuntimeError(
            "Frozen backend cannot resolve rapidocr package location."
        )
    manifest = Path(location).resolve().parent / "default_models.yaml"
    if not manifest.is_file():
        raise RuntimeError(
            f"Frozen backend is missing RapidOCR default_models.yaml at {manifest}. "
            "Build with --collect-data rapidocr and verify the final _internal/rapidocr/ "
            "directory before publishing the installer."
        )
    if manifest.stat().st_size == 0:
        raise RuntimeError(f"Frozen RapidOCR default_models.yaml is empty: {manifest}")
    print("RapidOCR configuration manifest OK (offline)", flush=True)


def full_profile_imports() -> None:
    # The bundle probe must never fetch Hugging Face model weights.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    # This missing YAML was the cause of single and batch PDF import failures
    # in installed SRA. Check it before expensive Torch/Docling imports.
    verify_rapidocr_model_manifest()
    import torch
    from docling.document_converter import DocumentConverter
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from huggingface_hub import hf_hub_download
    from transformers import AutoTokenizer, AutoModelForObjectDetection

    if int(torch.tensor([2, 3]).sum().item()) != 5:
        raise AssertionError("PyTorch CPU tensor calculation failed")
    for obj in (DocumentConverter, PdfPipelineOptions, hf_hub_download, AutoTokenizer, AutoModelForObjectDetection):
        if obj is None:
            raise AssertionError("A required Docling/Transformers API was not importable")
    exercise_heron_image_processor()
    # Layout model weights and real OCR are intentionally not downloaded or
    # invoked here. A separate optional real-PDF acceptance test is required.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("full",), required=True)
    opts = parser.parse_args(argv)
    image = pdf_roundtrip()
    image_decode_roundtrip(image, required=True)
    full_profile_imports()
    print(f"Frozen backend dependency/PDF/image probe PASSED ({opts.profile}; offline)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
