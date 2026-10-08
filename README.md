# Social Science Research IDE

这是一个 **local-first、evidence-first** 的社会科学研究阅读环境。

项目的目标不是把 PDF “总结得更长”，而是把论文中的**主张、证据、适用范围、语义支持关系和方法学风险**拆开保存，使研究者能够回到原文检查、比较和修正理解，并逐步把单篇阅读扩展为书籍阅读、研究项目、多文献比较和文献综述工作流。

当前主线仍是 **Paper Understanding v2**；下一阶段进入 **Desktop Foundation**：保留 Python 研究引擎，建立稳定的本地 API，并以 Avalonia 构建桌面工作台。

---

## 当前状态

目前已经具备：

- 单篇 / 批量 PDF 导入，本地 SQLite 存储；
- Docling-first 解析，保留 page / bbox / block provenance；PyMuPDF 作为后备；
- 文本质量检测与必要时 OCR；异常文本不会静默当作正常解析；
- Lite 提取元数据与基础研究事实；
- 元数据 layout recovery：front matter、重复页眉、页码范围等确定性恢复；
- `title / authors / journal / DOI` 使用 metadata provenance 校验；
- Paper Understanding v2：Study Profile、Claim Type、Claim Scope、Evidence Role、Semantic Support、Claim Audit；
- Pro 独立从整篇 SourceBlock 定向取证，不只读取 Lite 已引用片段；
- 按实验、观察性定量、质性、理论、混合方法、综述切换审读维度；
- “来源已定位”和“语义支持”两层状态；
- 精读报告：研究问题、研究设计、核心发现、作者解释、作者自述局限、AI 方法学审读、Claim/Evidence 审计；
- Paper Card JSON、解析文本、参考文献 JSON / BibTeX、精读 Markdown 导出；
- 本地模型缓存、数据库 doctor、手动元数据修订、重新解析；
- Lite / Pro 可使用独立 API Key、Base URL 和模型，也兼容旧的共享连接配置。

当前浏览器 UI 是**过渡界面**。它仍然可用，但后续主要用于调试和兼容；新的产品界面计划使用 Avalonia。

---

## 产品方向

项目正在从“单篇论文分析器”演进为：

> **以证据为底层结构的个人研究阅读环境。**

长期工作流：

```text
Sources
  ├─ Papers
  ├─ Books
  └─ Reports / Chapters
        ↓
Document Understanding
        ↓
Claims / Evidence / Notes / Review Decisions
        ↓
Research Projects
        ↓
Evidence Matrix / Debate / Comparison
        ↓
Synthesis
        ↓
Literature Review / Research Writing
```

论文、书籍和后续其他文献类型共享 provenance、evidence、claim 和 note 等底层概念，但不强迫不同文献使用同一套阅读模板。

---

## 当前架构

当前运行链路：

```text
PDF
  ↓
Docling / PyMuPDF
  ↓
SourceBlock + page/bbox provenance
  ↓
Evidence ledger
  ↓
Lite
  ├─ bibliographic metadata candidates
  └─ basic author-stated claims
  ↓
metadata provenance resolver + deterministic verification
  ↓
Paper Understanding v2 router
  ↓
Pro independent retrieval from the whole paper
  ├─ StudyProfile
  ├─ methodology-specific review
  ├─ ClaimAudit
  └─ reading recommendation
  ↓
PaperCard
  ↓
Overview / Facts & Evidence / Deep Reading / AI Review / References / Tables
```

核心代码仍是 Python：解析、证据账本、Lite/Pro、SQLite、参考文献和研究记忆都不会因为桌面化而重写成 C#。

---

## 目标架构：Avalonia Desktop + Python Core

下一阶段采用明确的前后端边界：

```text
┌──────────────────────────────┐
│ Avalonia Desktop (.NET)      │
│                              │
│ Library / Reader / Inspector │
│ Projects / Settings / Jobs   │
└──────────────┬───────────────┘
               │ localhost HTTP/JSON
               ▼
┌──────────────────────────────┐
│ Local Application API        │
│ versioned DTO / job status   │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Python Research Core         │
│                              │
│ parser / analyzer / library  │
│ references / repository      │
│ model clients / reports      │
└──────────────┬───────────────┘
               │
               ▼
        SQLite + local files
```

