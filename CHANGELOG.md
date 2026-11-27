# Changelog

本项目遵循语义化版本号（Semantic Versioning）。带 `-alpha` / `-beta` / `-rc` 后缀的标签发布为 GitHub Pre-release，正式版本号（如 `v0.2.2`）发布为正式 Release。

---

## [v0.3.0] - 2026-11-27

从单篇论文精读正式迈向**跨文献专题研究工作台（Multi-Paper Evidence & Synthesis Workbench）**，并落地两位数 MB 瘦壳安装包架构与一键式独立托管 CPU-only Docling 解析引擎。

### 核心新能力

- **两位数 MB 瘦壳安装包与独立托管 CPU-only Docling 引擎**
  - 引入 `slim` 瘦壳后端架构（随包内置独立 `uv` 工具，彻底解耦 `torch`/`docling`/`transformers`/`scipy`/`cv2`/`numpy` 等巨型推理依赖），完整保留纯 Docling PDF 解析质量（绝不引入 PyMuPDF）。
  - 在「设置 → 模型配置中心 → Docling / 增强」新增 **本地 PDF 解析引擎（CPU-only Docling）** 管理面板，支持一键安装、离线自检与安全清理独立虚拟环境，自动复用「网络代理」页的下载代理配置（严格剥离所有 AI API Key），并提供官方源 / 清华大学 / 阿里云 PyPI 镜像加速。
  - 瘦壳模式下全篇 PDF 解析、离线权重下载/验证与局部复核框选 OCR 均通过受控隔离子进程无缝委派给托管引擎，未安装时导入 PDF 自动弹窗直达模型配置中心。
- **跨文献研究专题、确定性 Evidence Matrix 与多文献综合（Literature Synthesis）**
  - 新增研究专题目录树、原生右键上下文菜单、专题级研究笔记（`MY NOTE`）与**零 LLM 调用的确定性跨文献 Evidence Matrix**（支持按阅读状态、人工审定状态与研究类型多维快速筛选，并导出 Excel UTF-8 BOM `.csv` 与 `.md`）。
  - 新增基于已审定证据矩阵的**多文献综合（Consensus / Conflicts & Scope Conditions / Methodological Divergence / Research Gaps）**：优先采纳人工修正表述、自动剔除已驳回主张、强制透明外发预检与双重确认、白名单防幻觉页码溯源（点击引文芯片直跳原 PDF 页高亮）、不可变快照归档，支持一键导出与复制 Markdown。
  - 支持专题精准批量工作流：处于专题视图时导入/拖放 PDF 自动幂等归入当前专题（绝不覆写既有核心文献状态）；专题顶栏提供「分析待处理 (N)」仅跑当前专题未分析论文。
- **研究者闭环：个人笔记、Claim 四态人工审定与 FTS5 全局检索高亮**
  - 新增单篇阅读笔记（`MY NOTE`）与独立表 `claim_reviews` 四态人工审定（确认 / 修正 / 存疑驳回 / 待复核），物理隔离于 `paper_cards`，重跑分析绝不覆盖。
  - 新增跨论文与专题级 SQLite FTS5 全文检索面板（覆盖正文、Paper Card、单篇笔记、专题笔记与人工审定），并支持 MD3 明暗自适应命中词荧光高亮与直跳定位。
- **NoteExpress 互操作、论文库排序与研究库 WAL 安全备份**
  - 支持单篇与整专题导出 NoteExpress 原生 `.ris`（完整合并人工题录修订、个人笔记与审定摘要至 `N1`）。
  - 新增论文库 6 维即时排序（最新导入、发表年份升/降序、题名、待分析优先、待审定优先）及左侧卡片轻量「已审 / 笔记」徽章。
  - 新增基于 SQLite Online Backup API 的**研究库安全备份（`.zip` 轻量/完整归档双模式，严守零凭据边界）**、桌面端只读完整性与 SHA-256 校验，以及离线原子恢复命令 `sra restore-backup`（自动保留 `.bak` 旧库并重写跨设备 PDF 路径）。

### 体验打磨与缺陷修复

