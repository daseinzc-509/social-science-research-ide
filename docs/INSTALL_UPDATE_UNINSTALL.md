# SRA Windows Desktop — 更新、卸载与隐私边界

> 当前是 **Desktop Frontend Preview**。安装器只包含 Avalonia/.NET 桌面前端，不包含 Python/FastAPI、Docling、OCR 或模型服务。它需要用户自行安装/运行 `sra api`。这不是完整的一键部署包。

## 安装

GitHub Actions 在 Windows runner 上 `dotnet publish -r win-x64 --self-contained true`，安全扫描，然后生成：

- `SRA-Desktop-win-x64-vX.Y.Z.zip`：免安装的桌面前端。
- `SRA-Desktop-Setup-win-x64-X.Y.Z.exe`：每用户安装器（Inno Setup），添加开始菜单、可选桌面快捷方式及 Windows “已安装的应用”卸载入口。
- `SRA-Desktop-SHA256SUMS.txt`：两份发布包的 SHA-256。

安装器使用 **稳定 AppId**，升级时使用新版本安装器覆盖旧程序目录。程序默认安装在 `%LOCALAPPDATA%\Programs\SRA Desktop`，不要求管理员权限。

## 更新

桌面设置页提供：

- **启动时自动检查更新**（默认开启）：从公开的 GitHub Releases API 检查版本号。
- **允许预发布版本**（默认关闭）：可选 alpha/beta/rc。
- **检查更新**：立即检查版本。
- **打开发布页面**：用户确认后通过系统浏览器获取新版安装器。

这一版实现的是 **自动检查 + 提示 + 用户主动安装**，**不做后台下载或静默安装**。不向 GitHub 上传任何 Key、PDF、数据库内容或本地笔记。版本号由 GitHub tag 传到 `dotnet publish` 的程序集版本；正常发布必须通过 `vX.Y.Z` tag。

为什么暂不执行静默更新：目前 Python 核心需要单独运行，且尚未引入可验证的安装包代码签名及前后端兼容升级机制。用户应从官方 Release 页面安装新版本，校验公开的 SHA-256。

GitHub 的 `/releases/latest` 不包括预发布，所以客户端使用**发布列表**按版本号比较；启用预发布开关后才会考虑预发布。

## 卸载

Windows 设置 → 应用 → 已安装的应用 → `Social Science Research IDE Desktop (Preview)` → 卸载。

- 删除：安装目录下的桌面前端程序、开始菜单快捷方式，以及安装时创建的桌面快捷方式。
- **保留**：`%LOCALAPPDATA%\SRA\config`（包括 Windows DPAPI 保护的 API Keys）、`%LOCALAPPDATA%\SRA\data`（PDF、research.sqlite3、笔记与模型结果）、桌面偏好文件。
- 对于旧版项目目录中的 `.env`、`data/`，安装/卸载程序完全不访问。

彻底删除个人数据请先确认已备份，再**由用户自行**删除 `%LOCALAPPDATA%\SRA`。卸载器绝不自动做这件事，防止误删研究成果。

CI 实际运行安装/卸载 smoke test，用测试标记文件确认用户目录不被删除。

## 发布前安全门禁

`python scripts/check_release_safety.py source .` 拒绝 Git 已跟踪的私有 `.env`、PDF、数据库、`.dpapi` 和疑似硬编码 API Key。

`python scripts/check_release_safety.py artifact artifacts/win-x64` 检查真正的 .NET 发布目录。

`python scripts/check_release_safety.py zip artifacts/...zip` 检查压缩包，安全检查失败时 GitHub Release 上传任务不会继续。Installer 仅包含已通过检查的 publish 目录和仓库中的安装声明。

> 这些扫描是防线，不保证发现所有形式的密钥；务必继续使用独立用户数据目录、DPAPI、受保护的 GitHub 权限及代码审查。如果旧 Key 曾进入 Git 历史，应立即吊销。

## 仍未完成的正式发布条件

1. Python/Docling 后端随安装器发布、Desktop 自动启动后端、无须另开命令行。
2. 本地 API 的随机鉴权令牌与跨来源访问防护。
3. 稳定的后端数据库迁移、版本协商和回滚策略。
4. Windows 安装包代码签名与经验证的静默更新机制。
5. 完整安装体验、自动更新和卸载行为的 Windows 真机测试。


## 存储透明度与可选择的数据清理（v0.2 预览增强）

最新路径、设置页展示、Windows 交互式卸载选择，以及 macOS 私有数据清理操作详见 [STORAGE_AND_UNINSTALL.md](STORAGE_AND_UNINSTALL.md)。
