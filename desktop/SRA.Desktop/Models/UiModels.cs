using System.Text.Json;

namespace SRA.Desktop.Models;

public sealed class EvidenceItem
{
    public string Page { get; init; } = "—";
    public string Quote { get; init; } = "";
    public string SourceBlockId { get; init; } = "";
    public string Role { get; init; } = "ANCHOR";
    // The underlying source block identifier remains in SourceBlockId and raw Card.
    // It should not interrupt reading with a UUID in the visible quote header.
    public string MetaLine => $"PDF 第 {Page} 页 · " + (Role switch
    {
        "ANCHOR" => "核心引文",
        "CONTEXT" => "上下文",
        "QUALIFIER" => "限定证据",
        "TABLE" => "表格",
        _ => Role,
    });

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
    public string EvidenceSummary => $"查看原文证据（{Evidence.Count} 段）";
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
        var scopeLabels = new Dictionary<string, string>
        {
            ["population"] = "研究群体",
            ["setting"] = "情境",
            ["time"] = "时间",
            ["subgroup"] = "子群体",
            ["treatment_or_exposure"] = "处理 / 暴露",
            ["outcome"] = "结果",
            ["conditions"] = "成立条件",
        };
        foreach (var name in scopeLabels.Keys)
        {
            if (!scope.TryGetProperty(name, out var value) || value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            {
                continue;
            }
            var text = value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString();
            if (!string.IsNullOrWhiteSpace(text))
            {
                items.Add($"{scopeLabels[name]}：{text}");
            }
        }
        return string.Join("  ·  ", items);
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

/// <summary>
/// One displayed cell. The value is preserved from Docling's table_rows;
/// presentation never performs number formatting, OCR correction or inference.
/// </summary>
public sealed class TableCellItem
{
    public string Text { get; init; } = "";
}

/// <summary>A single visual table row, padded only with empty display cells.</summary>
public sealed class TableRowItem
{
    public IReadOnlyList<TableCellItem> Cells { get; init; } = [];
}

public sealed class TableItem
{
    public string Page { get; init; } = "—";
    public string SourceBlockId { get; init; } = "";
    public string DisplayText { get; init; } = "";
    public IReadOnlyList<TableCellItem> FirstRowCells { get; init; } = [];
    public IReadOnlyList<TableRowItem> RemainingRows { get; init; } = [];
    public int RowCount { get; init; }
    public int ColumnCount { get; init; }

    public bool HasStructuredRows => RowCount > 0 && ColumnCount > 0;
    public bool HasRawOnly => !HasStructuredRows;
    public string Dimensions => HasStructuredRows
        ? $"{RowCount} 行 × {ColumnCount} 列 · 源自 PDF 识别"
        : "仅有原文提取文本，暂无可用的二维表格结构";

    public static TableItem FromJson(JsonElement node)
    {
        var parsedRows = new List<List<string>>();
        if (node.TryGetProperty("table_rows", out var rows) && rows.ValueKind == JsonValueKind.Array)
        {
            foreach (var row in rows.EnumerateArray())
            {
                if (row.ValueKind != JsonValueKind.Array)
                    continue;

                // Preserve extracted cell text. Null cells are display blanks;
                // repeated header text is NOT deduplicated or guessed away.
                var cells = row.EnumerateArray().Select(cell => cell.ValueKind switch
                {
                    JsonValueKind.String => cell.GetString() ?? "",
                    JsonValueKind.Null => "",
                    JsonValueKind.Undefined => "",
                    _ => cell.ToString(),
                }).ToList();
                parsedRows.Add(cells);
            }
        }

        var columnCount = parsedRows.Count == 0 ? 0 : parsedRows.Max(row => row.Count);
        var flatText = parsedRows.Count > 0 && columnCount > 0
            ? string.Join(Environment.NewLine, parsedRows.Select(row => string.Join("  |  ", row)))
            : "没有可显示的表格文本。";
        var originalText = JsonRead.Text(node, "text");

        static IReadOnlyList<TableCellItem> ToCells(IReadOnlyList<string> values, int width)
        {
            var result = new List<TableCellItem>(width);
            for (var index = 0; index < width; index++)
                result.Add(new TableCellItem
                {
                    Text = index < values.Count ? values[index] : "",
                });
            return result;
        }

        return new TableItem
        {
            Page = JsonRead.Text(node, "page_number", "—"),
            SourceBlockId = JsonRead.Text(node, "id"),
            // The PDF parser's original source text is the audit source.
            // Constructed row text is only the fallback if it was absent.
            DisplayText = string.IsNullOrWhiteSpace(originalText) ? flatText : originalText,
            RowCount = parsedRows.Count,
            ColumnCount = columnCount,
            // The first row is visually highlighted, but no assertion is made
            // that it is a verified header: merged/multi-level tables differ.
            FirstRowCells = parsedRows.Count == 0
                ? Array.Empty<TableCellItem>()
                : ToCells(parsedRows[0], columnCount),
            RemainingRows = parsedRows.Skip(1)
                .Select(row => new TableRowItem { Cells = ToCells(row, columnCount) })
                .ToList(),
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
