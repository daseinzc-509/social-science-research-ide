using System.Collections.ObjectModel;
using System.Text.Encodings.Web;
using System.Text.Json;
using SRA.Desktop.Models;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// PaperDetail / PaperCard 到各研究阅读维度集合的数据投影与清理逻辑。
/// </summary>
public partial class MainWindowViewModel
{
    private const int RawCardPreviewCharLimit = 20_000;

    private static readonly JsonSerializerOptions ReadableJsonOptions = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private string _fullRawCardJson = "";

    public string FullRawCardJson => string.IsNullOrEmpty(_fullRawCardJson) ? RawCardJson : _fullRawCardJson;

    partial void OnShowFullRawCardJsonChanged(bool value)
    {
        OnPropertyChanged(nameof(RawCardToggleButtonText));
        ApplyRawCardView();
    }

    public void ToggleFullRawCardView()
    {
        if (!IsRawCardTruncated) return;
        ShowFullRawCardJson = !ShowFullRawCardJson;
    }

    private void SetRawCardJsonSource(string fullText)
    {
        _fullRawCardJson = fullText;
        IsRawCardTruncated = fullText.Length > RawCardPreviewCharLimit;
        ShowFullRawCardJson = false;
        OnPropertyChanged(nameof(RawCardToggleButtonText));
        ApplyRawCardView();
    }

    private void ApplyRawCardView()
    {
        if (string.IsNullOrEmpty(_fullRawCardJson))
        {
            RawCardSummaryText = "尚未生成 Paper Card。";
            return;
        }
        var totalChars = _fullRawCardJson.Length;
        var approxKb = Math.Max(1, (int)Math.Round(totalChars / 1024.0));
        if (!IsRawCardTruncated || ShowFullRawCardJson)
        {
            RawCardJson = _fullRawCardJson;
            RawCardSummaryText = $"完整 Paper Card JSON（UTF-8 中文直显 · 约 {approxKb} KB · {totalChars:N0} 字符）";
            return;
        }
        var preview = _fullRawCardJson[..RawCardPreviewCharLimit];
        var lastNewLine = preview.LastIndexOf('\n');
        if (lastNewLine > RawCardPreviewCharLimit - 1000)
        {
            preview = preview[..lastNewLine];
        }
        RawCardJson = preview
            + "\n\n// ────────────────────────────────────────────────────────────\n"
            + $"// 已启用流畅预览（展示前 {preview.Length:N0} / 共 {totalChars:N0} 字符，约 {approxKb} KB），\n"
            + "// 防止超大只读文本框在点击与切换焦点时卡顿。\n"
            + "// 请使用上方「复制完整 JSON」「导出 JSON」或「展开完整 JSON」查看全部内容。\n"
            + "// ────────────────────────────────────────────────────────────";
        RawCardSummaryText = $"流畅预览模式（完整约 {approxKb} KB · {totalChars:N0} 字符，点击右侧可一键复制或导出完整 JSON）";
    }
    private void ProjectDetail(PaperDetailResponse detail)
    {
        References.Clear();
        foreach (var reference in detail.References)
        {
            if (reference.ValueKind == JsonValueKind.Object)
            {
                References.Add(ReferenceItem.FromJson(reference));
            }
        }
        ReferenceStatusText = References.Count == 0
            ? "尚未提取参考文献。"
            : $"已保存 {References.Count} 条参考文献，发现 {detail.CitationMentions.Count} 个正文引用位置。";

        if (detail.MetadataOverrides.ValueKind == JsonValueKind.Object)
        {
            var valuesCount = 0;
            if (detail.MetadataOverrides.TryGetProperty("values", out var values) && values.ValueKind == JsonValueKind.Object)
            {
                valuesCount = values.EnumerateObject().Count();
            }
            HasManualMetadata = valuesCount > 0;
            ManualMetadataNote = JsonRead.Text(detail.MetadataOverrides, "source_note");
        }

        if (detail.Card is not { ValueKind: JsonValueKind.Object } card)
        {
            ProjectNotesAndReviews(detail, []);
            SelectedStudyType = "未分析";
            SelectedAnalysisState = "等待分析";
            AbstractText = "这篇 PDF 已导入并解析，但还没有 Paper Card。";
            _fullRawCardJson = "";
            IsRawCardTruncated = false;
            ShowFullRawCardJson = false;
            RawCardJson = "尚无 Paper Card。";
            RawCardSummaryText = "尚无 Paper Card。";
            return;
        }

        SetRawCardJsonSource(JsonSerializer.Serialize(card, ReadableJsonOptions));
        SelectedAnalysisState = "可审计分析已完成";
        ProjectMetadata(card);
        ProjectStudyProfile(card);
        var activeIds = CollectCardClaimIds(card);
        ProjectNotesAndReviews(detail, activeIds);
        ProjectClaims(card);
        ProjectTables(card);
        ProjectVisuals(card);
        ProjectWarnings(card);
    }

