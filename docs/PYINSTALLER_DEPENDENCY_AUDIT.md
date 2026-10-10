# SRA Build Optimization v0.2 — PyInstaller Dependency Audit

这次以用户上传的 `sra-build-review.zip`（GitHub `origin/main` 快照）为基准修改打包脚本。**不修改论文解析器、模型逻辑、数据库或私人资料。**

## 体积基线：Windows full

来自此前 v0.2.0-alpha.3 的真实 Build Size Audit（已应用第一轮 Safe Pruning）：

| 指标 | 基线 |
|---|---:|
| 冻结后的 Python 后端 | 920.77 MiB |
| Avalonia/.NET | 101.16 MiB |
| 解压后组装目录 | 1,021.93 MiB |
| Windows Setup EXE | 281.93 MiB |
| 便携 ZIP | 397.66 MiB |

体积分组是路径估算，不代表移除某一依赖就能省下对应全部空间。本轮保留 PyTorch、Docling、Transformers、OCR 等关键功能。

## 1. 两种依赖收集策略

GitHub Release 手动运行新增 `collection_policy`：

| 策略 | 作用 |
|---|---|
| `conservative`（默认） | 保留 Docling、Torch、Transformers、SentencePiece、PyMuPDF、RapidOCR 和 Keyring 的必要资源；仅收窄 `fitz`、`pydantic`、`huggingface_hub`、`accelerate` 的宽泛收集规则；Windows 额外排除经检查的 OpenCV 视频 FFmpeg 插件 |
| `compatible` | 回退到此前宽泛的 `--collect-all` 规则，并保留 OpenCV 视频插件（仍执行第一轮 `.pdb` / PyMuPDF 开发文件清理） |

这意味着你可以在 **同一个 Git 提交**上重新打出兼容版进行比较，而不必回滚论文分析代码。

### Windows 视频组件

此前报告显示 `_internal/cv2/opencv_videoio_ffmpeg500_64.dll` 占 **29.45 MiB（解压后）**。SRA 的主流程需要 PDF 图像和 OCR，并不处理视频。新脚本只在 `conservative + win-x64` 时，允许移除 `_internal/cv2/opencv_videoio_ffmpeg<数字>_64.dll` 这种精确路径的 OpenCV 视频插件，保留 `cv2.pyd`、Torch/Docling/OCR 的全部运行二进制。

**最终安装包能省多少 MB，必须由下一次 Release 的 Size Audit 测量；不会以 29.45 MiB 直接估算压缩后的收益。**

## 2. 冻结后端自检

GitHub Actions 会在**打包前、清理后**运行已经由 PyInstaller 冻结的 `sra-backend`，使用 `--self-test-bundle`：

1. 在内存里生成测试 PDF，用 PyMuPDF 重新打开并验证文本。
2. 把测试 PDF 渲染成 PNG，使用 OpenCV `imdecode` / `cvtColor` 检查图像链路（full 模式）。
3. 导入 Docling `DocumentConverter/PdfPipelineOptions`、Transformers `AutoTokenizer`、Hugging Face Hub，执行 PyTorch CPU 张量测试（full 模式）。
4. 继续运行原有 API 启动与临时会话令牌鉴权冒烟测试。

测试不会读取用户论文库、调用外部 LLM 或下载模型权重。

**重要限制：** 冻结自检只覆盖 PDF 回退解析、图像链路和库导入，不等同于带权重的 Docling 版式推理、扫描 OCR 或表格恢复已经通过。发布前仍应在 Windows 和 Apple Silicon 真机测试代表性的中文/英文、扫描版、多栏、表格 PDF。如果出现问题，使用 `compatible` 重建，并保留缺陷报告。

## 3. 本地开发与 GitHub Actions

Windows 本地快速测试（不需要重新安装 SRA）：

```powershell
conda run -p .conda-env python -m pytest -q tests\test_dependency_collection.py tests\test_release_pruning.py tests\test_build_size_audit.py
```

可选，本机完整冻结（需要安装 PyInstaller + Docling，耗时较长）：

```powershell
conda run -p .conda-env python scripts\build_backend.py --profile full --collection-policy conservative --output dist\backend
.\dist\backend\sra-backend\sra-backend.exe --self-test-bundle --profile full
```

GitHub Actions → **Release / Windows + macOS bundled Desktop** → Run workflow，选择 `backend_profile=full`、`collection_policy=conservative`，运行成功后读取 Windows/Mac 的 `SRA-build-size-audit-*`。若需要可直接再运行一次 `compatible` 进行对比。

`conservative` 仍属 Alpha 的保守试验配置，不能把 CI 绿灯解读为全部论文解析场景都已验证。
