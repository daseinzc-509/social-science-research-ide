namespace SRA.Desktop.Models;

/// <summary>
/// 跨论文 / 专题级 SQLite FTS5 全文检索单条命中结果视图模型（严格保留来源可审计标识）。
/// </summary>
public sealed class SearchHitItem
{
    public string DocKey { get; init; } = "";
    public string PaperId { get; init; } = "";
    public string PaperTitle { get; init; } = "";
    public string SourceKind { get; init; } = "";
    public string Provenance { get; init; } = "";
    public int? PageNumber { get; init; }
    public string Snippet { get; init; } = "";
    public string HighlightQuery { get; init; } = "";
    public double Score { get; init; }

    public bool IsSourceBlock => string.Equals(SourceKind, "SOURCE_BLOCK", StringComparison.OrdinalIgnoreCase);
    public bool IsPaperCard => string.Equals(SourceKind, "PAPER_CARD", StringComparison.OrdinalIgnoreCase);
    public bool IsUserNote => string.Equals(SourceKind, "USER_NOTE", StringComparison.OrdinalIgnoreCase);
    public bool IsProjectNote => string.Equals(SourceKind, "PROJECT_NOTE", StringComparison.OrdinalIgnoreCase);
    public bool IsHumanReviewed => string.Equals(SourceKind, "HUMAN_REVIEWED", StringComparison.OrdinalIgnoreCase);
    public bool IsMetadata => string.Equals(SourceKind, "METADATA", StringComparison.OrdinalIgnoreCase);

    public bool IsCardAi => IsPaperCard && string.Equals(Provenance, "AI_INFERRED", StringComparison.OrdinalIgnoreCase);
    public bool IsCardAuthor => IsPaperCard && !IsCardAi;
    public bool IsAnyNote => IsUserNote || IsProjectNote;
    public bool IsPaperTarget => !IsProjectNote;

    public string? TargetProjectId =>
        PaperId.StartsWith("project:", StringComparison.Ordinal) && PaperId.Length > "project:".Length
            ? PaperId["project:".Length..]
            : null;

    public bool HasPageNumber => PageNumber is > 0;
    public string PageBadgeLabel => PageNumber is > 0 ? $"PDF p.{PageNumber}" : "";

    public string? SourceBlockId =>
        DocKey.StartsWith("block:", StringComparison.Ordinal) && DocKey.Length > 6
            ? DocKey[6..]
            : null;

    public bool IsClaimReviewHit =>
        IsHumanReviewed && DocKey.StartsWith("claim_review:", StringComparison.Ordinal);

    public bool CanJumpPdf => !IsProjectNote && (HasPageNumber || !string.IsNullOrWhiteSpace(SourceBlockId));

    public string SourceKindLabel => SourceKind.ToUpperInvariant() switch
    {
        "SOURCE_BLOCK" => "PDF 原文",
        "PAPER_CARD" => IsCardAi ? "AI REVIEW" : "AUTHOR · Card",
        "USER_NOTE" => "MY NOTE",
        "PROJECT_NOTE" => "专题笔记",
        "HUMAN_REVIEWED" => IsClaimReviewHit ? "人工审定" : "局部复核",
        "METADATA" => "书目元数据",
        _ => SourceKind,
    };

    public string PrimaryJumpLabel
    {
        get
        {
            if (IsProjectNote) return "打开专题笔记 →";
            if (IsUserNote) return "直达「我的笔记」→";
            if (IsClaimReviewHit) return "查看人工审定 →";
            if (IsSourceBlock && PageNumber is > 0) return $"定位 PDF p.{PageNumber} →";
            if (IsHumanReviewed && PageNumber is > 0) return $"定位复核页 p.{PageNumber} →";
            if (IsMetadata) return "查看书目概览 →";
            return "打开精读 →";
        }
    }

    public string SecondaryPdfJumpLabel => PageNumber is > 0 ? $"定位 PDF p.{PageNumber}" : "查看原文";

    /// <summary>
    /// 避免主按钮和副按钮重复：当主按钮不是跳 PDF 但命中条目仍携带有效页码时显示副按钮。
    /// </summary>
    public bool ShowSecondaryPdfButton =>
        !IsProjectNote && HasPageNumber && !(IsSourceBlock || (IsHumanReviewed && !IsClaimReviewHit));

    public static SearchHitItem FromView(SearchHitView view, string highlightQuery = "") => new()
    {
        DocKey = view.DocKey,
        PaperId = view.PaperId,
        PaperTitle = string.IsNullOrWhiteSpace(view.PaperTitle) ? view.PaperId : view.PaperTitle,
        SourceKind = view.SourceKind,
        Provenance = view.Provenance,
        PageNumber = view.PageNumber,
        Snippet = view.Snippet,
        HighlightQuery = highlightQuery,
        Score = view.Score,
    };
}
