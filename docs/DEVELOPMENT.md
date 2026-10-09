# Development Guide / 源码开发指南

> SRA Alpha · Windows x64 / macOS Apple Silicon · 最后更新：2026-10
>
> 本文说明如何**从源码反复开发和调试**。不需要安装 GitHub Release 的 EXE/DMG，也不需要每次修改后都运行 PyInstaller。

## 1. 开发模式与发布模式

| | 开发模式（本地） | 发布模式（CI / Release） |
| --- | --- | --- |
| 桌面端 | `dotnet run` 直接运行 Avalonia 源码 | `dotnet publish` 后封装到安装包 |
| Python 后端 | Conda 环境中运行 `sra api` | PyInstaller 冻结的 `sra-backend` |
| 后端启动 | **手动在独立终端启动**，默认 `127.0.0.1:8766` | 桌面端自动启动私有后端，随机端口和会话令牌 |
| 修改代码 | 改完直接重启受影响进程 | 重新构建、打包、安装验证 |
| 数据 | 推荐使用独立的开发数据目录 | 使用本机用户数据目录 |

**重要**：没有捆绑后端的桌面源码版会连接 `http://127.0.0.1:8766/`。若把冻结的 `sra-backend.exe`（macOS 为 `sra-backend`）放进桌面构建输出下的 `backend/sra-backend/`，桌面会优先启动它，导致你对 Python 源码的修改看似没有生效。开发时请不要把发布文件复制到 `bin/Debug/...` 下。

## 2. 环境准备

- Windows 10/11 x64，或 macOS Apple Silicon（开发分支；macOS Intel 暂不维护）。
- Python **3.12** 和 Conda（推荐 Miniforge）。
- .NET **10 SDK**（项目目标框架为 `net8.0`；当前 Avalonia 版本用 .NET 10 SDK 构建更稳妥）。
- Git；安装必要的 Python 包时需要网络。
- 有效的用户自有模型 API Key 仅在进行真正的 Lite/Pro 分析时需要。界面与大部分测试可以不调用真实模型。

检查工具：

```powershell
# Windows PowerShell
conda --version
conda run -p .conda-env python --version  # 需先完成下一节的环境创建
dotnet --info
git --version
```

## 3. 首次从源码启动：Windows

在仓库根目录（包含 `pyproject.toml`、`src/` 和 `desktop/`）执行。**环境安装只做一次**，平时改代码不用重新安装。

```powershell
cd 'D:\Social Science Research IDE'

# 首次创建 Python 环境
conda create -p .conda-env --override-channels -c conda-forge python=3.12 pip 'pydantic>=2.7,<3' 'pymupdf>=1.24,<2'
conda run -p .conda-env python -m pip install --no-build-isolation -e .
conda run -p .conda-env python -m pip install 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1' 'python-multipart>=0.0.9,<1' 'httpx>=0.27,<1' 'docling>=2.131,<3'

# 首次恢复 C# 依赖
 dotnet restore .\desktop\SRA.Desktop\SRA.Desktop.csproj
```

如果 `.conda-env` 已经存在，**跳过** `conda create`，不要覆盖当前可用环境。首次需要配置模型时，复制 `.env.example` 为被 Git 忽略的 `.env`，或使用程序设置页；不要提交真实 API Key。

### 终端 A：启动 Python API

推荐先隔离测试数据库（此路径在项目外，不会被 Git 上传）：

```powershell
cd 'D:\Social Science Research IDE'
$env:SRA_DATA_DIR = Join-Path $env:LOCALAPPDATA 'SRA-Dev\data'
conda run --no-capture-output -p .conda-env sra api
```

