# Sociology Research Agent / Social Science Research IDE

## Project Design Brief v0.4

本文件定义项目当前产品目标、架构边界和阶段路线图。v0.4 的核心变化是：项目已完成“薄 CLI 验证研究引擎”的早期阶段，进入 **Desktop Foundation**；桌面端采用 Avalonia，Python 研究引擎通过本地版本化 API 暴露能力。

最高优先级原则：

> 先构建可信的研究理解，再构建漂亮的研究工作台；界面通过稳定边界访问研究引擎，不把研究逻辑复制进 UI。

---

## 1. 产品定位

项目长期定位为 **Social Science Research IDE**：一个 local-first、evidence-first 的个人研究阅读环境。

它帮助研究者完成：

```text
发现 / 收集文献
  → 阅读
  → 可审计的结构化理解
  → 用户核查与修正
  → 项目级研究记忆
  → 比较 / 争论 / Evidence Matrix
  → 形成问题与论证
  → 文献综述 / 写作支持
```

它不是“一键搜索并自动写综述”的产品。AI 提供提取、比较、审读和候选综合，人类研究者决定理论意义、研究问题和最终论证。

---

## 2. 当前已验证能力：Paper Understanding v2

当前单篇论文链路为：

```text
PDF
  → Docling / PyMuPDF
  → SourceBlock + provenance
  → Evidence ledger
  → Lite factual index
  → deterministic metadata/evidence verification
  → study-type routing
  → Pro independent full-document retrieval
  → StudyProfile / ClaimAudit / methodological review
  → PaperCard
  → Deep Reading Report
```

核心原则：

- Evidence First；
- Traceability 与 Semantic Support 分开；
- 作者陈述、AI 推断、用户笔记分开；
- 确定性任务由代码处理；
- Pro 可以独立回源，不受 Lite 已选 evidence 的封闭世界限制；
- 论文类型不同，方法学审读 rubric 不同；
- Paper Card 当前是兼容视图，不是最终 ontology。

---

## 3. 为什么现在需要架构调整

早期设计故意避免公共 API 和桌面 IDE，以验证研究引擎是否值得继续开发。这个目标已经基本达成。

当前下一阶段需求包括：

- Avalonia 桌面 UI；
- PDF 阅读与 Claim/Evidence 联动；
- 长任务进度；
- 模型设置；
- 未来书籍阅读；
- 项目与多文献比较。

因此产品现在需要一个稳定的 **Presentation Boundary**。

架构调整不是重写 Python，而是增加：

```text
Avalonia Desktop
  → Local Application API
  → Application Services
  → existing Python Research Core
```

---

## 4. Desktop Foundation 架构

### 4.1 层次

```text
Desktop Presentation
  Avalonia / MVVM
        ↓
Local API
  localhost / versioned DTO / job status
        ↓
Application Services
  import / analyze / export / settings / review
        ↓
Research Core
  parser / analyzer / references / reports
        ↓
Infrastructure
  SQLite / files / model providers
```

### 4.2 桌面端规则

- Avalonia 不直接访问 SQLite；
- Avalonia 不直接调用 Docling；
- Avalonia 不依赖 analyzer 内部状态；
- API DTO 与 Pydantic domain model 分开；
- 后端只监听 loopback；
- 分析等长任务使用 job API；首版使用 polling；
- 后端进程由桌面端启动和关闭；
- Python 研究引擎保持独立可测试、可 CLI 调用。

### 4.3 API 技术选择

当前项目已经进入产品化本地 API 的合理时点。FastAPI 可作为适配层候选，原因是：

- 与 Pydantic schema 自然结合；
- 自动 OpenAPI；
- 便于生成或维护 C# typed client；
- route / validation / error handling 比手写 HTTP server 更适合长期维护。

这不代表研究逻辑依赖 FastAPI。API 只是 adapter。

---

## 5. 当前 Python 模块的演进

现有模块继续保留：

```text
parser.py
analyzer.py
models.py
repository.py
library.py
references.py
llm_client.py
config.py
cli.py
```

新增：

```text
services/
  analysis_service.py
  library_service.py
  settings_service.py
  export_service.py

api/
  app.py
  schemas.py
  routes/

legacy_web/
  app.py
```

第一轮不进行全面 package rename。优先把 `ui.py` 中的业务编排移入 services，再让 legacy web 和 Avalonia 共用同一应用层。

---

## 6. 模型服务边界

Lite 与 Pro 是独立 stage，应允许使用不同模型供应商：

```text
LiteConnection
  api_key
  base_url
  model

ProConnection
  api_key
  base_url
  model
```

环境变量：

```text
SRA_LITE_API_KEY
SRA_LITE_API_BASE_URL
SRA_LITE_MODEL

SRA_PRO_API_KEY
SRA_PRO_API_BASE_URL
SRA_PRO_MODEL
```

旧的 `SRA_API_KEY / SRA_API_BASE_URL` 作为兼容 fallback。

这仍然不是“多供应商插件系统”：当前只需要 OpenAI-compatible 的统一轻量 client + 两套独立连接。

---

## 7. Desktop v1 产品界面

第一版 Avalonia 采用三栏研究工作台：

```text
Library      Reader                    Inspector
Papers       PDF                       Claim
Books        Deep Reading              Evidence
Projects     later: Book Reader        Scope
Reading                               Semantic Support
                                      Notes / Review
```

首个垂直切片必须打通：

