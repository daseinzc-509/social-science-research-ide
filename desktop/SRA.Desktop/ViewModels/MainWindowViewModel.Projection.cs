using System.Collections.ObjectModel;
using System.Text.Json;
using SRA.Desktop.Models;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// PaperDetail / PaperCard 到各研究阅读维度集合的数据投影与清理逻辑。
/// </summary>
public partial class MainWindowViewModel
{
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
            SelectedStudyType = "未分析";
            SelectedAnalysisState = "等待分析";
            AbstractText = "这篇 PDF 已导入并解析，但还没有 Paper Card。";
            RawCardJson = "尚无 Paper Card。";
            return;
        }

        RawCardJson = JsonSerializer.Serialize(card, new JsonSerializerOptions { WriteIndented = true });
        SelectedAnalysisState = "可审计分析已完成";
        ProjectMetadata(card);
        ProjectStudyProfile(card);
        ProjectClaims(card);
        ProjectTables(card);
        ProjectVisuals(card);
        ProjectWarnings(card);
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
        var metadataClaims = ParseClaims(card, "metadata_claims");
        var basicFacts = ParseClaims(card, "basic_facts");
        var analysis = ParseClaims(card, "analysis");
        var limitations = ParseClaims(card, "limitations");
        var recommendation = ParseSingleClaim(card, "reading_recommendation");

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
                var claimId = JsonRead.Text(raw, "claim_id");
                if (!string.IsNullOrWhiteSpace(claimId) && auditById.TryGetValue(claimId, out var audit))
                {
                    findings.Add(MergeAudit(baseItem, audit));
                }
                else
                {
                    findings.Add(baseItem);
                }
            }
        }

        Replace(MajorFindings, findings);
        Replace(AuthorExplanations, basicFacts.Where(item => item.FieldName == "author_explanation"));
        Replace(AuthorLimitations, basicFacts.Where(item => item.FieldName == "research_limitations"));
        Replace(AnalysisClaims, analysis);
        Replace(AiLimitations, limitations);
        Replace(ReadingRecommendations, recommendation is null ? [] : [recommendation]);
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
