# SRA Build Optimization v0.3 — opt-in Lean packaging

## Baseline and motivation

Windows `full` / `conservative` build after v0.2:

| Metric | Baseline |
| --- | ---: |
| Windows setup EXE | 271.44 MiB |
| Portable Windows ZIP | 383.66 MiB |
| Frozen Python backend (uncompressed) | 885.06 MiB |
| Complete payload (uncompressed) | 986.22 MiB |

These are user-supplied build measurements, **not** targets or predictions for
`lean`. Most backend bytes are in Torch, OpenCV, NumPy/SciPy and Docling
runtime dependencies. We do **not** remove them blindly.

## Collection policies

| Policy | Purpose | Transformers | Optional developer modules | `.pyi` files |
| --- | --- | --- | --- | --- |
| `compatible` | Previous broad collection (rollback) | `--collect-all` | Unchanged | Kept |
| `conservative` | Existing release default | `--collect-all` | Unchanged | Kept |
| `lean` | Experimental measured candidate | `--collect-submodules` + `--collect-data` | Excluded (list below) | Pruned under `_internal` |

Lean excludes only the optional GUI/interactive/test tooling `IPython`,
`jupyter`, `jupyterlab`, `notebook`, `pytest`, `sphinx`, `mkdocs`, `tkinter`,
and `_tkinter`. These exclusions **do not modify developer installations**;
they apply solely to the PyInstaller frozen application. They must be validated
against SRA's actual document pipeline before considering production use.

The lean policy retains `--collect-all` for Docling/Docling Parse/IBM models,
PyMuPDF, SentencePiece and the required other runtime libraries. Torch,
OpenCV's image decoder, OCR, models and arbitrary dynamic libraries are never
removed by the new policy. Windows still removes only the separately tested
optional OpenCV FFmpeg video plugin under `conservative` / `lean`.

`--remove-type-stubs` is an explicit opt-in: it removes only `.pyi` files
under the frozen backend's `_internal` tree. Runtime `.py`, `.pyc`, `.pyd`,
`.dll`, `.so`, `.dylib`, `py.typed`, shared libraries and data remain.

## Mandatory gates

The Release workflow keeps the existing privacy scanner and runs all of these
checks **after pruning and before building installers**:

1. Generate PDF in the frozen backend with PyMuPDF, extract text, rasterize.
2. Decode the raster image and perform grayscale conversion via OpenCV.
3. For `full` profile, import Docling, Torch, Hugging Face and Transformers.
4. Load a built-in BERT config from Transformers; perform a native `tokenizers`
   encoding; serialize/deserialize a tiny tensor using `safetensors`.
5. Execute Torch CPU tensor arithmetic.
6. Start the **frozen** API server and test authenticated/unauthenticated
   localhost requests with isolated scratch user data.
7. Check release content for bundled secrets/PDF/SQLite, preserve install and
   uninstall checks, and generate existing Build Size Audit reports.

All probes are local and do not download model weights, use model API keys or
read private research libraries. They detect missing frozen modules and basic
PDF/image regressions but **do not prove** actual Docling layout/OCR inference.
Before shipping a `lean` build to others, test a real Chinese PDF, multi-column
PDF, scanned OCR and tables in an installed preview build.

## How to try without changing the default

GitHub → Actions → **Release / Windows + macOS bundled Desktop** →
**Run workflow**, select:

```text
Version: 0.2.0-alpha.5 (or another unused alpha version)
Backend profile: full
Collection policy: lean
```

Use the `SRA-build-size-audit-win-x64` summary to compare Setup EXE against
**271.44 MiB**. Also check `osx-arm64` results and build/runtime tests.

For fallback, run the same workflow with `collection_policy=conservative`, or
`compatible` when necessary. Tag releases intentionally default to
`conservative` — choosing `lean` does not silently change official releases.

## Safety/maintenance boundaries

- Do not delete `torch_cpu.dll`, `cv2.pyd`, model assets or arbitrary DLLs.
- Do not enable .NET trimming without proving Avalonia XAML/reflection
  compatibility.
- Do not promise a specific reduction: type stubs and optional module archives
  may already be absent, and compressed installer savings differ from disk
  savings.
- Do not release a `lean` build as stable based only on import probes.
- The workflow and scripts are based on the previously provided
  `sra-build-review.zip` + v0.2 dependency changes + macOS Bash hotfix. If
  newer workflow files were edited since then, compare before overwriting.