1. 查看论文库；
2. 导入 PDF；
3. 运行分析并看到进度；
4. 阅读 Paper Understanding / Deep Reading；
5. 点击 Claim → Evidence → PDF page/bbox；
6. 修改模型设置；
7. 导出结果。

只有这个闭环稳定后，才扩大到书籍。

---

## 8. Human Review 与评估

“可审计”必须逐渐从机器状态升级为研究者闭环。

需要增加：

```text
ReviewDecision
  claim_id
  user_status: confirmed | corrected | rejected | unresolved
  corrected_statement
  note
  updated_at
```

用户修正不能在重新分析时被覆盖。

同时建立 Eval Harness：

- 多种论文类型；
- claim / scope / semantic support / method-risk 人工参考；
- parser / model / prompt version；
- failure cases；
- 与“直接把 PDF 交给通用 LLM”的对照。

---

## 9. 书籍阅读：Book Understanding v1

书籍不是“一篇很长的论文”。

### 9.1 共享基础

未来增加：

```text
SourceDocument
  kind: PAPER | BOOK | BOOK_CHAPTER | REPORT

DocumentSection
SourceBlock
EvidenceSpan
Claim
UserNote
ReviewDecision
```

### 9.2 书籍专属理解结构

```text
central_thesis
chapter_argument
concept
definition
argument
supporting_example
counterargument
important_quote
author_position
```

输出为 Book Reading Report，而不是复用 Paper method-review 模板。

首版重点：

- EPUB / PDF；
- 目录与章节树；
- 全书中心论题；
- 章节论点；
- 核心概念；
- 论据 / 案例 / 反论点；
- 重要引文；
- 原文位置；
- 用户笔记。

---

## 10. Research Projects

论文和书籍通过 Project 聚合：

```text
Project
  research_question
  reading_list
    papers
    books / chapters
  notes
  evidence_matrix
  synthesis
```

Project 阶段才引入真正跨文档的 normalized research memory：

```text
Claim
ClaimEvidenceLink
ProjectDocument
ResearchQuestionVersion
ReviewDecision
```

不要在 Book/Desktop 之前提前设计完整 ontology。

---

## 11. 多文献综合

多文献阶段首先输出 Evidence Matrix，不直接生成长综述。

比较维度包括：

- population / setting / time；
- theory / concept；
- method / identification；
- finding / mechanism；
- semantic support；
- scope condition；
- methodological risk；
- contradiction / qualification。

然后再形成：

- 共识；
- 冲突；
- 理论关系；
- 方法差异；
- 研究缺口；
- Puzzle。

所有综合项仍需链接回各文献 Claim / Evidence。

---

## 12. 写作与文献综述

写作输入必须来自经研究者审查的研究记忆：

```text
Approved Evidence Matrix
  → Outline
  → Human Approval
  → Draft
  → Citation / Evidence Check
  → Human Revision
```

最终重要句子应可追踪：

```text
Draft sentence
  → synthesis claim
  → source claim
  → evidence
  → source document
  → page / location / quote
```

不提供“一键生成即可提交”的承诺。

---

## 13. 数据库演进

当前继续使用 SQLite。

进入 Book / Project milestone 前增加显式 schema migration 机制。可使用简单 SQL migration + `schema_version`，不需要为了迁移而强制引入 ORM。

原则：

- 原 PDF / EPUB 永远保留；
- parser/model 派生产物可重新构建；
- user note / review decision 不因模型重跑而丢失；
- schema 变更必须有版本和迁移路径。

---

## 14. 分阶段路线图

### Milestone A — Desktop Foundation

- 抽离 application services；
- 建立 `/api/v1` local API；
- legacy Web UI 改为共用 services；
- Avalonia Desktop shell；
- Library / Reader / Evidence Inspector / Settings / Jobs；
- Windows-first 开发版打包。

### Milestone B — Paper Reader Completion

- Claim/Evidence 与 PDF 跳转 / 高亮；
- Human Review Loop；
- Eval Harness；
- 更完整的用户笔记与修正。

### Milestone C — Book Understanding v1

- EPUB/PDF 书籍；
- 章节树；
- Book-specific claim schema；
- Book Reading Report。

### Milestone D — Research Projects

- Project / Reading List；
- Research Question versions；
- normalized cross-document Claim/Evidence；
- Evidence Matrix。

### Milestone E — Literature Synthesis

- consensus / conflict / scope / debate；
- external metadata and citation context；
- Gap / Puzzle candidates。

### Milestone F — Literature Review & Writing

- evidence-backed outline；
- draft；
- citation/evidence check；
- human revision。

---

## 15. 当前不做

```text
重写 Python research core 为 C#
PostgreSQL / cloud sync / multi-user auth
LangGraph 多 Agent 编排
Neo4j / GraphRAG
独立 Vector DB（没有评估前）
复杂完整 ontology
自动全网搜文献并一键生成可提交综述
```

这些能力必须由真实需求和评估结果证明其必要性。

---

## 16. 当前开发任务

当前任务从旧版“只完成 Milestone 0/1”调整为：

> **在不破坏 Paper Understanding v2 的前提下建立 Desktop Foundation。**

实现顺序：

```text
1. ui.py 业务逻辑抽离
2. Application Services
3. Local API + contract tests
4. Avalonia Library / Reader / Inspector
5. PDF evidence navigation
6. model settings / jobs / exports
7. Windows-first packaging
8. Human Review + Eval Harness
9. Book Understanding
```

通过标准不是 UI 控件数量，而是：

> 研究者能否从桌面工作台完成“导入 → 分析 → 阅读 → 检查证据 → 修正 → 保存”的可靠闭环。
