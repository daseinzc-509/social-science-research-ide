using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace SRA.Desktop.Models;

/// <summary>
/// 与后端 `compute_claim_id` 字节级一致的确定性 Claim 标识符。
/// </summary>
public static class ClaimIdHelper
{
    public static string Compute(string fieldName, string statement)
    {
        var bytes = Encoding.UTF8.GetBytes($"{fieldName}\0{statement}");
        var hash = SHA1.HashData(bytes);
        return "claim-" + Convert.ToHexString(hash).ToLowerInvariant()[..16];
    }
}

/// <summary>
/// 研究者针对单篇论文撰写的个人阅读笔记（MY NOTE / USER_NOTE）。
/// </summary>
public sealed class UserNoteItem
{
    public string Id { get; init; } = "";
    public string PaperId { get; init; } = "";
    public string Title { get; init; } = "";
    public string Note { get; init; } = "";
    public int? PageNumber { get; init; }
    public string CreatedAt { get; init; } = "";
    public string UpdatedAt { get; init; } = "";

    public bool HasTitle => !string.IsNullOrWhiteSpace(Title);
    public string DisplayTitle => HasTitle ? Title : "阅读笔记";
    public bool HasPageNumber => PageNumber is > 0;
    public string PageBadgeLabel => PageNumber is > 0 ? $"PDF p.{PageNumber} →" : "";
    public string TimeLabel => FormatShortTime(string.IsNullOrWhiteSpace(UpdatedAt) ? CreatedAt : UpdatedAt);

    public static UserNoteItem FromJson(JsonElement node)
    {
        int? page = null;
        if (node.TryGetProperty("page_number", out var pNode) &&
            pNode.ValueKind == JsonValueKind.Number &&
            pNode.TryGetInt32(out var pVal) && pVal > 0)
        {
            page = pVal;
        }
        return new UserNoteItem
        {
            Id = JsonRead.Text(node, "id"),
            PaperId = JsonRead.Text(node, "paper_id"),
            Title = JsonRead.Text(node, "title"),
            Note = JsonRead.Text(node, "note"),
            PageNumber = page,
            CreatedAt = JsonRead.Text(node, "created_at"),
            UpdatedAt = JsonRead.Text(node, "updated_at"),
        };
    }

    private static string FormatShortTime(string iso)
    {
        if (DateTimeOffset.TryParse(iso, out var dto))
        {
            return dto.ToLocalTime().ToString("yyyy-MM-dd HH:mm");
        }
        return string.IsNullOrWhiteSpace(iso) ? "" : iso[..Math.Min(16, iso.Length)];
    }
}

/// <summary>
/// 单条 Claim 的独立人工审定状态与修正记录（HUMAN_REVIEWED）。
/// </summary>
public sealed class ClaimReviewItem
{
    public string Id { get; init; } = "";
    public string PaperId { get; init; } = "";
    public string ClaimId { get; init; } = "";
    public string FieldName { get; init; } = "";
    public string FieldLabel => string.IsNullOrWhiteSpace(FieldName) ? "主张" : FieldLabels.Get(FieldName);
    public string OriginalStatement { get; init; } = "";
    public string UserStatus { get; init; } = "";
    public string CorrectedStatement { get; init; } = "";
    public string Note { get; init; } = "";
    public string UpdatedAt { get; init; } = "";
    public bool IsOrphan { get; init; }

    public string StatusLabel => StatusToLabel(UserStatus);
    public bool IsConfirmed => string.Equals(UserStatus, "confirmed", StringComparison.OrdinalIgnoreCase);
    public bool IsCorrected => string.Equals(UserStatus, "corrected", StringComparison.OrdinalIgnoreCase);
    public bool IsRejected => string.Equals(UserStatus, "rejected", StringComparison.OrdinalIgnoreCase);
    public bool IsUnresolved => string.Equals(UserStatus, "unresolved", StringComparison.OrdinalIgnoreCase);
    public bool HasCorrectedStatement => !string.IsNullOrWhiteSpace(CorrectedStatement);
    public bool HasNote => !string.IsNullOrWhiteSpace(Note);
    public bool HasOriginalStatement => !string.IsNullOrWhiteSpace(OriginalStatement);

    public string TimeLabel
    {
        get
        {
            if (DateTimeOffset.TryParse(UpdatedAt, out var dto))
                return dto.ToLocalTime().ToString("yyyy-MM-dd HH:mm");
            return string.IsNullOrWhiteSpace(UpdatedAt) ? "" : UpdatedAt[..Math.Min(16, UpdatedAt.Length)];
        }
    }

    public static string StatusToLabel(string? status) => (status ?? "").Trim().ToLowerInvariant() switch
    {
        "confirmed" => "✓ 已确认",
        "corrected" => "✎ 人工修正",
        "rejected" => "✕ 存疑驳回",
        "unresolved" => "? 待定",
        _ => "",
    };

    public static ClaimReviewItem FromJson(JsonElement node, bool isOrphan = false) => new()
    {
        Id = JsonRead.Text(node, "id"),
        PaperId = JsonRead.Text(node, "paper_id"),
        ClaimId = JsonRead.Text(node, "claim_id"),
        FieldName = JsonRead.Text(node, "field_name"),
        OriginalStatement = JsonRead.Text(node, "original_statement"),
        UserStatus = JsonRead.Text(node, "user_status"),
        CorrectedStatement = JsonRead.Text(node, "corrected_statement"),
        Note = JsonRead.Text(node, "note"),
        UpdatedAt = JsonRead.Text(node, "updated_at"),
        IsOrphan = isOrphan,
    };
}

public sealed record NoteEditorResult(string Title, string Note, int? PageNumber);

public sealed record ClaimReviewEditorResult(
    string UserStatus,
    string CorrectedStatement,
    string Note,
    bool ClearReview = false);
