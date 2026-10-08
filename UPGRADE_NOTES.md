# Paper Understanding v2 升级说明

这次升级不改 PDF parser 主链路，也不会回滚现有的乱码/OCR/metadata 修复；主要改动位于：

- `src/sociology_research/models.py`
- `src/sociology_research/analyzer.py`
- `src/sociology_research/config.py`
- `src/sociology_research/ui.py`
- `src/sociology_research/cli.py`
- `README.md`

## 安装方式

最稳妥：先备份项目，然后用包中的对应文件覆盖上述文件；或者在项目根目录应用 `paper_understanding_v2.patch`。

更新后重启本地 UI。**不需要重建 PDF 解析**，因为本次没有修改 parser version。对已有论文点一次“重新分析”即可生成 v2 Card；新的 prompt version 会自动使用新的模型缓存键。

## 新增结果

重新分析后的 Paper Card 会新增：

- `claim_id`
- `claim_type`
- `scope`
- `semantic_support`
- `support_note`
- evidence `role`
- `study_profile`
- `claim_audits`
- `review_plan`

旧 Card 仍能读取，但这些字段会使用兼容默认值；只有重新分析后才会获得真实的 v2 内容。

## 验证

本包附带 `tests/test_paper_understanding_v2.py`。测试覆盖：旧 Card 向后兼容、六类研究路由、mixed methods、claim type/scope/evidence role、Pro 独立回源、traceability 与 semantic support 分离、StudyProfile 修正 router、未知 audit 防护、无 Lite facts 时 Pro 仍运行、端到端语义审计、SQLite round-trip、模型枚举容错。
