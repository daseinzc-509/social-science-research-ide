# SRA 存储位置、安装与彻底卸载

> 本文记录 **Windows x64 + macOS Apple Silicon** 的开发/预览安装行为。不同版本安装程序的界面可能不同；以安装时实际显示路径为准。

## 1. 文件在哪里？

| 类型 | Windows（默认） | macOS（默认） |
|---|---|---|
| SRA 程序 | `%LOCALAPPDATA%\Programs\SRA Desktop`（安装时可改） | 用户放置的 `SRA.app`，通常在 `/Applications` |
| 论文库（PDF 副本、SQLite、笔记） | `%LOCALAPPDATA%\SRA\data` | `~/Library/Application Support/SRA/data` |
| API Key | `%LOCALAPPDATA%\SRA\config\model-keys.dpapi`，由 Windows DPAPI 保护 | 系统 Keychain 中的 SRA 专属服务条目 |
| 模型非密钥配置 | `%LOCALAPPDATA%\SRA\config` | `~/Library/Application Support/SRA/config` |
| 桌面偏好 | `%LOCALAPPDATA%\SRA\desktop-preferences.json` | `~/Library/Application Support/SRA/desktop-preferences.json` |
| SRA 管理的缓存、PDF 预览副本 | `%LOCALAPPDATA%\SRA\cache` | `~/Library/Application Support/SRA/cache` |

软件设置 → **存储与卸载** 会向 Python API 读取**实际使用**的论文库路径，以及各目录占用。设置页的「打开文件夹」按钮不会删除任何内容。若曾设置 `SRA_HOME` 或 `SRA_DATA_DIR`，显示的实际目录可能不同于此表；**自定义目录不纳入卸载器自动清理**。

新版本的捆绑后端会默认把 Hugging Face、PyTorch 和遵循 XDG 的缓存引导到 SRA 私有缓存目录，但**尊重已有的显式缓存环境变量**。Docling 仍可能根据模型配置、版本或历史运行位置使用其他目录；不保证每个依赖的历史缓存都在 SRA 根目录。为了避免首次解析失败，未强制把 `DOCLING_ARTIFACTS_PATH` 指向一个空目录。先前已下载到用户全局缓存的模型不会被自动移动或删除。

**导入 PDF 的原始文件不会因为导入或卸载而被删除**：SRA 研究库中的是管理用副本。若用户通过自定义研究库导入，该目录也不属于自动删除范围。

## 2. Windows 安装和卸载

- 安装向导会显示目标文件夹，并允许更改。SRA 以当前用户身份安装，无需管理员权限。
- 标准卸载：从「设置 → 应用 → 已安装的应用」卸载。**默认保留**论文、配置、密钥和缓存。
- 交互式卸载会明确询问是否**额外清除** `%LOCALAPPDATA%\SRA`。只有选择删除并在第二次提示中确认后，才尝试删除此固定目录。
- `/SILENT` / `/VERYSILENT` 自动卸载**始终保留**用户数据（供 CI 或管理员使用）。
- 如果 SRA 目录存在 junction / symbolic link 或无法安全检查的目录，自动清理停止，提醒用户手工检查。
- 自定义 `SRA_HOME` / `SRA_DATA_DIR`、项目源码目录下旧 `data/`、外部共享模型缓存、系统使用历史不会被卸载器删除。
- 已经安装的旧版本**不会凭空获得**此卸载选择。需要安装包含新版脚本的安装器并通过 Windows 本地实际验收。

**谨慎：** 如果选择删除，`%LOCALAPPDATA%\SRA` 中的笔记、数据库和导入副本不可恢复。请先备份。真实资料不要用来做卸载器测试。使用干净的 Windows 测试账户或虚拟机验收。

## 3. macOS 安装和卸载

- 把 `SRA.app` 放进 `/Applications`（或自己选择的位置）。移至废纸篓即移除程序，**默认保留**私人资料。
- DMG 里附带 `Remove_SRA_User_Data.command`，只有用户自行运行并输入 **`DELETE-SRA-DATA`** 后才会清除当前用户的 SRA 专用 `Application Support/SRA` 目录、SRA Keychain 条目与一个旧版偏好文件。
- 执行前必须退出桌面端和 `sra-backend`。不会删除原始 PDF、自定义/外部目录或全局缓存。
- 未 Apple 公证的 Alpha 包可能被 Gatekeeper 拦截，包括 DMG 中的辅助脚本；可参考文档在 Terminal 中检查脚本内容后手动执行，不建议关闭全局安全功能。
- 旧版 .NET 预览版本的桌面偏好如位于 `~/.local/share/SRA/desktop-preferences.json`，新版会尝试复制到统一目录，不覆盖已有配置。

## 4. 不代表无痕卸载

Windows 安装登记由 Inno Setup 处理，标准卸载后会清理它创建的卸载项；Windows 最近使用记录、系统缩略图、崩溃日志和其他共享缓存不属于 SRA 管理的数据。macOS 也可能留有系统级使用记录。因此“清除本机 SRA 私有数据”**不等同于抹除操作系统中的每一条历史记录**。

## 5. 开发验证

```powershell
conda run -p .conda-env python -m pytest -q tests/test_storage_transparency.py
dotnet build .\desktop\SRA.Desktop\SRA.Desktop.csproj
```

正式打包工作流需要在 Windows runner 上编译 Inno Setup，并在 macOS runner 上生成 DMG。静态检查或 Python 单测通过，不代表已经在真实系统完成交互式卸载验收。
