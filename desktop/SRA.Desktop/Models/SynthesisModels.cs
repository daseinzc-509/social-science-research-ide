using System.Text.Json;

namespace SRA.Desktop.Models;

/// <summary>
/// 发起专题跨文献综合前的本地透明预检结果（零收费、零外发）。
/// </summary>
public sealed class SynthesisPreviewInfo
{
    public string ProjectId { get; init; } = "";
    public string ProjectName { get; init; } = "";
    public string SnapshotDigest { get; init; } = "";
    public string ProModel { get; init; } = "";
    public string Provider { get; init; } = "";
    public bool IncludeUnreviewed { get; init; } = true;
    public bool IncludeProjectNotes { get; init; } = true;
    public string FocusInstruction { get; init; } = "";
    public int IncludedPaperCount { get; init; }
    public int IncludedClaimCount { get; init; }
    public int ConfirmedClaimCount { get; init; }
    public int CorrectedClaimCount { get; init; }
    public int RejectedClaimCount { get; init; }
    public int SkippedUnreviewedCount { get; init; }
    public int ProjectNoteCount { get; init; }
    public int ApproximateInputChars { get; init; }
    public int MaximumOutputTokens { get; init; } = 4096;
    public string OutboundPayload { get; init; } = "";
    public string Note { get; init; } = "";

    public static SynthesisPreviewInfo FromJson(JsonElement node) => new()
    {
        ProjectId = JsonRead.Text(node, "project_id"),
        ProjectName = JsonRead.Text(node, "project_name"),
        SnapshotDigest = JsonRead.Text(node, "snapshot_digest"),
        ProModel = JsonRead.Text(node, "pro_model"),
        Provider = JsonRead.Text(node, "provider"),
        IncludeUnreviewed = !node.TryGetProperty("include_unreviewed", out var iu) || iu.ValueKind != JsonValueKind.False,
        IncludeProjectNotes = !node.TryGetProperty("include_project_notes", out var ipn) || ipn.ValueKind != JsonValueKind.False,
        FocusInstruction = JsonRead.Text(node, "focus_instruction"),
        IncludedPaperCount = ReadInt(node, "included_paper_count"),
        IncludedClaimCount = ReadInt(node, "included_claim_count"),
        ConfirmedClaimCount = ReadInt(node, "confirmed_claim_count"),
        CorrectedClaimCount = ReadInt(node, "corrected_claim_count"),
        RejectedClaimCount = ReadInt(node, "rejected_claim_count"),
        SkippedUnreviewedCount = ReadInt(node, "skipped_unreviewed_count"),
        ProjectNoteCount = ReadInt(node, "project_note_count"),
        ApproximateInputChars = ReadInt(node, "approximate_input_chars"),
        MaximumOutputTokens = ReadInt(node, "maximum_output_tokens", 4096),
        OutboundPayload = JsonRead.Text(node, "outbound_payload"),
        Note = JsonRead.Text(node, "note"),
    };

    private static int ReadInt(JsonElement node, string prop, int fallback = 0) =>
        node.TryGetProperty(prop, out var val) && val.ValueKind == JsonValueKind.Number && val.TryGetInt32(out var n)
            ? n
            : fallback;
}

public sealed record SynthesisConsentResult(bool ApproveExternalSend, bool AcknowledgeApiCost);

/// <summary>
/// 专题跨文献综合历史不可变快照摘要项（供下拉选择器展示）。
/// </summary>
public sealed class SynthesisHistorySummary
{
    public string Id { get; init; } = "";
    public string ProjectId { get; init; } = "";
    public string SnapshotDigest { get; init; } = "";
    public string Model { get; init; } = "";
    public bool IncludeUnreviewed { get; init; } = true;
    public int IncludedPaperCount { get; init; }
    public int IncludedClaimCount { get; init; }
    public int ConfirmedClaimCount { get; init; }
    public int CorrectedClaimCount { get; init; }
    public int RejectedClaimCount { get; init; }
    public string CreatedAt { get; init; } = "";

    public string TimeLabel
    {
        get
        {
            if (DateTimeOffset.TryParse(CreatedAt, out var dto))
                return dto.ToLocalTime().ToString("yyyy-MM-dd HH:mm");
            return string.IsNullOrWhiteSpace(CreatedAt) ? "" : CreatedAt[..Math.Min(16, CreatedAt.Length)];
        }
    }

    public string DisplayLabel =>
        $"{TimeLabel} · {Model} · {IncludedPaperCount} 篇 / {IncludedClaimCount} 条主张"
        + (IncludeUnreviewed ? "" : "（仅已审定）");

