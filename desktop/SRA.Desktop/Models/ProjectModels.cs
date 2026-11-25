using System.Text.Json;

namespace SRA.Desktop.Models;

/// <summary>
/// 研究专题中单篇论文的阅读进度选项。
/// </summary>
public sealed record ProjectReadingStatusOption(string Value, string Label)
{
    public static IReadOnlyList<ProjectReadingStatusOption> All { get; } =
    [
        new("queued", "待读"),
        new("reading", "在读"),
        new("reviewed", "已复核"),
        new("core", "核心文献"),
    ];

    public static string ToLabel(string? status) => (status ?? "").Trim().ToLowerInvariant() switch
    {
        "reading" => "在读",
        "reviewed" => "已复核",
        "core" => "核心文献",
        _ => "待读",
    };

    public override string ToString() => Label;
}

/// <summary>
/// 新建/编辑研究专题弹窗返回结果。
/// </summary>
public sealed record ProjectEditorResult(string Name, string ResearchQuestion, string Description);

/// <summary>
/// 专题级跨文献研究笔记（MY NOTE / USER_NOTE）。
/// </summary>
public sealed class ProjectNoteItem
{
    public string Id { get; init; } = "";
    public string ProjectId { get; init; } = "";
    public string Title { get; init; } = "";
    public string Note { get; init; } = "";
    public string CreatedAt { get; init; } = "";
    public string UpdatedAt { get; init; } = "";

    public bool HasTitle => !string.IsNullOrWhiteSpace(Title);
    public string DisplayTitle => HasTitle ? Title : "专题研究笔记";
    public string TimeLabel
    {
        get
        {
            var iso = string.IsNullOrWhiteSpace(UpdatedAt) ? CreatedAt : UpdatedAt;
            if (DateTimeOffset.TryParse(iso, out var dto))
            {
                return dto.ToLocalTime().ToString("yyyy-MM-dd HH:mm");
            }
            return string.IsNullOrWhiteSpace(iso) ? "" : iso[..Math.Min(16, iso.Length)];
        }
    }

    public static ProjectNoteItem FromJson(JsonElement node) => new()
    {
        Id = JsonRead.Text(node, "id"),
        ProjectId = JsonRead.Text(node, "project_id"),
        Title = JsonRead.Text(node, "title"),
        Note = JsonRead.Text(node, "note"),
        CreatedAt = JsonRead.Text(node, "created_at"),
        UpdatedAt = JsonRead.Text(node, "updated_at"),
    };
}

/// <summary>
/// 证据矩阵单条主张附带的可点击原文页码引用。
/// </summary>
public sealed class MatrixCitationItem
{
    public string PaperId { get; init; } = "";
    public string PaperTitle { get; init; } = "";
    public string SourceBlockId { get; init; } = "";
    public int PageNumber { get; init; }
    public string Quote { get; init; } = "";

    public bool HasValidPage => PageNumber > 0;
    public string BadgeLabel => PageNumber > 0 ? $"PDF p.{PageNumber} →" : "查看原文";
    public string TooltipText => string.IsNullOrWhiteSpace(Quote)
        ? $"跳转到 {PaperTitle} 第 {PageNumber} 页"
        : $"PDF 第 {PageNumber} 页：{Quote}";

    public static MatrixCitationItem FromJson(JsonElement node, string paperId, string paperTitle)
    {
        var page = 0;
        if (node.TryGetProperty("page_number", out var pNode) && pNode.ValueKind == JsonValueKind.Number)
        {
            pNode.TryGetInt32(out page);
        }
        return new MatrixCitationItem
        {
            PaperId = paperId,
            PaperTitle = paperTitle,
            SourceBlockId = JsonRead.Text(node, "source_block_id"),
            PageNumber = page,
            Quote = JsonRead.Text(node, "quote"),
        };
    }
}

/// <summary>
/// 证据矩阵中的单条结构化主张（严格保留 AUTHOR / AI_INFERRED 来源与语义审计状态）。
/// </summary>
public sealed class MatrixClaimItem
{
    public string ClaimId { get; init; } = "";
    public string FieldName { get; init; } = "";
    public string Statement { get; init; } = "";
    public string Provenance { get; init; } = "";
    public string Verification { get; init; } = "";
    public string SemanticSupport { get; init; } = "";
    public string HumanReviewStatus { get; init; } = "";
    public string HumanCorrectedStatement { get; init; } = "";
    public string HumanReviewNote { get; init; } = "";
    public List<MatrixCitationItem> Citations { get; init; } = [];

