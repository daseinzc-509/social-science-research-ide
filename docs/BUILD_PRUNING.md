# SRA Build Optimization v0.1 — Safe Pruning

> Release-only 清理 / Windows x64 + macOS Apple Silicon / 不改变 Python 研究引擎

## 优化边界

本轮只移除已经编译、且不参与软件运行的发布冗余：

| 目标 | 移除规则 | 为什么可以先试 |
|---|---|---|
| Avalonia/.NET 自包含发布目录 `dist/desktop` | `*.pdb` | Windows/.NET 调试符号，不是执行程序所需组件 |
| PyInstaller 冻结后端 `dist/backend/sra-backend` | **仅** `pymupdf/mupdf-devel/**` 下的 `.lib` / `.a` / `.h` / `.hpp` / `.hxx` | 编译与链接用开发文件，不是 PyMuPDF 运行库 |

**明确不动**：`torch_cpu.dll`、PyTorch、Docling、OpenCV、FFmpeg、MuPDF `.dll` / `.pyd` / `.so` / `.dylib`、OCR/表格模型、Lite/Pro 分析逻辑、Python 包安装配置、`.NET PublishTrimmed` 和用户数据。

如果未来真的需要可分发的调试符号，可以另外建立私有 symbol Artifact。本轮不建议把百兆级 PDB 混在安装包中。对于性能诊断/崩溃分析需求，仓库及构建产物保留原始源码，Release 的符号需要另外归档。

## 当前基准（2026-10-09 Windows win-x64）

这是用户提供的 **优化前** `Build Size Audit`，不是优化结果：

| 指标 | 基准 |
|---|---:|
| 解压后后端 | 927.15 MiB |
| 解压后 Avalonia + .NET | 201.35 MiB |
| 组装后程序 | 1,128.50 MiB |
| Setup.exe | 298.41 MiB |
| Portable ZIP | 423.93 MiB |
| `libSkiaSharp.pdb` | 80.14 MiB |
| `libHarfBuzzSharp.pdb` | 19.95 MiB |
| PyMuPDF `mupdfcpp64.lib` | 3.85 MiB |

三项典型发布冗余共约 **103.94 MiB 解压体积**，不代表安装器必定减小 103.94 MiB：LZMA/ZIP 压缩率不同。重新构建后以本次 `Build Size Audit` 为准。其它符合白名单的文件也可能被移除，实际列表见 pruning JSON 报告。

## GitHub Actions 中的顺序

```text
Freeze Python backend
    ↓
Publish Avalonia self-contained desktop
    ↓
Safe pruning (--apply) + exact-byte report
    ↓
Frozen backend smoke (health 200, unauthorized 401)
    ↓
Assemble Windows / macOS payload
    ↓
Privacy safety gate
    ↓
Setup / ZIP / DMG and final build size audit
```

完整的文件名单和实际删减体积会写入：

- `dist/build-size-audit/prune-report-win-x64.md/.json`
- `dist/build-size-audit/prune-report-osx-arm64.md/.json`

Actions → 对应 Bundle Job → **Step summary** 直接看到删除统计；`SRA-build-size-audit-*` Artifact 提供 JSON/Markdown 和总体体积报表。

安全机制：白名单；脚本默认**预览**；显式 `--apply` 才删除；要求两个输入为不同的冻结发布目录，并确认目录有预期可执行文件；跳过 symlink/junction；不会递归删除目录或碰原始 PDF/SQLite/API Key；删除后再做启动和鉴权冒烟测试。

## 本地预览（可选）

只有当 `dist/backend/sra-backend` 和 `dist/desktop` 已经实际构建时才能运行。平时从源码运行 SRA **不需要执行清理脚本**。

```powershell
python scripts/prune_release_assets.py `
  --target win-x64 `
  --backend dist/backend/sra-backend `
  --desktop dist/desktop `
  --output-dir dist/build-size-audit
```

预览结果不修改文件。如果你在本地进行发布测试并明确希望删掉上述发布文件，可在命令末尾加入 `--apply`；务必确认两个输入路径确实是 `dist` 里的**可重新生成的发布目录**。

## 如何验收

```powershell
conda run -p .conda-env python -m pytest -q tests\test_release_pruning.py tests\test_build_size_audit.py
```

提交之后，在 GitHub Actions 手动运行 `Release / Windows + macOS bundled Desktop`，选择 `full`。同一平台对比 `prune-report`、`build-size` 和最终 Setup/DMG 文件大小，并下载安装包做一次真实 PDF（特别是 OCR、表格、多栏）的端到端解析验收。

**验收标准**：Windows/Mac Release 构建成功、隐私门禁通过、冻结后端冒烟测试通过；设备上的 SRA 可启动，真实 PDF 的 Docling/OCR/表格解析和证据溯源正常。只有这些都通过，才接受瘦身改动。

## 对应 Git 提交

```text
perf(build): prune release debug symbols and PyMuPDF development files
```