### 架构规则

1. **Avalonia 只负责产品界面与桌面生命周期，不重写研究引擎。**
2. **API 是桌面端唯一稳定入口。** Avalonia 不直接访问 SQLite，也不直接 import Python 内部模型。
3. **Domain model 与 API DTO 分开。** 内部 Pydantic 模型可以演进，而桌面 API 保持版本化兼容。
4. **先轮询任务状态，不急着引入 WebSocket/SSE。** 当前分析任务分钟级，简单 job polling 足够。
5. **SQLite 继续作为单机默认存储。** 在真实规模证明有必要之前不迁移 PostgreSQL。
6. **现有 Web UI 暂时保留为 legacy/debug adapter。** 新功能不再优先堆进内嵌 HTML。

详细架构见 `docs/ARCHITECTURE.md`。

---

## 推荐目录演进

不做一次性大搬家。先保持 `src/sociology_research` 包名稳定，再逐步形成：

```text
.
├─ src/
│  └─ sociology_research/
│     ├─ models.py              # 当前领域模型；后续再拆 domain/
│     ├─ analyzer.py            # Paper Understanding v2
│     ├─ parser.py
│     ├─ repository.py
│     ├─ library.py
│     ├─ references.py
│     ├─ config.py
│     ├─ llm_client.py
│     ├─ services/              # 新：面向 UI/API 的应用用例
│     ├─ api/                   # 新：localhost API + DTO
│     └─ legacy_web/            # 后续：旧 Web UI 迁入这里
├─ desktop/
│  ├─ SRA.Desktop/              # Avalonia 应用
│  └─ SRA.Desktop.Tests/
├─ tests/
├─ docs/
│  ├─ ARCHITECTURE.md
│  └─ PROJECT_DESIGN.md
├─ README.md
└─ pyproject.toml
```

第一轮重构只需要新增 `services/`、`api/`、`desktop/`，不必马上把现有 Python 文件全部移动。

---

## 模型配置

推荐 Lite 与 Pro 使用完全独立的连接：

```text
SRA_LITE_API_KEY=...
SRA_LITE_API_BASE_URL=https://...
SRA_LITE_MODEL=...

SRA_PRO_API_KEY=...
SRA_PRO_API_BASE_URL=https://...
SRA_PRO_MODEL=...
```

如果 Lite / Pro 使用同一家服务商，可以填写相同 Key / Base URL。

旧配置仍可作为兼容 fallback：

```text
SRA_API_KEY=...
SRA_API_BASE_URL=https://...
SRA_LITE_MODEL=...
SRA_PRO_MODEL=...
```

不要提交真实 `.env` 或 API Key。

---

## 快速开始

### Python 环境

```powershell
conda create -p .conda-env --override-channels -c conda-forge python=3.12 "pydantic>=2.7,<3" "pymupdf>=1.24,<2" pip
conda run -p .conda-env python -m pip install --no-build-isolation -e .
conda run -p .conda-env python -m pip install "docling>=2.131,<3"
```

### 当前 Web 工作台

```powershell
conda run --no-capture-output -p .conda-env sra ui
```

这是当前可用入口；Avalonia Desktop 尚属于下一阶段实现目标。

### 常用 CLI

```powershell
conda run -p .conda-env sra import-pdf "D:\papers\example.pdf"
conda run -p .conda-env sra import-pdf-dir "D:\papers" --json
conda run --no-capture-output -p .conda-env sra analyze PAPER_ID
conda run -p .conda-env sra export-card PAPER_ID --output paper-card.json
conda run -p .conda-env sra export-text PAPER_ID --output parsed-paper.txt
conda run -p .conda-env sra extract-references PAPER_ID --output references.json
conda run -p .conda-env sra export-references PAPER_ID --output references.bib --format bibtex
```

UI 中分析完成后可导出 `*-deep-reading.md` 精读报告。

---

## 两层“支持”状态

### VerificationStatus：来源是否可定位

- `SUPPORTED`：来源已定位；
- `NEEDS_REVIEW`：存在来源问题或该项本身为 AI 推断，需要人工复核；
- `NO_SOURCE`：没有来源引用。

### SemanticSupportStatus：原文与主张的语义关系