    private static HashSet<string> CollectCardClaimIds(JsonElement card)
    {
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var group in new[] { "metadata_claims", "basic_facts", "analysis", "limitations" })
        {
            foreach (var item in ParseClaims(card, group))
            {
                if (!string.IsNullOrWhiteSpace(item.ClaimId)) ids.Add(item.ClaimId);
            }
        }
        var rec = ParseSingleClaim(card, "reading_recommendation");
        if (rec is not null && !string.IsNullOrWhiteSpace(rec.ClaimId))
        {
            ids.Add(rec.ClaimId);
        }
        return ids;
    }

    private void ProjectMetadata(JsonElement card)
    {
        if (!card.TryGetProperty("metadata", out var metadata) || metadata.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        MetadataTitle = JsonRead.Text(metadata, "title", SelectedTitle);
        var authors = JsonRead.StringList(metadata, "authors");
        MetadataAuthors = authors.Count == 0 ? "—" : string.Join("、", authors);
        MetadataJournal = JsonRead.Text(metadata, "journal", "—");
        MetadataYear = JsonRead.Text(metadata, "year", "—");
        var volume = JsonRead.Text(metadata, "volume");
        var issue = JsonRead.Text(metadata, "issue");
        MetadataVolumeIssue = string.IsNullOrWhiteSpace(volume) && string.IsNullOrWhiteSpace(issue)
            ? "—"
            : $"{(string.IsNullOrWhiteSpace(volume) ? "" : $"Vol. {volume}")}{(!string.IsNullOrWhiteSpace(volume) && !string.IsNullOrWhiteSpace(issue) ? " · " : "")}{(string.IsNullOrWhiteSpace(issue) ? "" : $"No. {issue}")}";
        MetadataPages = JsonRead.Text(metadata, "pages", "—");
        MetadataDoi = JsonRead.Text(metadata, "doi", "—");
        AbstractText = JsonRead.Text(metadata, "abstract", "未提取摘要。");
        Keywords.Clear();
        foreach (var keyword in JsonRead.StringList(metadata, "keywords"))
        {
            Keywords.Add(keyword);
        }
        OnPropertyChanged(nameof(HasKeywords));
    }

    private void ProjectStudyProfile(JsonElement card)
    {
        if (!card.TryGetProperty("study_profile", out var profile) || profile.ValueKind != JsonValueKind.Object)
        {
            SelectedStudyType = "unknown";
            return;
        }
        SelectedStudyType = JsonRead.Text(profile, "study_type", "unknown");
        SelectedStudyMethod = JsonRead.Text(profile, "method", "—");
        SelectedIdentification = JsonRead.Text(profile, "identification_strategy", "—");
        StudyRationale = JsonRead.Text(profile, "rationale");
        if (card.TryGetProperty("review_plan", out var reviewPlan) && reviewPlan.ValueKind == JsonValueKind.Array)
        {
            ReviewPlanText = string.Join(" · ", reviewPlan.EnumerateArray().Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : item.ToString()).Where(item => !string.IsNullOrWhiteSpace(item)));
        }
    }

    private void ProjectClaims(JsonElement card)
    {
        var metadataClaims = ParseClaims(card, "metadata_claims").Select(AttachHumanReview).ToList();
        var basicFacts = ParseClaims(card, "basic_facts").Select(AttachHumanReview).ToList();
        var analysis = ParseClaims(card, "analysis").Select(AttachHumanReview).ToList();
        var limitations = ParseClaims(card, "limitations").Select(AttachHumanReview).ToList();
        var recommendation = ParseSingleClaim(card, "reading_recommendation") is { } rec
            ? AttachHumanReview(rec)
            : null;

        Replace(MetadataClaims, metadataClaims);
        Replace(BasicFacts, basicFacts);
        Replace(ResearchQuestions, basicFacts.Where(item => item.FieldName == "research_question"));
        Replace(TheoryConcepts, basicFacts.Where(item => item.FieldName == "theory_concept"));
        Replace(ResearchDesignClaims, basicFacts.Where(item => item.FieldName is "research_method" or "research_sample" or "measurement_indicator"));

        var auditById = new Dictionary<string, JsonElement>();
        if (card.TryGetProperty("claim_audits", out var audits) && audits.ValueKind == JsonValueKind.Array)
        {
            foreach (var audit in audits.EnumerateArray())
            {
                var claimId = JsonRead.Text(audit, "claim_id");
                if (!string.IsNullOrWhiteSpace(claimId))
                {
                    auditById[claimId] = audit;
                }
            }
        }

        var findings = new List<ClaimItem>();
        if (card.TryGetProperty("basic_facts", out var rawFacts) && rawFacts.ValueKind == JsonValueKind.Array)
        {
            foreach (var raw in rawFacts.EnumerateArray())
            {
                if (JsonRead.Text(raw, "field_name") != "major_finding")
                {
                    continue;
                }
                var baseItem = ClaimItem.FromJson(raw);
                var claimId = baseItem.ClaimId;
                var merged = !string.IsNullOrWhiteSpace(claimId) && auditById.TryGetValue(claimId, out var audit)
                    ? MergeAudit(baseItem, audit)
                    : baseItem;
                findings.Add(AttachHumanReview(merged));
            }
        }

        Replace(MajorFindings, findings);
        Replace(AuthorExplanations, basicFacts.Where(item => item.FieldName == "author_explanation"));
        Replace(AuthorLimitations, basicFacts.Where(item => item.FieldName == "research_limitations"));
        Replace(AnalysisClaims, analysis);
        Replace(AiLimitations, limitations);
        Replace(ReadingRecommendations, recommendation is null ? [] : [recommendation]);
        RaiseProjectionSectionState();
    }

    private static ClaimItem MergeAudit(ClaimItem source, JsonElement audit)
    {
        var evidence = source.Evidence;
        if (audit.TryGetProperty("evidence", out var evidenceNode) && evidenceNode.ValueKind == JsonValueKind.Array)
        {
            var auditEvidence = evidenceNode.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object).Select(EvidenceItem.FromJson).ToList();
            if (auditEvidence.Count > 0)
            {
                evidence = auditEvidence;
            }
        }
        return new ClaimItem
        {
            ClaimId = source.ClaimId,
            FieldName = source.FieldName,
            FieldLabel = source.FieldLabel,
            Statement = source.Statement,
            Provenance = source.Provenance,
            Verification = source.Verification,
            ClaimType = source.ClaimType,
            Scope = source.Scope,
            SemanticSupport = JsonRead.Text(audit, "semantic_support", source.SemanticSupport),
            Rationale = JsonRead.Text(audit, "rationale", source.Rationale),
            Evidence = evidence,
            HumanReviewStatus = source.HumanReviewStatus,
            HumanCorrectedStatement = source.HumanCorrectedStatement,
            HumanReviewNote = source.HumanReviewNote,
        };
    }

    private static List<ClaimItem> ParseClaims(JsonElement card, string propertyName)
    {
        if (!card.TryGetProperty(propertyName, out var node) || node.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return node.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object).Select(ClaimItem.FromJson).ToList();
    }

    private static ClaimItem? ParseSingleClaim(JsonElement card, string propertyName)
    {
        if (!card.TryGetProperty(propertyName, out var node) || node.ValueKind != JsonValueKind.Object)
        {
            return null;
        }
        return ClaimItem.FromJson(node);
    }

    private void ClearProjection()
    {
        MetadataClaims.Clear();
        BasicFacts.Clear();
        ResearchQuestions.Clear();
        TheoryConcepts.Clear();
        ResearchDesignClaims.Clear();
        MajorFindings.Clear();
        AuthorExplanations.Clear();
        AuthorLimitations.Clear();
        AnalysisClaims.Clear();
        AiLimitations.Clear();
        ReadingRecommendations.Clear();
        References.Clear();
        Tables.Clear();
        OnPropertyChanged(nameof(HasNoTables));
        Formulas.Clear();
        foreach (var previous in Figures)
            previous.PreviewImage = null;
        Figures.Clear();
        OnPropertyChanged(nameof(HasNoVisuals));
        Keywords.Clear();
        SelectedStudyType = "—";
        SelectedStudyMethod = "—";
        SelectedIdentification = "—";
        StudyRationale = "";
        SelectedFactsCount = "—";
        SelectedEvidenceCount = "—";
        SelectedReviewsCount = "—";
        MetadataTitle = "—";
        MetadataAuthors = "—";
        MetadataJournal = "—";
        MetadataYear = "—";
        MetadataVolumeIssue = "—";
        MetadataPages = "—";
        MetadataDoi = "—";
        AbstractText = "尚未提取摘要。";
        WarningsText = "";
        WarningsSummary = "暂无提取警告";
        HasWarnings = false;
        HasManualMetadata = false;
        ManualMetadataNote = "";
        ReviewPlanText = "—";
        ReferenceStatusText = "尚未提取参考文献。";
        _fullRawCardJson = "";
        IsRawCardTruncated = false;
        ShowFullRawCardJson = false;
        RawCardSummaryText = "尚未选择论文。";
        ClearNotesAndReviews();
        RaiseProjectionSectionState();
    }

    private void RaiseProjectionSectionState()
    {
        OnPropertyChanged(nameof(HasKeywords));
        OnPropertyChanged(nameof(HasMetadataClaims));
        OnPropertyChanged(nameof(HasBasicFacts));
        OnPropertyChanged(nameof(HasResearchQuestions));
        OnPropertyChanged(nameof(HasTheoryConcepts));
        OnPropertyChanged(nameof(HasResearchDesignClaims));
        OnPropertyChanged(nameof(HasMajorFindings));
        OnPropertyChanged(nameof(HasAuthorExplanations));
        OnPropertyChanged(nameof(HasAuthorLimitations));
        OnPropertyChanged(nameof(HasAnalysisClaims));
        OnPropertyChanged(nameof(HasAiLimitations));
        OnPropertyChanged(nameof(HasReadingRecommendations));
        OnPropertyChanged(nameof(MetadataClaimsCount));
        OnPropertyChanged(nameof(BasicFactsCount));
        OnPropertyChanged(nameof(ResearchQuestionsCount));
        OnPropertyChanged(nameof(TheoryConceptsCount));
        OnPropertyChanged(nameof(ResearchDesignCount));
        OnPropertyChanged(nameof(MajorFindingsCount));
        OnPropertyChanged(nameof(AuthorExplanationsCount));
        OnPropertyChanged(nameof(AuthorLimitationsCount));
        OnPropertyChanged(nameof(AnalysisClaimsCount));
        OnPropertyChanged(nameof(AiLimitationsCount));
        OnPropertyChanged(nameof(ReadingRecommendationsCount));
    }

    private static void Replace<T>(ObservableCollection<T> target, IEnumerable<T> values)
    {
        target.Clear();
        foreach (var value in values)
        {
            target.Add(value);
        }
    }
}
