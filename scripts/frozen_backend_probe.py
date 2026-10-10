"""Offline PDF + image + model-import checks inside the *frozen* backend.

Never reads a user's papers, environment secrets, or downloads model weights.
This confirms the packaging of basic PDF/image libraries and Docling's Python
import graph; an actual Docling OCR/layout inference test remains a separate
acceptance gate and should not be inferred from this probe.
"""
from __future__ import annotations

import argparse
import io
import os


def pdf_roundtrip() -> bytes:
    import pymupdf

    label = "SRA frozen backend PDF smoke check"
    document = pymupdf.open()
    page = document.new_page(width=320, height=220)
    page.insert_text((30, 50), label, fontsize=11)
    document_bytes = document.tobytes()
    document.close()
    with pymupdf.open(stream=document_bytes, filetype="pdf") as readback:
        actual = readback[0].get_text()
        if label not in actual:
            raise AssertionError("PyMuPDF failed to extract the self-generated PDF text")
        png_bytes = readback[0].get_pixmap(matrix=pymupdf.Matrix(1, 1)).tobytes("png")
    return png_bytes


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


def full_profile_imports() -> None:
    # The probe must not trigger an implicit Hugging Face model download.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from docling.document_converter import DocumentConverter
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from huggingface_hub import hf_hub_download
    from transformers import AutoConfig, AutoTokenizer
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from safetensors.torch import load as load_tensor, save as save_tensor

    # These native / dynamically loaded packages live outside Transformers'
    # own folder and MUST still function under the lean collection policy.
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "sra": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    if tokenizer.encode("sra").ids != [1]:
        raise AssertionError("The frozen tokenizers native extension is unusable")
    if AutoConfig.for_model("bert").model_type != "bert":
        raise AssertionError("The Transformers configuration registry is incomplete")
    encoded_tensor = save_tensor({"numbers": torch.tensor([2, 3])})
    if load_tensor(encoded_tensor)["numbers"].sum().item() != 5:
        raise AssertionError("The safetensors native extension is unusable")

    if int(torch.tensor([2, 3]).sum().item()) != 5:
        raise AssertionError("PyTorch CPU tensor calculation failed")
    for obj in (DocumentConverter, PdfPipelineOptions, hf_hub_download, AutoTokenizer):
        if obj is None:
            raise AssertionError("A required Docling/Transformers API was not importable")
    # No Docling model instantiation or inference here: not all models ship
    # inside the installer, and full layout/OCR acceptance needs a real PDF.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("core", "full"), required=True)
    opts = parser.parse_args(argv)
    image = pdf_roundtrip()
    image_decode_roundtrip(image, required=opts.profile == "full")
    if opts.profile == "full":
        full_profile_imports()
    print(f"Frozen backend dependency/PDF/image probe PASSED ({opts.profile}; offline)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
