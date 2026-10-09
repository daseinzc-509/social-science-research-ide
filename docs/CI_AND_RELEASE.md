# SRA Desktop 顶部细节修复 + GitHub Actions CI / Release

## 本次修改
- `desktop/SRA.Desktop/Views/MainWindow.axaml`：移除顶部“本地 API”指示标签；最小化按钮由易塌缩的 Path 改为 15 × 2 的主题文字色短横线。功能依旧保留，后台 API 不受影响。
- `.github/workflows/ci.yml`：每次任意分支 push 或 PR 自动跑 Python API smoke tests、编译 Avalonia，并上传 `win-x64` 桌面 ZIP。
- `.github/workflows/release-desktop.yml`：推送 `v*` tag 先测试、再构建 Windows self-contained Desktop ZIP 并创建 GitHub **预发布**（不是完整安装器）；可通过 Actions 页手动运行只生成 artifact。
- `tests/test_desktop_chrome.py`：防止顶部 API 标签和不可见最小化图标回归。

## 安装步骤
解压 ZIP 到你的项目根目录 `D:\Social Science Research IDE`，合并 `desktop`、`.github`、`tests`、`docs` 目录。

**不要**将 ZIP 里的单个 workflow 文件放到项目根目录：必须在 `.github/workflows/` 下。

本地验证：
```powershell
dotnet build .\desktop\SRA.Desktop\SRA.Desktop.csproj
conda run -p .conda-env python -m pytest tests\test_desktop_chrome.py -q
```

提交：
```powershell
git add desktop/SRA.Desktop/Views/MainWindow.axaml .github/workflows/ci.yml .github/workflows/release-desktop.yml tests/test_desktop_chrome.py docs/CI_AND_RELEASE.md
git commit -m "fix(desktop): correct titlebar controls and add CI release workflows"
git push
```

确认 GitHub Actions → `CI / Desktop Preview` 全部绿色，再打 tag：
```powershell
git tag -a v0.1.1 -m "SRA Desktop v0.1.1"
git push origin v0.1.1
```

## CI 运行边界
- Python smoke tests：`test_api_phase1.py`、`test_api_phase2.py`、`test_api_ui_parity.py`、`test_desktop_chrome.py`。仓库需要有前三个文件；如果没有，请从你以前的 Phase1/Phase2/UI Parity 提交中一并补齐，不要隐藏失败。
- Windows 使用 .NET 10 SDK 编译 net8.0；否则 Avalonia 12 的 Roslyn generator 可能不兼容旧 SDK。
- 不需要 `SRA_LITE_API_KEY` / `SRA_PRO_API_KEY`，因为当前 API 回归使用 fake pipeline。
- 构建结果可以从 GitHub Actions 的 Artifacts 下载。
- Release ZIP **只包含 Avalonia 前端和 .NET runtime**。Python、Docling、OCR 模型及本地数据不会被打包，打开前仍须运行 `sra api`。下一阶段可实现后端随桌面自动启动及完整安装器。
- 现在没有在本环境实际执行 Windows `dotnet build`。请以 GitHub Actions 和你的 Windows 构建输出为准。
