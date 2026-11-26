using System.Text.RegularExpressions;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Documents;
using Avalonia.Media;

namespace SRA.Desktop.Controls;

/// <summary>
/// 支持中英双语检索词荧光高亮的 MD3 文本控件（动态绑定主题令牌，Light / Dark 实时自适应）。
/// </summary>
public sealed partial class HighlightedSnippetText : TextBlock
{
    private static readonly Regex SegmentRegex = new(
        @"[0-9A-Za-z]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+",
        RegexOptions.Compiled);

    private static readonly Regex CjkRegex = new(
        @"^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+$",
        RegexOptions.Compiled);

    public static readonly StyledProperty<string> SnippetTextProperty =
        AvaloniaProperty.Register<HighlightedSnippetText, string>(nameof(SnippetText), "");

    public static readonly StyledProperty<string> HighlightQueryProperty =
        AvaloniaProperty.Register<HighlightedSnippetText, string>(nameof(HighlightQuery), "");

    static HighlightedSnippetText()
    {
        SnippetTextProperty.Changed.AddClassHandler<HighlightedSnippetText>((control, _) => control.RebuildInlines());
        HighlightQueryProperty.Changed.AddClassHandler<HighlightedSnippetText>((control, _) => control.RebuildInlines());
    }

    public string SnippetText
    {
        get => GetValue(SnippetTextProperty);
        set => SetValue(SnippetTextProperty, value);
    }

    public string HighlightQuery
    {
        get => GetValue(HighlightQueryProperty);
        set => SetValue(HighlightQueryProperty, value);
    }

    private void RebuildInlines()
    {
        var inlines = Inlines;
        if (inlines is null) return;
        inlines.Clear();

        var source = SnippetText ?? "";
        if (string.IsNullOrEmpty(source))
        {
            return;
        }

        var ranges = FindHighlightRanges(source, HighlightQuery ?? "");
        if (ranges.Count == 0)
        {
            inlines.Add(new Run(source));
            return;
        }

        var cursor = 0;
        foreach (var (start, end) in ranges)
        {
            if (start > cursor)
            {
                inlines.Add(new Run(source[cursor..start]));
            }
            var hitRun = new Run(source[start..end])
            {
                FontWeight = FontWeight.SemiBold,
            };
            hitRun.Bind(TextElement.BackgroundProperty, this.GetResourceObservable("AmberSoftBrush"));
            hitRun.Bind(TextElement.ForegroundProperty, this.GetResourceObservable("AmberInkBrush"));
            inlines.Add(hitRun);
            cursor = end;
        }
        if (cursor < source.Length)
        {
            inlines.Add(new Run(source[cursor..]));
        }
    }

    private static List<(int Start, int End)> FindHighlightRanges(string text, string query)
    {
        var cleanQuery = query.Trim();
        if (string.IsNullOrWhiteSpace(cleanQuery))
        {
            return [];
        }

        var rawRanges = new List<(int Start, int End)>();
        var terms = new List<string>();
        foreach (Match m in SegmentRegex.Matches(cleanQuery))
        {
            var chunk = m.Value;
            if (string.IsNullOrWhiteSpace(chunk)) continue;
            if (CjkRegex.IsMatch(chunk))
            {
                // 优先整词匹配；若未直接命中且长度 >= 2，则回退相邻双字词元（与 FTS5 分词规则一致）
                if (text.IndexOf(chunk, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    terms.Add(chunk);
                }
                else if (chunk.Length >= 2)
                {
                    for (var i = 0; i < chunk.Length - 1; i++)
                    {
                        terms.Add(chunk.Substring(i, 2));
                    }
                }
                else
                {
                    terms.Add(chunk);
                }
            }
            else
            {
                terms.Add(chunk);
            }
            if (terms.Count >= 24) break;
        }

        foreach (var term in terms.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (string.IsNullOrEmpty(term)) continue;
            var searchFrom = 0;
            while (searchFrom < text.Length)
            {
                var idx = text.IndexOf(term, searchFrom, StringComparison.OrdinalIgnoreCase);
                if (idx < 0) break;
                rawRanges.Add((idx, idx + term.Length));
                searchFrom = idx + term.Length;
            }
        }

        if (rawRanges.Count == 0) return [];
        rawRanges.Sort((a, b) => a.Start != b.Start ? a.Start.CompareTo(b.Start) : a.End.CompareTo(b.End));

        var merged = new List<(int Start, int End)>();
        var current = rawRanges[0];
        for (var i = 1; i < rawRanges.Count; i++)
        {
            var next = rawRanges[i];
            if (next.Start <= current.End)
            {
                current = (current.Start, Math.Max(current.End, next.End));
            }
            else
            {
                merged.Add(current);
                current = next;
            }
        }
        merged.Add(current);
        return merged;
    }
}