    public bool IsAuthor => string.Equals(Provenance, "AUTHOR_STATED", StringComparison.OrdinalIgnoreCase);
    public bool IsAi => string.Equals(Provenance, "AI_INFERRED", StringComparison.OrdinalIgnoreCase);
    public string SourceTag => IsAuthor ? "AUTHOR" : (IsAi ? "AI REVIEW" : Provenance);
    public bool HasCitations => Citations.Count > 0;
    public bool HasHumanReview => !string.IsNullOrWhiteSpace(HumanReviewStatus);
    public string HumanReviewBadgeLabel => ClaimReviewItem.StatusToLabel(HumanReviewStatus);
    public bool IsHumanConfirmed => string.Equals(HumanReviewStatus, "confirmed", StringComparison.OrdinalIgnoreCase);
    public bool IsHumanCorrected => string.Equals(HumanReviewStatus, "corrected", StringComparison.OrdinalIgnoreCase);
    public bool IsHumanRejected => string.Equals(HumanReviewStatus, "rejected", StringComparison.OrdinalIgnoreCase);
    public bool IsHumanUnresolved => string.Equals(HumanReviewStatus, "unresolved", StringComparison.OrdinalIgnoreCase);
    public bool HasHumanCorrectedStatement => !string.IsNullOrWhiteSpace(HumanCorrectedStatement);

    public string AuditBadgeLabel
    {
        get
        {
            var semantic = (SemanticSupport ?? "").Trim().ToUpperInvariant();
            if (!string.IsNullOrEmpty(semantic))
            {
                return semantic switch
                {
                    "SUPPORTS" => "语义支持",
                    "PARTIALLY_SUPPORTS" => "部分支持",
                    "QUALIFIES" => "附带限定",
                    "CONTRADICTS" => "存在冲突",
                    "BACKGROUND_ONLY" => "仅背景提及",
                    "UNCLEAR" => "支持度待核",
                    _ => semantic,
                };
            }
            return (Verification ?? "").Trim().ToUpperInvariant() switch
            {
                "SUPPORTED" => "已溯源",
                "NEEDS_REVIEW" => "待核验",
                "NO_SOURCE" => "无原文出处",
                _ => "",
            };
        }
    }

    public bool HasAuditBadge => !string.IsNullOrWhiteSpace(AuditBadgeLabel);
    public bool IsAuditPositive => string.Equals(SemanticSupport, "SUPPORTS", StringComparison.OrdinalIgnoreCase)
        || (string.IsNullOrWhiteSpace(SemanticSupport) && string.Equals(Verification, "SUPPORTED", StringComparison.OrdinalIgnoreCase));
    public bool IsAuditNegative => string.Equals(SemanticSupport, "CONTRADICTS", StringComparison.OrdinalIgnoreCase);
    public bool IsAuditWarning => HasAuditBadge && !IsAuditPositive && !IsAuditNegative;

    public static MatrixClaimItem FromJson(JsonElement node, string paperId, string paperTitle)
    {
        var citations = new List<MatrixCitationItem>();
        if (node.TryGetProperty("citations", out var cNode) && cNode.ValueKind == JsonValueKind.Array)
        {
            foreach (var c in cNode.EnumerateArray())
            {
                if (c.ValueKind == JsonValueKind.Object)
                {
                    citations.Add(MatrixCitationItem.FromJson(c, paperId, paperTitle));
                }
            }
        }
        var humanStatus = "";
        var humanCorrected = "";
        var humanNote = "";
        if (node.TryGetProperty("human_review", out var hr) && hr.ValueKind == JsonValueKind.Object)
        {
            humanStatus = JsonRead.Text(hr, "user_status");
            humanCorrected = JsonRead.Text(hr, "corrected_statement");
            humanNote = JsonRead.Text(hr, "note");
        }
        return new MatrixClaimItem
        {
            ClaimId = JsonRead.Text(node, "claim_id"),
            FieldName = JsonRead.Text(node, "field_name"),
            Statement = JsonRead.Text(node, "statement"),
            Provenance = JsonRead.Text(node, "provenance"),
            Verification = JsonRead.Text(node, "verification"),
            SemanticSupport = JsonRead.Text(node, "semantic_support"),
            HumanReviewStatus = humanStatus,
            HumanCorrectedStatement = humanCorrected,
            HumanReviewNote = humanNote,
            Citations = citations,
        };
    }
}

/// <summary>
/// 跨文献 Evidence Matrix 中的单篇论文对比行（零模型调用，100% 确定性投影）。
/// </summary>
public sealed class ProjectMatrixRowItem
{
    public string PaperId { get; init; } = "";
    public string Title { get; init; } = "";
    public List<string> Authors { get; init; } = [];
    public int? Year { get; init; }
    public string Journal { get; init; } = "";
    public string ReadingStatus { get; init; } = "queued";
    public string ProjectNote { get; init; } = "";
    public bool HasCard { get; init; }

    public string StudyType { get; init; } = "";
    public string Method { get; init; } = "";
    public string IdentificationStrategy { get; init; } = "";
    public string Population { get; init; } = "";
    public string DataSource { get; init; } = "";

