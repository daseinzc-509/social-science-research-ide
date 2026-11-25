# Changelog

本项目遵循语义化版本号（Semantic Versioning）。带 `-alpha` / `-beta` / `-rc` 后缀的标签发布为 GitHub Pre-release，正式版本号（如 `v0.2.2`）发布为正式 Release。

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
