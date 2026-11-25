# AGENTS.md — Social Science Research IDE 开发协作与工程红线指南

本文件面向参与本仓库开发的人类贡献者与 AI Coding Agents（Antigravity、Claude Code、Codex、Cursor 等）。**动手改代码前请先阅读本文件与 `docs/ARCHITECTURE.md`。**

---

## 1. 项目速览与核心定位

**Social Science Research IDE（SRA）** 是一个 **local-first、evidence-first** 的社科文献研究工作台。

- **技术栈**：
  - 桌面端：`.NET 8`（`net8.0`，C# 12）+ `Avalonia UI 12.1.3` + `CommunityToolkit.Mvvm`（建议用 .NET 10 SDK 编译），采用 Google Material Design 3（MD3）设计系统；
  - 后端与研究核心：`Python 3.12` + `FastAPI` + `Pydantic v2` + `Docling 2.134.0` + `SQLite`（WAL 模式 + FTS5 双语全文检索）；
  - 双端打包：Windows x64（PyInstaller + Inno Setup）、macOS Apple Silicon ARM64（PyInstaller + `SRA.app` + `hdiutil` DMG）。
- **核心设计原则**：桌面端只做交互与展示，所有解析、证据校验、数据库访问与模型调用都通过本地版本化 API（`/api/v1`）收口到 Python 应用服务层。

---

## 2. 不可突破的 6 条领域与安全红线

1. **Evidence First（证据不可篡改）**
   - 原始 PDF、Docling 提取的 `SourceBlock` 与既有 `PaperCard` 属于审计基准；局部 OCR 校订（`region_reviews`）、独立 Pro 复评（`review_reanalyses`）、手动元数据修订（`paper_metadata_overrides`）与个人笔记（`user_notes`）必须独立存储，**绝不可反向覆盖原始证据**。
   - 重跑分析绝不可覆盖用户手动修订的元数据或人工审定记录。
2. **来源严格分离（Provenance Separation）**
   - 作者陈述（`AUTHOR_STATED`）、AI 方法学审读（`AI_INFERRED`）、人工校订（`HUMAN_REVIEWED`）与个人笔记（`USER_NOTE`）必须在数据模型、全文索引和 UI 呈现中保持独立身份，禁止混为一谈。
3. **Traceability ≠ Semantic Support**
   - `VerificationStatus` 仅代表可回溯到原文位置；`SemanticSupportStatus` 是模型辅助的语义支持判断，不得把“找到页码”等同于“逻辑成立”。
4. **Cost-Aware & Fail-Closed（外发计费显式授权）**
   - 任何会调用外部付费 API 的高成本或敏感操作（单张图片 Vision 分析、v5 局部复核后独立 Pro 复评）必须满足：
     - 先做纯本地预检并向研究者展示将发送的内容与快照指纹；
     - 同意复选框默认未勾选（`IsChecked = false`），必须显式勾选才允许发起请求；
     - 超时或失败后**绝不自动重试**，必须回到预检态，防止重复扣费。
5. **纯 Docling 架构（禁止回退 PyMuPDF）**
   - PDF 解析与表格提取统一走 Docling，不得重新引入 `pymupdf` / `fitz` 或恢复旧 `core` 打包 profile（由 `scripts/check_bundle_without_pymupdf.py` 与 `tests/test_docling_only_regressions.py` 强制守护）。
6. **隐私与凭据隔离**
   - 永远不要提交 `.env`、`data/`、`*.pdf`、`*.sqlite3`、`*.dpapi`、`secrets*.json` 或真实 API Key。
   - 桌面端 API 通信仅绑定 `127.0.0.1`，捆绑版强制校验 256-bit `X-SRA-Desktop-Token`。
   - 卸载程序静默模式必须永远保留用户数据目录（`%LOCALAPPDATA%\SRA` / `~/Library/Application Support/SRA`）。

---

## 3. 代码组织与修改边界

| 层级 | 目录 | 允许做什么 | 禁止做什么 |
| --- | --- | --- | --- |
| **桌面 UI 与 ViewModel** | `desktop/SRA.Desktop/` | MVVM 展示、MD3 样式、交互事件、通过 `SraApiClient` 调用 `/api/v1` | 直连 SQLite、直接调用 Docling/Python、在 C# 中复制证据校验逻辑、硬编码明暗色值 |
| **HTTP API 适配层** | `src/sociology_research/api/` | 路由声明、请求校验、Pydantic 响应 DTO、SSE 流式转发、异常映射 | 直接写复杂业务编排或绕过 `services/` 操作数据库 |
| **应用服务层** | `src/sociology_research/services/` | 用例编排（导入、分析、任务调度、项目与矩阵、模型中心、局部复核、导出、维护） | 直接依赖桌面 UI 实现细节 |
| **研究核心与存储** | `src/sociology_research/` 及 `analysis/` | PDF 解析、Lite/Pro 管线、确定性核验、SQLite 仓储与 FTS5 索引 | 绕过 `db.py` 的 `open_sqlite_connection` / `sqlite_connection` 直接 `sqlite3.connect` |

---

## 4. 高频踩坑与静态契约红线（务必核对）

本仓库包含一批严格守护桌面端与发布流程的静态契约测试，改动对应文件前请务必确认以下约束：

