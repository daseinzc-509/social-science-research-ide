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

/// <summary>
/// A located cell from Docling's first-class table structure.
/// All offsets are zero-based; spans are explicit, never guessed from
/// duplicate header strings or cell contents.
/// </summary>
public sealed class TableSpanCellItem
{
    public int Row { get; init; }
    public int Column { get; init; }
    public int RowSpan { get; init; } = 1;
    public int ColumnSpan { get; init; } = 1;
    public string Text { get; init; } = "";
    public bool IsHeader { get; init; }
    public bool HeaderVerified { get; init; }
}


public sealed class VisualSourceItem
{
    public string Page { get; init; } = "—";
    public string Label { get; init; } = "";
    public string Text { get; init; } = "";
    public string SourceId { get; init; } = "";
    public string Location { get; init; } = "";

    public static VisualSourceItem FromFormula(JsonElement node) => new()
    {
        Page = JsonRead.Text(node, "page_number", "—"),
        Text = JsonRead.Text(node, "text", "未提取公式文本"),
        Label = "公式 · 可能为 LaTeX 或原始识别文本",
        SourceId = JsonRead.Text(node, "id"),
        Location = FormatBbox(node),
    };

    public static VisualSourceItem FromFigure(JsonElement node) => new()
    {
        Page = JsonRead.Text(node, "page_number", "—"),
        Text = JsonRead.Text(node, "caption", "该图片尚无可靠的文字说明，请打开 PDF 核对。"),
        Label = "图片来源定位 · 未执行图片内容推断",
        SourceId = JsonRead.Text(node, "id"),
        Location = FormatBbox(node),
    };

    private static string FormatBbox(JsonElement node)
    {
        if (!node.TryGetProperty("bbox", out var bbox) || bbox.ValueKind != JsonValueKind.Array)
            return "未取得区域坐标";
        var values = bbox.EnumerateArray().ToArray();
        if (values.Length != 4 || values.Any(value => value.ValueKind != JsonValueKind.Number))
            return "未取得区域坐标";
        return string.Join(", ", values.Select(value => value.GetDouble().ToString("F1")));
    }
}

public sealed class TableItem
{
    public string Page { get; init; } = "—";
    public string SourceBlockId { get; init; } = "";
    public string DisplayText { get; init; } = "";
    public IReadOnlyList<TableCellItem> FirstRowCells { get; init; } = [];
    public IReadOnlyList<TableRowItem> RemainingRows { get; init; } = [];
    public IReadOnlyList<TableSpanCellItem> SpanCells { get; init; } = [];
    public bool HasVerifiedSpans { get; init; }
    public int RowCount { get; init; }
    public int ColumnCount { get; init; }

    public bool HasStructuredRows => RowCount > 0 && ColumnCount > 0;
    public bool HasRawOnly => !HasStructuredRows;
    public string Dimensions => HasStructuredRows
        ? $"{RowCount} 行 × {ColumnCount} 列 · " +
          (HasVerifiedSpans ? "保留 Docling 合并单元格坐标" : "仅有二维网格，表头结构待核验")
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
        var spans = ReadAuthoritativeSpans(node, parsedRows, columnCount);
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
            SpanCells = spans ?? BuildFlatCells(parsedRows, columnCount),
            HasVerifiedSpans = spans is not null,
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

    private static IReadOnlyList<TableSpanCellItem>? ReadAuthoritativeSpans(
        JsonElement node, IReadOnlyList<List<string>> rows, int width)
    {
        if (rows.Count == 0 || width == 0 ||
            !node.TryGetProperty("table_cells", out var cellsNode) ||
            cellsNode.ValueKind != JsonValueKind.Array || cellsNode.GetArrayLength() == 0)
            return null;

        var occupied = new bool[rows.Count, width];
        var result = new List<TableSpanCellItem>();
        foreach (var source in cellsNode.EnumerateArray())
        {
            if (source.ValueKind != JsonValueKind.Object ||
                !TryReadNumber(source, "row", out var row) ||
                !TryReadNumber(source, "col", out var column) ||
                !TryReadNumber(source, "row_span", out var rowSpan) ||
                !TryReadNumber(source, "col_span", out var colSpan) ||
                row < 0 || column < 0 || rowSpan < 1 || colSpan < 1 ||
                row > rows.Count - rowSpan || column > width - colSpan)
                return null;

            for (var r = row; r < row + rowSpan; r++)
            for (var c = column; c < column + colSpan; c++)
            {
                if (occupied[r, c]) return null;
                occupied[r, c] = true;
            }

            var isHeader = ReadBool(source, "column_header") || ReadBool(source, "row_header");
            result.Add(new TableSpanCellItem
            {
                Row = row,
                Column = column,
                RowSpan = rowSpan,
                ColumnSpan = colSpan,
                Text = JsonRead.Text(source, "text"),
                IsHeader = isHeader,
                HeaderVerified = isHeader,
            });
        }

        // Docling can omit empty logical cells. Display them as blanks without
        // inventing any content, but never copy duplicated span header text.
        for (var row = 0; row < rows.Count; row++)
        for (var col = 0; col < width; col++)
            if (!occupied[row, col])
                result.Add(new TableSpanCellItem
                {
                    Row = row, Column = col,
                    Text = col < rows[row].Count ? rows[row][col] : "",
                });

        return result;
    }

    private static IReadOnlyList<TableSpanCellItem> BuildFlatCells(
        IReadOnlyList<List<string>> rows, int width)
    {
        var output = new List<TableSpanCellItem>();
        for (var row = 0; row < rows.Count; row++)
        for (var col = 0; col < width; col++)
            output.Add(new TableSpanCellItem
            {
                Row = row, Column = col,
                Text = col < rows[row].Count ? rows[row][col] : "",
                IsHeader = row == 0,
                HeaderVerified = false,
            });
        return output;
    }

    private static bool TryReadNumber(JsonElement node, string name, out int result)
    {
        result = 0;
        return node.TryGetProperty(name, out var value) &&
            value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out result);
    }

    private static bool ReadBool(JsonElement node, string name)
    {
        return node.TryGetProperty(name, out var value) &&
            value.ValueKind == JsonValueKind.True;
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