    public List<MatrixClaimItem> ResearchQuestions { get; init; } = [];
    public List<MatrixClaimItem> TheoriesAndConcepts { get; init; } = [];
    public List<MatrixClaimItem> ResearchDesign { get; init; } = [];
    public List<MatrixClaimItem> MajorFindings { get; init; } = [];
    public List<MatrixClaimItem> AuthorExplanations { get; init; } = [];
    public List<MatrixClaimItem> AuthorLimitations { get; init; } = [];
    public List<MatrixClaimItem> AiLimitations { get; init; } = [];

    public string ReadingStatusLabel => ProjectReadingStatusOption.ToLabel(ReadingStatus);
    public bool IsCoreStatus => string.Equals(ReadingStatus, "core", StringComparison.OrdinalIgnoreCase);
    public bool IsReviewedStatus => string.Equals(ReadingStatus, "reviewed", StringComparison.OrdinalIgnoreCase);
    public bool IsReadingStatus => string.Equals(ReadingStatus, "reading", StringComparison.OrdinalIgnoreCase);
    public bool IsQueuedStatus => !IsCoreStatus && !IsReviewedStatus && !IsReadingStatus;

    public bool HasProjectNote => !string.IsNullOrWhiteSpace(ProjectNote);
    public bool HasNoCard => !HasCard;
    public bool HasResearchQuestions => ResearchQuestions.Count > 0;
    public bool HasMajorFindings => MajorFindings.Count > 0;
    public bool HasAuthorExplanations => AuthorExplanations.Count > 0;
    public bool HasAuthorLimitations => AuthorLimitations.Count > 0;
    public bool HasAiLimitations => AiLimitations.Count > 0;

    public string MetaSubtitle
    {
        get
        {
            var parts = new List<string>();
            if (Authors.Count > 0) parts.Add(string.Join("、", Authors));
            if (Year is not null) parts.Add(Year.Value.ToString());
            if (!string.IsNullOrWhiteSpace(Journal)) parts.Add(Journal);
            return parts.Count == 0 ? "书目信息待补充" : string.Join(" · ", parts);
        }
    }

    public string StudyTypeLabel => (StudyType ?? "").Trim().ToLowerInvariant() switch
    {
        "quantitative_observational" => "定量观察研究",
        "experimental" => "实验研究",
        "qualitative" => "质性研究",
        "mixed_methods" => "混合方法",
        "theoretical" => "理论探讨",
        "review" => "文献综述",
        "methodological" => "方法论研究",
        "" => "待分析",
        var raw => raw,
    };

    public string MethodSummary => string.IsNullOrWhiteSpace(Method) ? "—" : Method;
    public string IdentificationSummary => string.IsNullOrWhiteSpace(IdentificationStrategy) ? "—" : IdentificationStrategy;
    public string PopulationDataSummary
    {
        get
        {
            var parts = new[] { Population, DataSource }.Where(s => !string.IsNullOrWhiteSpace(s)).ToArray();
            return parts.Length == 0 ? "—" : string.Join(" · ", parts);
        }
    }

    public static ProjectMatrixRowItem FromJson(JsonElement node)
    {
        var paperId = JsonRead.Text(node, "paper_id");
        var title = JsonRead.Text(node, "title", "未命名论文");
        int? year = null;
        if (node.TryGetProperty("year", out var yNode) && yNode.ValueKind == JsonValueKind.Number && yNode.TryGetInt32(out var yVal))
        {
            year = yVal;
        }
        var hasCard = node.TryGetProperty("has_card", out var hcNode) && hcNode.ValueKind == JsonValueKind.True;

        List<MatrixClaimItem> ReadClaims(string prop)
        {
            var list = new List<MatrixClaimItem>();
            if (node.TryGetProperty(prop, out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in arr.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.Object)
                    {
                        list.Add(MatrixClaimItem.FromJson(item, paperId, title));
                    }
                }
            }
            return list;
        }

        return new ProjectMatrixRowItem
        {
            PaperId = paperId,
            Title = title,
            Authors = JsonRead.StringList(node, "authors"),
            Year = year,
            Journal = JsonRead.Text(node, "journal"),
            ReadingStatus = JsonRead.Text(node, "reading_status", "queued"),
            ProjectNote = JsonRead.Text(node, "project_note"),
            HasCard = hasCard,
            StudyType = JsonRead.Text(node, "study_type"),
            Method = JsonRead.Text(node, "method"),
            IdentificationStrategy = JsonRead.Text(node, "identification_strategy"),
            Population = JsonRead.Text(node, "population"),
            DataSource = JsonRead.Text(node, "data_source"),
            ResearchQuestions = ReadClaims("research_questions"),
            TheoriesAndConcepts = ReadClaims("theories_and_concepts"),
            ResearchDesign = ReadClaims("research_design"),
            MajorFindings = ReadClaims("major_findings"),
            AuthorExplanations = ReadClaims("author_explanations"),
            AuthorLimitations = ReadClaims("author_limitations"),
            AiLimitations = ReadClaims("ai_limitations"),
        };
    }
}