    public override string ToString() => DisplayLabel;

    public static SynthesisHistorySummary FromJson(JsonElement node) => new()
    {
        Id = JsonRead.Text(node, "id"),
        ProjectId = JsonRead.Text(node, "project_id"),
        SnapshotDigest = JsonRead.Text(node, "snapshot_digest"),
        Model = JsonRead.Text(node, "model"),
        IncludeUnreviewed = !node.TryGetProperty("include_unreviewed", out var iu) || iu.ValueKind != JsonValueKind.False,
        IncludedPaperCount = ReadInt(node, "included_paper_count"),
        IncludedClaimCount = ReadInt(node, "included_claim_count"),
        ConfirmedClaimCount = ReadInt(node, "confirmed_claim_count"),
        CorrectedClaimCount = ReadInt(node, "corrected_claim_count"),
        RejectedClaimCount = ReadInt(node, "rejected_claim_count"),
        CreatedAt = JsonRead.Text(node, "created_at"),
    };

    private static int ReadInt(JsonElement node, string prop) =>
        node.TryGetProperty(prop, out var val) && val.ValueKind == JsonValueKind.Number && val.TryGetInt32(out var n)
            ? n
            : 0;
}

/// <summary>
/// 综合要点附带的经白名单校验的可点击溯源主张芯片。
/// </summary>
public sealed class SynthesisClaimCitationItem
{
    public string PaperId { get; init; } = "";
    public string PaperTitle { get; init; } = "";
    public string ClaimId { get; init; } = "";
    public string FieldName { get; init; } = "";
    public string Provenance { get; init; } = "";
    public string HumanStatus { get; init; } = "";
    public string Statement { get; init; } = "";
    public string OriginalStatement { get; init; } = "";
    public int? PageNumber { get; init; }
    public string SourceBlockId { get; init; } = "";
    public string Quote { get; init; } = "";

    public bool IsHumanCorrected => string.Equals(HumanStatus, "corrected", StringComparison.OrdinalIgnoreCase);
    public bool IsHumanConfirmed => string.Equals(HumanStatus, "confirmed", StringComparison.OrdinalIgnoreCase);
    public bool HasPageNumber => PageNumber is > 0;

    public string ShortPaperTitle =>
        PaperTitle.Length <= 18 ? PaperTitle : PaperTitle[..18] + "…";

    public string ChipLabel
    {
        get
        {
            var prefix = IsHumanCorrected ? "✎ " : (IsHumanConfirmed ? "✓ " : "");
            var page = PageNumber is > 0 ? $" · PDF p.{PageNumber} →" : " →";
            return $"{prefix}{ShortPaperTitle}{page}";
        }
    }

    public string TooltipText
    {
        get
        {
            var lines = new List<string> { $"论文：{PaperTitle}" };
            if (IsHumanCorrected) lines.Add("人工审定：✎ 人工修正（HUMAN_REVIEWED）");
            else if (IsHumanConfirmed) lines.Add("人工审定：✓ 已确认（HUMAN_REVIEWED）");
            lines.Add($"主张：{Statement}");
            if (!string.IsNullOrWhiteSpace(Quote)) lines.Add($"原文摘录：{Quote}");
            lines.Add(PageNumber is > 0 ? $"点击直跳 PDF 第 {PageNumber} 页高亮原文" : "点击打开该论文精读视图");
            return string.Join("\n", lines);
        }
    }

    public static SynthesisClaimCitationItem FromJson(JsonElement node)
    {
        int? page = null;
        if (node.TryGetProperty("page_number", out var pNode) && pNode.ValueKind == JsonValueKind.Number && pNode.TryGetInt32(out var pVal) && pVal > 0)
        {
            page = pVal;
        }
        return new SynthesisClaimCitationItem
        {
            PaperId = JsonRead.Text(node, "paper_id"),
            PaperTitle = JsonRead.Text(node, "paper_title"),
            ClaimId = JsonRead.Text(node, "claim_id"),
            FieldName = JsonRead.Text(node, "field_name"),
            Provenance = JsonRead.Text(node, "provenance"),
            HumanStatus = JsonRead.Text(node, "human_status"),
            Statement = JsonRead.Text(node, "statement"),
            OriginalStatement = JsonRead.Text(node, "original_statement"),
            PageNumber = page,
            SourceBlockId = JsonRead.Text(node, "source_block_id"),
            Quote = JsonRead.Text(node, "quote"),
        };
    }
}

