# SRA Repository Hygiene / 项目目录清理与隐私检查

这份文档面向**源码仓库**，不是卸载指南。清理之前先停止桌面程序与 `sra api`，执行 `git status --short`，最好已提交或备份当前改动。

## 1. 按当前项目根目录分类

| 路径 | 是否保留 | 原因 |
| --- | --- | --- |
| `.git/` | **必须保留** | 当前仓库的提交历史与分支信息；删除后会丢失本地 Git 仓库。 |
| `.github/` | **必须保留** | CI、隐私扫描、Windows/macOS 自动构建与 Release 工作流。 |
| `src/` | **必须保留** | Python 研究引擎、服务与本地 API。 |
| `desktop/` | **必须保留** | Avalonia 桌面程序与资源。 |
| `installer/` | **必须保留** | Windows/macOS 安装与打包配置。 |
| `scripts/` | **必须保留** | 安全检查、后端冻结、迁移和发布工具。 |
| `tests/` | **必须保留** | 防止 PDF 解析、模型 schema、安全与发布功能回归。 |
| `docs/` | **保留，逐步整理** | 平台打包、安全说明与后续设计文档。 |
| `pyproject.toml`、`README.md`、`PROJECT_DESIGN.md` | **必须保留** | 安装配置、对外介绍和项目设计。 |
| `.gitignore`、`.gitattributes`、`.env.example` | **必须保留** | 仓库规则与安全的配置模板。 |
| `.env` | **私人文件，不提交也不要随意删除** | 可能仍保留可用的本机密钥、覆盖配置或数据路径；先确认迁移状态。 |
| `data/` | **私人研究库，不提交也不要随意删除** | PDF、SQLite、分析结果、缓存与用户笔记。迁移成功后再单独决定是否归档旧库。 |
| `.conda-env/` | **可重新创建，但当前正常工作时保留** | 本地 Python 环境，不随源码或安装包分发。 |
| `.conda-pkgs/` | **可评估清理，非必须** | Conda 本地包缓存；可能可重新下载，也可能服务于离线安装。 |
| `.pytest_cache/` | **可以删除** | 测试运行缓存，会自动重建。 |
| `**/__pycache__/`、`*.pyc` | **可以删除** | Python 字节码缓存，会自动重建。 |
| `desktop/**/bin/`、`desktop/**/obj/` | **可以删除** | .NET 构建输出；下次 `dotnet build` 自动重建。 |
| `build/`、`dist/`、`artifacts/` | **确认不需要已有发布包后可清理** | 构建暂存产物；注意不要误删唯一的发布版本。 |

> **不要把“被 `.gitignore` 忽略”理解为“应该删掉”。** `.env` 和 `data/` 属于要保护的私人文件，不属于可随手清理的垃圾。

## 2. Windows PowerShell：安全检查（只读）

在项目根目录执行：

```powershell
git status --short

git ls-files -- .env data
git ls-files | Select-String '\.(pdf|sqlite3|sqlite|db)$'

git check-ignore -v .env
```

前两条 `git ls-files` 查询理想情况下没有匹配结果。如果出现已跟踪的私人文件，**先停止提交**，检查 Git 历史与密钥是否需要撤销轮换；修改 `.gitignore` 不会从 Git 历史中移除秘密。

## 3. Windows PowerShell：清理纯构建缓存（可选）

先关闭程序。最保守的清理是：

```powershell
# 这不会删除研究库、Conda 环境或 API Key。
Remove-Item -Recurse -Force .\.pytest_cache -ErrorAction SilentlyContinue

dotnet clean .\desktop\SRA.Desktop\SRA.Desktop.csproj

# 可选：只清理明确的源码/测试路径下的 Python 字节码目录
Get-ChildItem .\src,.\tests,.\scripts -Directory -Recurse -Filter __pycache__ -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force
```

**不建议**执行 `Remove-Item -Recurse -Force .\data`、`Remove-Item -Recurse -Force .\.git`、`Remove-Item -Recurse -Force .\.conda-env` 或面向整个仓库的无条件递归删除命令。

## 4. 过时文档与历史升级包

曾用于“直接覆盖修复”的 `.zip`、`.patch`、`*.bak` 或临时脚本，应当先用 `git ls-files` 判断是否受到版本管理。若只是本机下载物、且新代码已经在 Git 中验证，可移动到项目外的私人归档目录后再清理；**不要按文件名一律删除 `scripts/` 或 `docs/` 中的内容**。

`PROJECT_DESIGN.md`、旧 Web UI、Python CLI 不属于“没用代码”：它们分别承载研究原则、兼容调试路径及自动化入口。等桌面功能、CI 和数据迁移完成、并证明不再依赖时，才考虑正式标记弃用。

## 5. 正式发布前的门禁

1. **仅发布 CI 产物**，不要压缩开发项目根目录。
2. 检查 `scripts/check_release_safety.py` 的 source/artifact/zip 扫描结果。
3. 确认 Windows 与 Apple Silicon 的安装包经真实设备测试，未包含 `.env`、PDF 或 SQLite。
4. 公布 `.env.example` 而不是 `.env`；密钥按用户独立设置。
5. 若仓库没有 `LICENSE`，正式对外宣传开源授权前先确定许可证。
6. 不公开包含真实论文库、私人笔记或 API 设置的截图；README 展示图建议使用虚构示例数据。
