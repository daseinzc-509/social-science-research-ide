# Social Science Research IDE / Sociology Research Agent

这是一个 **local-first、evidence-first** 的社会科学论文阅读与研究记忆原型。它的目标不是把 PDF “总结得更长”，而是把论文中的**主张、证据、适用范围、语义支持关系和方法学风险**拆开保存，使研究者能够回到原文检查、比较和修正理解。

当前主线是 **Paper Understanding v2**：Docling-first PDF 解析 → Lite 事实索引 → 独立 Pro 回源审读 → 可审计 Paper Card → 精读报告视图。

## 当前能力

- 单篇 / 批量 PDF 导入，本地 SQLite 存储；
- Docling-first 解析，保留 page / bbox / block provenance；PyMuPDF 作为后备；
- 文本质量检测与必要时 OCR；异常文本不会静默当作正常解析；
- Lite 提取元数据与基础研究事实；
- 元数据 layout recovery：front matter、重复页眉、页码范围等确定性恢复；
- `title / authors / journal / DOI` 使用 metadata provenance 校验，不再要求整个值必须塞进一小段普通正文 quote；
- Paper Understanding v2：Study Profile、Claim Type、Claim Scope、Evidence Role、Semantic Support、Claim Audit；
- Pro **独立从整篇 SourceBlock 定向取证**，不只读取 Lite 已引用的片段；
- 按实验、观察性定量、质性、理论、混合方法、综述切换方法学审读维度；
- UI 显示“来源已定位”和“语义支持”两层状态；
- **精读报告**：研究问题、研究设计、核心发现、作者解释、作者自述局限、AI 方法学审读、Claim/Evidence 审计；
- Paper Card JSON、解析文本、参考文献 JSON / Zotero BibTeX、精读 Markdown 导出；
- 本地模型缓存、仓库 doctor、手动元数据修订和重新解析。

## 设计原则

1. **Evidence First**：重要判断必须能回到 PDF 页、block 或 evidence span。
2. **Traceability ≠ Entailment**：`SUPPORTED` 只表示来源已定位；`SemanticSupportStatus` 单独表达原文是支持、部分支持、限定、冲突、背景还是不清楚。
3. **来源分离**：作者陈述、AI 推断、用户笔记不能混为一类。
4. **确定性任务由代码做**：页码、source ID、证据 token、去重、缓存和 schema 边界由程序校验。
5. **Paper Card 是视图，不是最终 ontology**：当前保持向后兼容；未来可将 Claim / Evidence / StudyProfile 进一步规范化存储。

## 快速开始

```powershell
conda create -p .conda-env --override-channels -c conda-forge python=3.12 "pydantic>=2.7,<3" "pymupdf>=1.24,<2" pip
conda run -p .conda-env python -m pip install --no-build-isolation -e .
conda run -p .conda-env python -m pip install "docling>=2.131,<3"
```

项目根目录 `.env`：

```text
SRA_API_KEY=...
SRA_API_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
SRA_LITE_MODEL=doubao-seed-2.1-lite
SRA_PRO_MODEL=doubao-seed-2.1-pro
```

启动 UI：

```powershell
conda run --no-capture-output -p .conda-env sra ui
```

常用 CLI：

```powershell
conda run -p .conda-env sra import-pdf "D:\papers\example.pdf"
conda run -p .conda-env sra import-pdf-dir "D:\papers" --json
conda run --no-capture-output -p .conda-env sra analyze PAPER_ID
conda run -p .conda-env sra export-card PAPER_ID --output paper-card.json
conda run -p .conda-env sra export-text PAPER_ID --output parsed-paper.txt
conda run -p .conda-env sra extract-references PAPER_ID --output references.json
conda run -p .conda-env sra export-references PAPER_ID --output references.bib --format bibtex
```

UI 中分析完成后可以直接点击 **“导出精读报告”**，得到 `*-deep-reading.md`。

## Lite → Pro 数据流

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

## 两层“支持”状态

### 1. VerificationStatus：来源是否可定位

- `SUPPORTED`：来源已定位；
- `NEEDS_REVIEW`：存在来源问题或该项本身为 AI 推断，需要人工复核；
- `NO_SOURCE`：没有来源引用。

### 2. SemanticSupportStatus：原文与主张的语义关系

- `SUPPORTS`
- `PARTIALLY_SUPPORTS`
- `QUALIFIES`
- `CONTRADICTS`
- `BACKGROUND_ONLY`
- `UNCLEAR`

因此可以合法出现：**“来源已定位，但原文只部分支持 / 限定 / 反驳该主张。”**

## Pro schema 稳定性

Pro 模型输出位于外部模型边界，允许出现无害的可选字段漂移。`ProReviewItem`、`LLMClaimAudit` 和 `ProAnalysis` 会忽略未知展示字段，并兼容旧式 `field_name / statement` 形状；真正的 evidence token、PDF provenance 和 claim ID 仍由程序严格校验。

这解决了一类典型错误：模型返回 `rationale: null` 或其他额外说明字段时，不应让整次已经完成的 Pro 调用因为 `extra_forbidden` 而丢失。

## Ark completion budget

`AnalysisConfig` 默认：

```python
lite_max_completion_tokens = 4000
pro_max_completion_tokens = 16000
```

Chat Completions 使用 `max_completion_tokens`。Pro 开启 thinking 时，该额度同时覆盖推理与最终回答；若服务返回 `finish_reason=length`，客户端会拒绝保存截断 JSON。

## 精读报告

精读报告不是第三次模型调用，而是 saved `PaperCard` 的确定性视图。它包含：

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

这样做是为了避免“摘要的摘要”继续损失证据链。

## 缓存与版本

模型缓存 key 同时包含 prompt version、模型、thinking 配置、system prompt 和 user prompt。只要 schema 或 prompt 实际变化，旧缓存就不会被误当作兼容结果。

PDF parser version 变化时，系统会重新解析并清除该论文失效的 Paper Card / model cache；原 PDF 与用户笔记保留。

## 当前边界

- 论文身份元数据目前仍主要来自 PDF / layout recovery；未来可增加 DOI / Crossref / CNKI authority resolver；
- `SemanticSupportStatus` 仍是模型辅助审读结果，不是统计学“证明”；
- 还没有完整的跨论文 normalized research memory；
- 引用上下文 / 后续文献支持与反驳层仍属于后续阶段；
- 单篇论文不能自行证明其“领域创新性”。

## 下一步优先级

1. **Eval Harness**：为不同论文类型建立人工标注的 claim / scope / support / method-risk 回归集；
2. **Human review loop**：允许用户确认、修正 claim audit，并把修正持久化；
3. **Evidence Matrix**：在多篇论文间比较 population、method、claim、scope condition 与 semantic support；
4. **External challenge layer**：接入 citation context / DOI metadata 后，再做外部支持、限定与反驳。

项目的核心目标仍然是：**可审计的学术理解，而不是更方便的摘要。**