/// <summary>
/// 跨文献综合的单条结构化论点（共识 / 冲突 / 方法论差异 / 研究缺口）。
/// </summary>
public sealed class SynthesisPointItem
{
    public string Title { get; init; } = "";
    public string Summary { get; init; } = "";
    public string DivergenceAxis { get; init; } = "";
    public List<SynthesisClaimCitationItem> SupportingClaims { get; init; } = [];

    public bool HasDivergenceAxis => !string.IsNullOrWhiteSpace(DivergenceAxis);
    public bool HasSupportingClaims => SupportingClaims.Count > 0;

    public static SynthesisPointItem FromJson(JsonElement node)
    {
        var refs = new List<SynthesisClaimCitationItem>();
        if (node.TryGetProperty("supporting_claims", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in arr.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.Object)
                {
                    refs.Add(SynthesisClaimCitationItem.FromJson(item));
                }
            }
        }
        return new SynthesisPointItem
        {
            Title = JsonRead.Text(node, "title"),
            Summary = JsonRead.Text(node, "summary"),
            DivergenceAxis = JsonRead.Text(node, "divergence_axis"),
            SupportingClaims = refs,
        };
    }
}

/// <summary>
/// 完整的单次专题跨文献综合详情快照。
/// </summary>
public sealed class SynthesisDetailItem
{
    public string Id { get; init; } = "";
    public string ProjectId { get; init; } = "";
    public string SnapshotDigest { get; init; } = "";
    public string Model { get; init; } = "";
    public bool IncludeUnreviewed { get; init; } = true;
    public string FocusInstruction { get; init; } = "";
    public int IncludedPaperCount { get; init; }
    public int IncludedClaimCount { get; init; }
    public int ConfirmedClaimCount { get; init; }
    public int CorrectedClaimCount { get; init; }
    public int RejectedClaimCount { get; init; }
    public string CreatedAt { get; init; } = "";
    public string ExecutiveSummary { get; init; } = "";
    public List<SynthesisPointItem> Consensus { get; init; } = [];
    public List<SynthesisPointItem> Conflicts { get; init; } = [];
    public List<SynthesisPointItem> MethodologicalDifferences { get; init; } = [];
    public List<SynthesisPointItem> ResearchGaps { get; init; } = [];
    public List<string> Limitations { get; init; } = [];
    public string Markdown { get; init; } = "";

    public string AuditStatsLine =>
        $"纳入 {IncludedPaperCount} 篇论文 · {IncludedClaimCount} 条主张（已确认 {ConfirmedClaimCount} · 人工修正 {CorrectedClaimCount} · 自动剔除已驳回 {RejectedClaimCount}）· 模型 {Model}";

    public static SynthesisDetailItem FromJson(JsonElement node)
    {
        static List<SynthesisPointItem> ReadPoints(JsonElement parent, string key)
        {
            var list = new List<SynthesisPointItem>();
            if (parent.TryGetProperty(key, out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in arr.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.Object)
                    {
                        list.Add(SynthesisPointItem.FromJson(item));
                    }
                }
            }
            return list;
        }

        var synNode = node.TryGetProperty("synthesis", out var sn) && sn.ValueKind == JsonValueKind.Object
            ? sn
            : node;
        var summary = SynthesisHistorySummary.FromJson(node);
        return new SynthesisDetailItem
        {
            Id = summary.Id,
            ProjectId = summary.ProjectId,
            SnapshotDigest = summary.SnapshotDigest,
            Model = summary.Model,
            IncludeUnreviewed = summary.IncludeUnreviewed,
            FocusInstruction = JsonRead.Text(node, "focus_instruction"),
            IncludedPaperCount = summary.IncludedPaperCount,
            IncludedClaimCount = summary.IncludedClaimCount,
            ConfirmedClaimCount = summary.ConfirmedClaimCount,
            CorrectedClaimCount = summary.CorrectedClaimCount,
            RejectedClaimCount = summary.RejectedClaimCount,
            CreatedAt = summary.CreatedAt,
            ExecutiveSummary = JsonRead.Text(synNode, "executive_summary"),
            Consensus = ReadPoints(synNode, "consensus"),
            Conflicts = ReadPoints(synNode, "conflicts"),
            MethodologicalDifferences = ReadPoints(synNode, "methodological_differences"),
            ResearchGaps = ReadPoints(synNode, "research_gaps"),
            Limitations = JsonRead.StringList(synNode, "limitations"),
            Markdown = JsonRead.Text(node, "markdown"),
        };
    }
}
