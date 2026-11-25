# SRA Desktop — 安装、更新、卸载与隐私边界

自 `v0.2` 系列（含当前正式版 **`v0.2.2`**）起，Windows 与 macOS 发布包均为**一体化捆绑安装包**：内含 Avalonia / .NET 8 桌面端与 PyInstaller 冻结后的 Python / FastAPI / Docling 研究后端，启动桌面端即自动拉起本地私有后端，无需手动运行 `sra api`。

## 安装

### Windows x64
GitHub Actions 在原生 `windows-latest` 上完成后端冻结、前端自包含发布、离线自检与安全扫描，提供两类产物：
- `SRA-Desktop-Setup-win-x64-<version>.exe`（推荐）：单用户 Inno Setup 安装器，默认安装到 `%LOCALAPPDATA%\Programs\SRA Desktop`，不需要管理员权限，添加开始菜单与可选桌面快捷方式。
- `SRA-win-x64-v<version>.zip`：免安装便携包，解压即可运行 `SRA.Desktop.exe`。
- `SHA256SUMS-win-x64.txt`：安装产物的 SHA-256 校验值。

> 安装器使用稳定 `AppId`（`org.daseinzc.socialscienceresearchide.desktop`），升级时直接覆盖程序目录，绝不触碰你的研究数据目录。桌面端退出时通过 Windows Job Object 与父进程看门狗自动终止后端进程，不留孤儿进程。

### macOS Apple Silicon（ARM64，macOS 14+）
- `SRA-osx-arm64-v<version>.dmg`（推荐）：打开后将 `SRA.app` 拖入 `Applications`。
- `SRA-osx-arm64-v<version>.zip`：可直接解压运行的 `.app` 压缩包。
- **签名提示**：当前 macOS 构建使用本地 ad-hoc 签名，尚未通过 Apple Developer ID 公证；首次打开可能需要在「系统设置 → 隐私与安全性」或右键「打开」确认放行。Intel Mac（x86_64）暂不支持。

## 更新

桌面「设置 → 更新与安装」提供：
- **启动时自动检查更新**（默认开启）：仅请求公开 GitHub Releases API 检查版本号，不上传任何论文、笔记或 API Key。
- **允许预发布版本**（默认关闭）：仅在开启后提示 `-alpha` / `-beta` / `-rc` 预发布版本。
- **检查更新 / 打开发布页面**：由用户确认后跳转官方 GitHub Releases 页面下载并安装，不做静默下载或强制热更新。

## 卸载与数据保护

- **Windows**：通过「Windows 设置 → 应用 → 已安装的应用 → Social Science Research IDE」卸载。
  - **静默卸载**（含 CI 自动化测试）：**永远保留** `%LOCALAPPDATA%\SRA` 下的全部个人数据。
  - **交互式卸载**：默认保留个人数据；仅在用户显式选择删除并完成二次确认，且目标目录不含符号链接/挂载点时，才清理 SRA 专属数据目录。
- **macOS**：将 `SRA.app` 移入废纸篓即可卸载程序；如需清理本地数据，可使用 DMG 中附带的 `Remove_SRA_User_Data.command` 或在备份后手动删除 `~/Library/Application Support/SRA`。
- **个人目录边界**：
  - Windows：`%LOCALAPPDATA%\SRA\data`（PDF / SQLite / 笔记 / 缓存）与 `%LOCALAPPDATA%\SRA\config`（DPAPI 加密凭据）
  - macOS：`~/Library/Application Support/SRA/data` 与 macOS Keychain

关于存储占用展示与清理机制的完整细节，另见 [STORAGE_AND_UNINSTALL.md](STORAGE_AND_UNINSTALL.md)。

## 发布前安全门禁

每次打包与发布前都会自动执行三道隐私与完整性防线：
1. `python scripts/check_release_safety.py source .` — 阻止已跟踪的 `.env`、PDF、SQLite、`.dpapi` 与疑似密钥进入源码。
2. `python scripts/check_release_safety.py artifact <dir>` — 检查组装完成的应用目录不包含任何私人运行数据。
3. `python scripts/check_release_safety.py zip <archive>` — 检查最终分发压缩包内容干净，门禁失败则中断发布。