### 4.1 桌面端 XAML / C# 约束
- **C# 12（`net8.0`）编译规则**：`async` 方法内部不可声明 `Span<T>` / `ReadOnlySpan<T>` 局部变量（触发 `CS9202`）。Release 编译必须 **0 警告，0 错误**。
- **`MainWindow.axaml` 必须保持合法 XML**，且严守：
  - `Click="MinimizeWindow_Click"` 按钮内有且只有 1 个 `<Border>`，`Width >= 12`、`Height >= 1`、`Background` 含 `InkBrush`；
  - `RawCardJson` 与 `JobLog` 的 `TextBox` 属性顺序保持 `Text="{Binding ...}"` → `Background="{DynamicResource SurfaceSoftBrush}"` → `Foreground="{DynamicResource InkBrush}"`（`test_p0_stability_regressions.py` 正则匹配）；
  - `x:Name="ModelCenterLaunchButton"` 恰好出现 1 次且**不在 XAML 写 `Click="..."`**，由 `MainWindow.axaml.cs` 在 `InitializeComponent()` 后通过 `ModelCenterLaunchButton.Click += OpenModelCenter_Click;` 显式绑定（规避 Avalonia `AVLN3000`）；
  - 不得出现 `<TextBlock Text="本地 API"`、`CodeBrush`、`<TextBlock Text="0.1"` 或旧的冗余模型表单标签；
  - 保留 `DragDrop.AllowDrop="True"`、`DropOverlay`、`LibrarySearchBox` 及现有按钮事件名。
- **`RegionReviewWindow.cs`**：必须保留 v5 外发复评安全契约（`IsChecked = false`、`_externalConsent.IsChecked != true`、`outbound_payload`、`查看即将发送给 Pro API 的完整文字`、`PrepareReviewedComparisonAsync`、`RunReviewedComparisonAsync`、`ResetReviewRunPlan`）与三子分页结构。
- **`ModelCenterWindow.cs`**：保留 `TabControl`、`"AI 服务商"`、`"网络代理"`、`"Docling / 增强"`、`"保存并应用到选定阶段"`、`StageDescription`、`SaveModelCenterEnhancementsAsync`；**不要删除** `desktop/SRA.Desktop/Views/ModelCenterWindow.cs.tmpl`（被 `scripts/apply_model_center_integration.py` 引用）。
- **`StructuredTableView.cs` & `UiModels.cs`**：保留 `Grid.SetRowSpan` / `Grid.SetColumnSpan` 与自适应资源绑定；`TableItem` 中禁止使用 `Distinct()` 篡改重复表头单元格。
- **安装器**：`installer/windows/SRA.Desktop.iss` 的 `AppId=org.daseinzc.socialscienceresearchide.desktop` 与 `PrivilegesRequired=lowest` 不可更改，且禁止添加 `[UninstallDelete]`。

### 4.2 样式与主题约束
- 新增或修改任何桌面控件/弹窗，必须复用 [App.axaml](desktop/SRA.Desktop/App.axaml) 中的 MD3 动态资源令牌（`SurfaceContainer*Brush`、`TonalFillBrush`、`OnTonalFillBrush`、`Ink*Brush`、`OutlineSubtleBrush` 等）及 [ControlStyles.axaml](desktop/SRA.Desktop/Styles/ControlStyles.axaml) 按钮分级（`primary` / `tonal` / `secondary` / `ghost` / `danger`），确保 Light / Dark 主题实时切换无硬编码色块。

### 4.3 Windows PowerShell 注意事项
- Windows PowerShell 5.1 传 `-c "..."` 给 `python.exe` 会剥离内嵌双引号；执行多行或带引号的 Python 校验脚本时，使用 here-string 管道 `@' ... '@ | & ".\.conda-env\python.exe" -`。

---

## 5. 必跑验证门禁（提交前）

在仓库根目录执行以下全部校验，**全部通过后才可提交**：

```powershell
# 1. 桌面端 Release 编译（必须 0 警告、0 错误）
dotnet build "desktop\SRA.Desktop\SRA.Desktop.csproj" -c Release --nologo

# 2. 全量 Python / API / 架构 / 打包策略 / 桌面契约回归测试
& ".\.conda-env\python.exe" -m pytest -q tests

# 3. 源码隐私安全门禁（必须输出 RELEASE SAFETY GATE: PASSED (source)）
& ".\.conda-env\python.exe" scripts/check_release_safety.py source .

# 4. 补丁整洁度检查
git diff --check
```

若新增功能属于跨平台离线回归测试，请同步评估是否将其加入 `.github/workflows/ci.yml` 的 `python-tests` 清单中，防止云端 CI 出现盲区。

---

## 6. 提交、发版与文档同步纪律

- **语言规范**：面向用户的文案、代码注释、文档默认使用**简体中文**；专有名词、代码标识符、路径和命令保持原样。
- **提交规范**：遵循 Conventional Commits（`feat(...)`、`fix(...)`、`refactor(...)`、`ci:`、`docs:`、`release(vX.Y.Z):`）。未经用户明确指示，不擅自 `git commit` / `git push` / `git tag`。
- **文档同步**：改动架构、数据库表、API 路由、快捷键或发版策略时，同步更新 `docs/ARCHITECTURE.md`、`CHANGELOG.md` 与 `README.md`。
- **严禁将 `bin/`、`obj/`、`dist/`、`__pycache__/` 或临时下载产物纳入 Git 跟踪**。