预期本地地址为 `http://127.0.0.1:8766`。在另一个终端可检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/v1/health
```

**注意**：开发 API 使用旧的无桌面令牌兼容模式，仅供本机使用。切勿绑定 `0.0.0.0` 或将端口转发到公网/局域网。

### 终端 B：启动 Avalonia Desktop

```powershell
cd 'D:\Social Science Research IDE'
dotnet run --project .\desktop\SRA.Desktop\SRA.Desktop.csproj
```

窗口打开后就可以边运行边观察日志。**不要为每次界面改动重新下载 EXE。**

也可以用 Visual Studio / Rider 打开 `desktop/SRA.Desktop/SRA.Desktop.csproj`，设置该项目为启动项目，用断点调试 C#。

## 4. 日常修改 → 验证 → 再修改

| 你修改的文件 | 操作 | 需要重新安装依赖吗？ |
| --- | --- | --- |
| `desktop/SRA.Desktop/Views/*.axaml` 或 `Styles/*.axaml` | 关闭桌面窗口，重新 `dotnet run` | 否 |
| `desktop/SRA.Desktop/ViewModels/*.cs` / `Services/*.cs` | 重新运行桌面端，必要时用 IDE 断点 | 否 |
| `src/sociology_research/api/`、`services/`、`analyzer.py` 等 Python 文件 | **Ctrl+C 停止终端 A 的 API，重新运行 `sra api`**；通常无需重启桌面 | 否，`pip install -e .` 使用可编辑安装 |
| `tests/*.py` | 直接运行对应的 pytest 测试 | 否 |
| `pyproject.toml` 或依赖版本 | 按变动重新 `pip install -e .` / 安装依赖 | 可能需要 |
| `installer/`、`.github/workflows/` | 本地验证脚本并推送 CI，检查真实安装包 | 需要重新构建发布产物 |

可选快速迭代命令（Avalonia 的 XAML 变更不保证全部可以无重启热更新，必要时按上面可靠方式重启）：

```powershell
dotnet watch --project .\desktop\SRA.Desktop\SRA.Desktop.csproj run
```

**示例：调整左侧论文选中颜色**：修改 `desktop/SRA.Desktop/Styles/LibraryStyles.axaml` → 保存 → 关闭当前桌面窗口 → 在终端 B 再次运行 `dotnet run` → 检查颜色。整个过程无需动 Python、GitHub Actions 或安装包。

**示例：修复论文元数据**：修改 `src/sociology_research/analyzer.py` → 在终端 A 重新启动 `sra api` → 回到桌面端重试（如果界面持有已缓存的数据，可手动刷新）。不要为这种修复重打 EXE。

## 5. 源码开发：macOS Apple Silicon

在 Mac 上安装 Miniforge/Conda、Python 3.12 和 .NET 10 SDK。然后于项目根目录创建相同的 Conda 环境，并执行与 Windows 对应的 Python 安装命令。

终端 A：

```bash
export SRA_DATA_DIR="$HOME/Library/Application Support/SRA-Dev/data"
conda run --no-capture-output -p .conda-env sra api
```

终端 B：

```bash
dotnet run --project desktop/SRA.Desktop/SRA.Desktop.csproj
```

开发模式同样需要两个进程。不要把 macOS `.app` 安装包里的冻结后端当作日常源码调试入口。Mac 原生环境、依赖及签名问题以 CI 和真机结果为准。

## 6. 运行测试

```powershell
# 回归测试（不应调用真实 Lite/Pro 模型）
conda run -p .conda-env python -m pytest -q tests

# 只测 API / 研究引擎相关子集
conda run -p .conda-env python -m pytest -q tests\test_api_phase1.py tests\test_api_phase2.py tests\test_api_ui_parity.py

# 检查桌面编译
dotnet build .\desktop\SRA.Desktop\SRA.Desktop.csproj

# Git 提交前检查
git diff --check
git status --short
```

如果修改涉及 PDF 解析、模型调用或精读报告，请增加针对对应行为的自动化回归测试。尽量使用模拟模型响应，避免本地/CI 测试耗费真实模型额度。

## 7. 何时才需要真正安装软件？

只有当你要验证这些问题时才需要构建安装器：

- `Setup.exe`、`.app` / `.dmg` 能否正常安装和首次启动；
- 冻结 Python 后端是否能自动启动，包含哪些 Docling/OCR 依赖；
- 升级与卸载会不会损坏用户数据；
- 系统权限、签名、macOS Gatekeeper、公证等发行问题。

日常 UI、API 或模型输出 bug 一律优先使用**源码运行 + 本地测试**。普通 push 上的 CI 侧重测试和编译；专门的 Release 工作流才负责冻结后端与制作安装器。最终打包成功与否，以对应平台的 GitHub Actions 和实机安装测试为准。

## 8. 常见问题

**桌面打开了，但论文列表为空：** 检查终端 A 的 API 是否已启动、桌面是否连接 `127.0.0.1:8766`；确认 `SRA_DATA_DIR` 指向你打算使用的测试库。源码开发库与安装版的个人库默认可能不同。

**修改 Python 代码后没有变化：** 重新启动 API；确认 `pip install -e .` 已安装当前仓库，并确认桌面 `bin/Debug/...` 下没有意外复制的 `backend/sra-backend/` 冻结程序。

**Windows 编译或 XAML 报错：** 先 `dotnet --info`，然后 `dotnet build desktop/SRA.Desktop/SRA.Desktop.csproj`。如果是资源编译问题，再按需清理 `bin/`、`obj/`，不要每次都删除。

**模型请求失败：** 检查设置中的 Lite/Pro 连接、API Key、限额和日志；修改 UI 不能解决模型服务本身的问题。真实分析可能产生计费。

**个人 PDF / API Key 是否会提交：** `.env`、`data/` 与个人 PDF 必须保持在 Git 之外。发布前仍应执行隐私扫描；绝不把开发目录整体压缩给其他用户。

相关文档：[架构](ARCHITECTURE.md) · [安全分发](SECURE_DISTRIBUTION.md) · [Windows/macOS 发布](CROSS_PLATFORM_DISTRIBUTION.md)。