- `SUPPORTS`
- `PARTIALLY_SUPPORTS`
- `QUALIFIES`
- `CONTRADICTS`
- `BACKGROUND_ONLY`
- `UNCLEAR`

因此可以合法出现：**“来源已定位，但原文只部分支持、限定或反驳该主张。”**

---

## 精读报告

精读报告不是第三次模型调用，而是 saved `PaperCard` 的确定性视图，避免“摘要的摘要”进一步损失证据链。

当前包含：

- 论文定位 / 摘要；
- 研究设计画像；
- 研究问题；
- 理论与概念；
- 研究方法、样本与测量；
- 核心发现及 semantic support；
- 作者解释 / 机制；
- 作者自述局限；
- AI 方法学审读与 AI 发现的局限；
- 阅读建议；
- Claim / Evidence 审计与 PDF 原文。

---

## Book Understanding：下一类文献

书籍不会被当作“一篇很长的论文”。

预计共享：

```text
SourceDocument
SourceBlock
EvidenceSpan
Claim
UserNote
ReviewDecision
```

但书籍使用自己的理解结构，例如：

```text
central_thesis
chapter_argument
concept
argument
supporting_example
counterargument
important_quote
author_position
```

目标输出是可审计的 **Book Reading Report**，并保留章节、位置和原文证据。

在书籍能力开始实现前，不进行大规模数据库 ontology 重构；先把 Desktop / API 边界稳定下来。

---

## Roadmap

### Milestone A — Desktop Foundation（下一阶段）

- 从 `ui.py` 抽出应用服务与本地 API；
- 建立版本化 localhost API；
- 保留旧 Web UI 作为过渡；
- 新建 Avalonia Desktop；
- 完成 Library / Reader / Evidence Inspector / Settings / Jobs；
- PDF 页码跳转和证据定位形成完整桌面闭环；
- 完成 Windows-first 开发版打包。

### Milestone B — Paper Reader Completion

- Human review loop：确认 / 修正 claim 与 semantic support；
- 用户批注与证据联动；
- Eval Harness 与回归集；
- 改善 PDF/Claim/Evidence 并排阅读体验。

### Milestone C — Book Understanding v1

- EPUB / PDF 书籍导入；
- 章节树；
- 全书中心论题、章节论点、概念、案例、反论点、重要引文；
- Book Reading Report；
- 书籍证据定位与用户笔记。

### Milestone D — Research Projects

- Project / Reading List / Tag / Reading Status；
- 论文与书籍统一加入研究项目；
- Project-level research question；
- normalized Claim / Evidence memory；
- Evidence Matrix。

### Milestone E — Synthesis

- 多文献共识 / 冲突 / scope condition / 方法差异；
- Debate / Concept / Theory 关系；
- Research Gap / Puzzle 候选；
- External challenge layer / citation context。

### Milestone F — Literature Review & Writing

```text
Approved Evidence Matrix
  → Outline
  → Human Approval
  → Draft
  → Citation / Evidence Check
  → Human Revision
```

不提供“一键生成即可提交”的产品承诺。

---

## 当前边界

- 当前核心能力仍以论文为主；
- 论文身份元数据目前主要来自 PDF / layout recovery；外部 authority resolver 仍是后续能力；
- `SemanticSupportStatus` 是模型辅助审读结果，不是统计学证明；
- 完整 normalized research memory、跨论文 Evidence Matrix、书籍理解和文献综述尚未完成；
- 当前 Web UI 是过渡产品界面；
- Avalonia Desktop 与版本化 Local API 尚未实现。

---

## 核心原则

1. **Evidence First**：重要判断必须能回到来源页、block 或 evidence span。
2. **Traceability ≠ Entailment**：找到原文不等于原文充分支持结论。
3. **来源分离**：作者陈述、AI 推断、用户笔记不能混为一类。
4. **确定性任务由代码做**：页码、source ID、证据 token、去重、缓存和 schema 边界由程序校验。
5. **UI 不拥有研究逻辑**：桌面端只通过稳定应用接口访问研究引擎。
6. **Paper / Book 使用不同阅读镜头，共享 Evidence 基础设施。**
7. **先形成可审计研究记忆，再做多文献生成。**

项目核心目标仍然是：

> **可审计的学术理解，而不是更方便的摘要。**