- 修复「原始 Card」页签中文 JSON 转义体积膨胀导致的焦点切换卡顿（改用 UTF-8 直显 + 流畅预览 + 一键复制完整 JSON + 禁用只读框动画与撤销栈）。
- 修复删除论文时因 `IsBusy` 守卫短路导致左侧列表残留幽灵项与 404 的问题，以及删完最后一篇后右侧统计未归零的问题。

---

## [v0.2.2] - 2026-11-25

单篇社科论文精读桌面工作台的首个正式稳定版本，汇总了 P0–P2 架构重构与 Google Material Design 3 前端交互重塑。

### 新增与亮点

- **Material Design 3（MD3）全面视觉升级**
  - 引入明暗双主题五级 Tonal Surface 色阶、学术紫主色（Seed `#6750A4`）、高对比度暗色主题、全药丸四级按钮体系（Filled / Filled Tonal / Outlined / Text / Error）、填充式全药丸检索栏与 Active Pill 顶栏导航。
  - `ClaimCard` 新增左侧 4px 语义来源色条（严格区分 `AUTHOR` / `AI REVIEW` / `MY NOTE`）与四色语义审计状态徽章；合并单元格表格预览全面自适应明暗主题。
- **高频桌面操作闭环**
  - **全窗口拖放导入 PDF**：支持直接拖入单篇 PDF、多篇 PDF 或整个文件夹自动扫描导入，配 MD3 居中拖放提示蒙层。
  - **全局快捷键**：`Ctrl+F` 聚焦检索栏、`Ctrl+/` 切换右侧研究画像（专注阅读）、`Ctrl+O` 导入 PDF、`Esc` 清空检索关键词。
  - **原文证据直跳 PDF 页高亮**：点击 `ClaimCard` 中任意原文证据的「定位 PDF 原文 →」，直接打开 `RegionReviewWindow` 对应 PDF 页，以琥珀色荧光框高亮对应 `SourceBlock` 并自动滚入视野（绝不修改原始 PDF、`SourceBlock` 或 `PaperCard`）。
  - **两大核心弹窗重塑**：局部复核弹窗右侧重构为 3 个清晰子分页（`① 框选校订` / `② 版本历史` / `③ 独立复评 (Pro)`），模型配置中心全面采用 MD3 分组卡片与双列清爽表单，完整保留外发预检与显式计费勾选红线。

### 架构、性能与稳定性（P0–P2）

- **P0 稳态加固**：全库统一启用 SQLite WAL 模式、`PRAGMA foreign_keys=ON` 与 `busy_timeout`；`JobService` 支持重任务有界并发、安全检查点取消与终态记录上限回收；Windows Job Object 与后端父进程看门狗防止桌面端异常退出后残留孤儿 Python 进程。
- **P1 架构分层**：统一多模态 LLM 客户端与网络代理边界、抽取应用服务层（`ModelCenterService`、`RegionReviewService`、`VisualService` 等）、建立强类型 FastAPI 响应契约，并将超大模块拆分为清晰职责边界。
- **P2 能力演进**：新增 SQLite FTS5 中英双语全文检索（支持文献正文、Paper Card、笔记与人工校订索引并兼容老库自动回填）、后端 `research_projects` 与零 LLM 调用的确定性 Evidence Matrix API、SSE `/api/v1/jobs/{job_id}/events` 实时进度推送（桌面端支持自动降级轮询），以及 PyInstaller 依赖卫生与构建裁剪门禁。

### 工程卫生与发布流水线

- 移除早期提交中误纳入 Git 跟踪的 101 个 `bin/Debug` 与 `obj/Debug` 二进制产物（释放约 568 MB 仓库负载）。
- `.github/workflows/ci.yml` 补齐全部 P0–P2 架构与 MD3 桌面契约回归测试，本地全量测试达到 **400 passed**。
- `.github/workflows/release-desktop.yml` 支持按 tag 自动分流正式 Release 与 Pre-release，并自动读取 `CHANGELOG.md` 对应版本说明。

---

## [v0.2.1-beta.1] - 2026-10-11

- 首个 Windows x64 + macOS Apple Silicon 双端联合打包（Avalonia 桌面端 + PyInstaller 冻结 Docling 后端）公开预发布版本。
- 落地统一模型配置中心、局部区域 OCR 校订与 v5 独立 Pro 对照复评、存储透明度与安全卸载保护、隐私防泄漏门禁及离线构建体积审计。
