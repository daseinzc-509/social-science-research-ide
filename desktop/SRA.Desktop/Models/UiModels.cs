using System.Text.Json;

namespace SRA.Desktop.Models;

public sealed class EvidenceItem
{
    public string Page { get; init; } = "—";
    public string Quote { get; init; } = "";
    public string SourceBlockId { get; init; } = "";
    public string Role { get; init; } = "ANCHOR";
    public string MetaLine => $"PDF p.{Page} · {Role}" + (string.IsNullOrWhiteSpace(SourceBlockId) ? "" : $" · {SourceBlockId}");

    public static EvidenceItem FromJson(JsonElement node)
    {
        return new EvidenceItem
        {
            Page = JsonRead.Text(node, "page_number", "—"),
            Quote = JsonRead.Text(node, "quote"),
            SourceBlockId = JsonRead.Text(node, "source_block_id"),
            Role = JsonRead.Text(node, "role", "ANCHOR"),
        };
    }
}

public sealed class ClaimItem
{
    public string FieldName { get; init; } = "";
    public string FieldLabel { get; init; } = "";
    public string Statement { get; init; } = "";
    public string Provenance { get; init; } = "";
    public string Verification { get; init; } = "";
    public string ClaimType { get; init; } = "OTHER";
    public string SemanticSupport { get; init; } = "";
    public string Rationale { get; init; } = "";
    public string Scope { get; init; } = "";
    public List<EvidenceItem> Evidence { get; init; } = [];

    public bool HasEvidence => Evidence.Count > 0;
    public bool HasRationale => !string.IsNullOrWhiteSpace(Rationale);
    public bool HasScope => !string.IsNullOrWhiteSpace(Scope);
    public bool HasSemanticSupport => !string.IsNullOrWhiteSpace(SemanticSupport);
    public string SourceLabel => Provenance switch
    {
        "AUTHOR_STATED" => "AUTHOR",
        "AI_INFERRED" => "AI REVIEW",
        "USER_NOTE" => "MY NOTE",
        _ => string.IsNullOrWhiteSpace(Provenance) ? "SOURCE" : Provenance,
    };
    public string AuditLine => string.Join(" · ", new[] { ClaimType, Verification, SemanticSupport }
        .Where(value => !string.IsNullOrWhiteSpace(value)));

    public static ClaimItem FromJson(JsonElement node)
    {
        var field = JsonRead.Text(node, "field_name", "claim");
        var evidence = new List<EvidenceItem>();
        if (node.TryGetProperty("evidence", out var evidenceNode) && evidenceNode.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in evidenceNode.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.Object)
                {
                    evidence.Add(EvidenceItem.FromJson(item));
                }
            }
        }

        return new ClaimItem
        {
            FieldName = field,
            FieldLabel = FieldLabels.Get(field),
            Statement = JsonRead.Text(node, "statement"),
            Provenance = JsonRead.Text(node, "provenance"),
            Verification = JsonRead.Text(node, "verification"),
            ClaimType = JsonRead.Text(node, "claim_type", "OTHER"),
            SemanticSupport = JsonRead.Text(node, "semantic_support"),
            Rationale = JsonRead.Text(node, "rationale"),
            Scope = FormatScope(node),
            Evidence = evidence,
        };
    }

    private static string FormatScope(JsonElement node)
    {
        if (!node.TryGetProperty("scope", out var scope) || scope.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        var items = new List<string>();
        foreach (var name in new[] { "population", "setting", "time", "subgroup", "treatment_or_exposure", "outcome", "conditions" })
        {
            if (!scope.TryGetProperty(name, out var value) || value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            {
                continue;
            }
            var text = value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString();
            if (!string.IsNullOrWhiteSpace(text))
            {
                items.Add($"{name}: {text}");
            }
        }
        return string.Join(" · ", items);
    }
}

public sealed class ReferenceItem
{
    public string Ordinal { get; init; } = "";
    public string RawText { get; init; } = "";
    public string ParseStatus { get; init; } = "";
    public string Year { get; init; } = "";
    public string Doi { get; init; } = "";
    public string SourcePage { get; init; } = "";
    public string SourceBlockId { get; init; } = "";
    public string MetaLine => $"PDF p.{SourcePage}" + (string.IsNullOrWhiteSpace(Doi) ? "" : $" · DOI {Doi}");

    public static ReferenceItem FromJson(JsonElement node) => new()
    {
        Ordinal = JsonRead.Text(node, "ordinal"),
        RawText = JsonRead.Text(node, "raw_text"),
        ParseStatus = JsonRead.Text(node, "parse_status"),
        Year = JsonRead.Text(node, "year"),
        Doi = JsonRead.Text(node, "doi"),
        SourcePage = JsonRead.Text(node, "source_page", "—"),
        SourceBlockId = JsonRead.Text(node, "source_block_id"),
    };
}

public sealed class TableItem
{
    public string Page { get; init; } = "—";
    public string DisplayText { get; init; } = "";

    public static TableItem FromJson(JsonElement node)
    {
        var lines = new List<string>();
        if (node.TryGetProperty("table_rows", out var rows) && rows.ValueKind == JsonValueKind.Array)
        {
            foreach (var row in rows.EnumerateArray())
            {
                if (row.ValueKind != JsonValueKind.Array)
                {
                    continue;
                }
                lines.Add(string.Join("  |  ", row.EnumerateArray().Select(cell => cell.ValueKind == JsonValueKind.String ? cell.GetString() : cell.ToString())));
            }
        }
        if (lines.Count == 0)
        {
            lines.Add(JsonRead.Text(node, "text", "没有可显示的表格文本。"));
        }
        return new TableItem
        {
            Page = JsonRead.Text(node, "page_number", "—"),
            DisplayText = string.Join(Environment.NewLine, lines),
        };
    }
}

public static class FieldLabels
{
    private static readonly Dictionary<string, string> Labels = new(StringComparer.OrdinalIgnoreCase)
    {
        ["research_question"] = "研究问题",
        ["theory_concept"] = "理论 / 概念",
        ["research_method"] = "研究方法",
        ["research_sample"] = "样本 / 研究对象",
        ["measurement_indicator"] = "测量 / 指标",
        ["major_finding"] = "核心发现",
        ["author_explanation"] = "作者解释 / 机制",
        ["key_number"] = "关键数字",
        ["research_limitations"] = "作者自述局限",
        ["title"] = "题名",
        ["authors"] = "作者",
        ["journal"] = "期刊",
        ["year"] = "年份",
        ["doi"] = "DOI",
        ["keywords"] = "关键词",
        ["abstract"] = "摘要",
    };

    public static string Get(string fieldName) => Labels.TryGetValue(fieldName, out var label) ? label : fieldName;
}

public static class JsonRead
{
    public static string Text(JsonElement node, string propertyName, string fallback = "")
    {
        if (!node.TryGetProperty(propertyName, out var value) || value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
        {
            return fallback;
        }
        return value.ValueKind == JsonValueKind.String ? value.GetString() ?? fallback : value.ToString();
    }

    public static List<string> StringList(JsonElement node, string propertyName)
    {
        if (!node.TryGetProperty(propertyName, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return value.EnumerateArray()
            .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : item.ToString())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Select(item => item!)
            .ToList();
    }
}

public sealed record AnalysisDialogResult(string? ResearchContext, string? ExcludeAfterText, bool Force);
