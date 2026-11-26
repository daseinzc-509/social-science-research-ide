"""独立托管引擎子进程 PDF 解析入口。

在瘦壳（slim）模式下，主 API 进程不内嵌 torch/docling，而是通过受控子进程在
`user_root()/engine/docling/venv` 中执行本模块，100% 复用 `DoclingDocumentParser`
的双遍 OCR、中文乱码质检、页脚印刷页码、表格跨行跨列合并、公式与图表区域定位逻辑。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .parser import DoclingDocumentParser, DocumentParseError
from .user_storage import _atomic_bytes


def run_parse(
    pdf_path: Path,
    *,
    paper_id: str,
    sha256: str,
    output_path: Path,
) -> int:
    error_path = output_path.with_suffix(".error.json")

    def on_progress(message: str) -> None:
        safe = str(message or "").replace("\r", " ").replace("\n", " ").strip()[:240]
        if safe:
            print(f"SRA stage: {safe}", flush=True)

    try:
        parser = DoclingDocumentParser()
        parsed = parser.parse(
            pdf_path,
            paper_id=paper_id,
            sha256=sha256,
            progress=on_progress,
        )
        _atomic_bytes(output_path, parsed.model_dump_json().encode("utf-8"))
        return 0
    except DocumentParseError as exc:
        payload = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
        _atomic_bytes(error_path, payload)
        return 2
    except Exception as exc:  # pragma: no cover - 防御性兜底
        payload = json.dumps(
            {"error": f"Docling worker failed ({type(exc).__name__}): {exc}"},
            ensure_ascii=False,
        ).encode("utf-8")
        _atomic_bytes(error_path, payload)
        return 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SRA isolated Docling PDF parse worker")
    ap.add_argument("pdf_path", type=Path)
    ap.add_argument("paper_id")
    ap.add_argument("sha256")
    ap.add_argument("output_json", type=Path)
    args = ap.parse_args(argv)
    return run_parse(
        args.pdf_path,
        paper_id=args.paper_id,
        sha256=args.sha256,
        output_path=args.output_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
