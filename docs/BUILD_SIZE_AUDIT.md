# SRA Build Size Audit

> Windows x64 / macOS Apple Silicon · 完整前后端捆绑发布的体积审计

SRA 的发布流程会将 **Avalonia/.NET** 和 **PyInstaller 冻结的 Python 后端**打成完整安装包。`Docling`、`PyTorch`、`Transformers` 及其原生依赖可能占据主要空间。在决定删减任何依赖之前，先取得可复现的**真实打包输出数据**。

## 怎样查看报告（不需要下载或安装软件）

1. 将审计文件提交至 GitHub `main`（只会添加测试，不会因 push 自动启动昂贵的完整 Release）。
2. GitHub → **Actions** → **Release / Windows + macOS bundled Desktop** → **Run workflow**，选最新版 `main`、`version=0.2.0-alpha.1`、`backend_profile=full`。
3. 进入该次运行的 **Bundle win-x64** 或 **Bundle osx-arm64**：页面中的 **Step summary** 直接显示报告主要结果。
4. 下方 **Artifacts** 单独下载很小的 `SRA-build-size-audit-win-x64` 或 `SRA-build-size-audit-osx-arm64`。**不需要下载数百 MB 的安装包**。

每个平台提供 3 份文件：

- `build-size-win-x64.md` / `build-size-osx-arm64.md`：阅读报告、分类估计、最大 30 个文件；
- `.json`：精确字节数，方便记录或以后做版本对比；
- `-files.csv`：全部冻结后端与桌面发布目录中的文件大小，路径相对于对应目录，不包含本地绝对路径。

## 统计范围与解释

| 指标 | 扫描目录 | 注意 |
|---|---|---|
| 冻结 Python 后端 | `dist/backend/sra-backend` | PyInstaller 产物，包含解释器及运行依赖 |
| Avalonia/.NET | `dist/desktop` | self-contained 的发布文件 |
| 组装后 App | Windows `dist/SRA-Windows` / Mac `dist/SRA.app` | 已包含前面两个组件，**不能与它们相加** |
| 安装包体积 | `Setup.exe` / `.dmg` / portable `.zip` | 文件自身在 runner 上的字节数，不是 GitHub Artifact 二次压缩大小 |
| Python 类别 | 从相对路径推断 Torch/Docling/HF/OCR 等 | 每个文件仅归入一个类别；**不是依赖的精确归因** |

所有大小同时按 MiB（1 MiB = 1,048,576 B）显示。压缩安装包大小与解压后的有效负载大小不能混淆。原生库、共享库及 PyInstaller hook 生成的二进制可能落入 `Other backend files`。

## 在本机复用（有完整 build 输出时）

PowerShell 中的示例（须先存在 `dist/backend/sra-backend`、`dist/desktop`、`dist/SRA-Windows` 和安装包）：

```powershell
python scripts/report_build_sizes.py `
  --target win-x64 --profile full `
  --backend dist/backend/sra-backend `
  --desktop dist/desktop `
  --payload dist/SRA-Windows `
  --asset-glob 'dist/SRA-Desktop-Setup-*.exe' `
  --asset-glob 'dist/SRA-win-x64-*.zip' `
  --output-dir dist/build-size-audit
```

如果目录或安装包缺失，脚本**主动报错**，不会虚构一个体积报告。脚本不读取文件内容、不删除文件、不下载安装依赖。

## 以后怎么瘦身？

1. 按 `backend_categories_bytes` 比较后端各类实际占比，重点检查原生 `.dll`/`.dylib` 和 PyInstaller `--collect-all`。
2. 用 CSV 判断是否有重复收集的模型文件、数据包或运行库（仅文件名相同不能证明内容相同）。
3. 用可复现的 `full` build 改一次、测一次；跑实际的 PDF、Docling OCR、表格、Lite/Pro 等端到端流程。
4. **不要**因为体积大就擅自删除 PyTorch/Docling，或给 Avalonia 启用 trimming；这可能损坏解析模型和反射加载。

## 隐私与发布边界

- CI 在组装打包时仍运行 `check_release_safety.py`；体积报告并不会绕过这道门禁。
- 报告只统计构建产物，不扫描用户的 `.env`、原始 PDF、SQLite 数据库。
- 不把体积审计文件作为公开安装包附件：Release Job 的 `pattern: SRA-bundle-*` 不会匹配 `SRA-build-size-audit-*`。
- 如果 CI 失败在冻结后端或打包之前，**本次可能不会生成报告**；先修复对应构建错误。

## 对应提交

```text
ci(release): add Windows and macOS build size audit reports
```
