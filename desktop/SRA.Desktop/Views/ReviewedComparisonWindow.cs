using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input.Platform;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;

namespace SRA.Desktop.Views;

/// <summary>
/// Side-by-side presentation of an immutable original-card snapshot and a
/// separate AI_INFERRED comparison. Deliberately does not present human
/// transcriptions as author quotations or as a replacement Paper Card.
/// </summary>
public sealed class ReviewedComparisonWindow : Window
{
    private readonly string _markdown;

    public ReviewedComparisonWindow(JsonElement report)
    {
        Title = "SRA · 原始 Paper Card / 人工复核后独立对照";
        Width = 1180;
        Height = 850;
        MinWidth = 850;
        MinHeight = 550;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;

        var baseline = ReadOnly(BuildBaseline(report));
        var revised = ReadOnly(BuildReviewed(report));
        _markdown = Read(report, "markdown");

        var originalPane = new DockPanel
        {
            Margin = new Thickness(13, 12),
            Children =
            {
                new TextBlock
                {
                    Text = "原始分析 A · AUTHOR_STATED / 原始 Paper Card",
                    FontSize = 14,
                    FontWeight = Avalonia.Media.FontWeight.SemiBold,
                    Margin = new Thickness(0, 0, 0, 8),
                },
            },
        };
        DockPanel.SetDock(originalPane.Children[0], Dock.Top);
        originalPane.Children.Add(baseline);

        var reviewedPane = new DockPanel
        {
            Margin = new Thickness(13, 12),
            Children =
            {
                new TextBlock
                {
                    Text = "复核对照 B · HUMAN_REVIEWED + AI_INFERRED",
                    FontSize = 14,
                    FontWeight = Avalonia.Media.FontWeight.SemiBold,
                    Margin = new Thickness(0, 0, 0, 8),
                },
            },
        };
        DockPanel.SetDock(reviewedPane.Children[0], Dock.Top);
        reviewedPane.Children.Add(revised);

        var compareGrid = new Grid
        {
            ColumnDefinitions = new ColumnDefinitions("*,*"),
        };
        Grid.SetColumn(originalPane, 0);
        Grid.SetColumn(reviewedPane, 1);
        compareGrid.Children.Add(originalPane);
        compareGrid.Children.Add(reviewedPane);

        var copy = new Button
        {
            Content = "复制完整可审计 Markdown 报告",
            HorizontalAlignment = HorizontalAlignment.Right,
        };
        var status = new TextBlock
        {
            Text = "此报告是历史快照。若原文或人工修订后来改变，旧报告不会自动更新。",
            TextWrapping = Avalonia.Media.TextWrapping.Wrap,
            VerticalAlignment = VerticalAlignment.Center,
        };
        copy.Click += async (_, _) =>
        {
            try
            {
                var clipboard = TopLevel.GetTopLevel(this)?.Clipboard
                    ?? throw new InvalidOperationException("剪贴板当前不可用");
                await clipboard.SetTextAsync(_markdown);
                status.Text = "已复制对照报告；原始 Paper Card 未修改。";
            }
            catch (Exception exc)
            {
                status.Text = "复制失败：" + exc.Message;
            }
        };
        var footer = new Grid
        {
            ColumnDefinitions = new ColumnDefinitions("*,Auto"),
            Margin = new Thickness(15, 8),
            Children = { status, copy },
        };
        Grid.SetColumn(copy, 1);
        var top = new TextBlock
        {
            Text = "左栏为复评时的原始主张快照；右栏为经人工修订上下文提示后的 Pro 模型评价。"
                 + " 模型没有被授权重写作者引文。请回到 PDF 核验全部关键数值。",
            TextWrapping = Avalonia.Media.TextWrapping.Wrap,
            Margin = new Thickness(18, 12),
        };
        var content = new Grid
        {
            RowDefinitions = new RowDefinitions("Auto,*,Auto"),
        };
        Grid.SetRow(top, 0);
        Grid.SetRow(compareGrid, 1);
        Grid.SetRow(footer, 2);
        content.Children.Add(top);
        content.Children.Add(compareGrid);
        content.Children.Add(footer);
        Content = content;
    }

    private static TextBox ReadOnly(string text) => new()
    {
        Text = text,
        IsReadOnly = true,
        AcceptsReturn = true,
        TextWrapping = Avalonia.Media.TextWrapping.Wrap,
        HorizontalAlignment = HorizontalAlignment.Stretch,
        VerticalAlignment = VerticalAlignment.Stretch,
        Padding = new Thickness(12),
        FontSize = 12,
    };

    private static string Read(JsonElement obj, string name)
    {
        if (obj.ValueKind != JsonValueKind.Object ||
            !obj.TryGetProperty(name, out var value))
            return "";
        return value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? "" : value.ToString();
    }

    private static string BuildBaseline(JsonElement report)
    {
        var content = new StringBuilder();
        content.AppendLine("来源：原始 Paper Card（保存时的不可变快照）");
        content.AppendLine();
        if (!report.TryGetProperty("input_snapshot", out var snapshot))
            return "没有可显示的历史快照。";
        content.AppendLine("PDF SHA-256：" + Read(snapshot, "pdf_sha256"));
        content.AppendLine("Card SHA-256：" + Read(snapshot, "original_card_sha256"));
        content.AppendLine();
        content.AppendLine("原始研究主张：");
        if (snapshot.TryGetProperty("original_claims", out var claims) &&
            claims.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in claims.EnumerateArray())
            {
                content.AppendLine("• " + Read(item, "field_name") + " · "
                    + Read(item, "claim_id"));
                content.AppendLine(Read(item, "statement"));
                content.AppendLine("来源：" + Read(item, "provenance")
                    + " / " + Read(item, "verification"));
                content.AppendLine();
            }
        }
        content.AppendLine("对应人工复核区域的原始 Docling 来源文本：");
        if (snapshot.TryGetProperty("human_reviews", out var revisions) &&
            revisions.ValueKind == JsonValueKind.Array)
        {
            foreach (var revision in revisions.EnumerateArray())
            {
                content.AppendLine("PDF 第 " + Read(revision, "pdf_page") + " 页");
                if (revision.TryGetProperty("linked_original_source", out var sources) &&
                    sources.ValueKind == JsonValueKind.Array)
                {
                    foreach (var source in sources.EnumerateArray())
                    {
                        content.AppendLine("SourceBlock " + Read(source, "source_block_id"));
                        content.AppendLine(Read(source, "original_text"));
                        if (source.TryGetProperty("source_excerpt_truncated", out var limited)
                            && limited.ValueKind == JsonValueKind.True)
                            content.AppendLine("[原始 SourceBlock 文本较长，此处只展示一部分]");
                    }
                }
                content.AppendLine();
            }
        }
        content.AppendLine("本栏并非模型复写，不包含人工纠正为作者原文的替换。");
        return content.ToString();
    }

    private static string BuildReviewed(JsonElement report)
    {
        var content = new StringBuilder();
        content.AppendLine("来源：已核验的人类修订（HUMAN_REVIEWED）");
        content.AppendLine("评价：AI_INFERRED（不作为逐字原文引文）");
        content.AppendLine();
        if (!report.TryGetProperty("input_snapshot", out var snapshot))
            return "缺少该复评的输入快照。";
        var map = new Dictionary<string, (string Text, int Page)>();
        if (snapshot.TryGetProperty("human_reviews", out var reviews) &&
            reviews.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in reviews.EnumerateArray())
            {
                var page = item.TryGetProperty("pdf_page", out var pageValue) ?
                    pageValue.GetInt32() : 0;
                map[Read(item, "review_id")] = (Read(item, "corrected_text"), page);
            }
        }
        if (!report.TryGetProperty("comparison", out var comparison))
            return "缺少该复评的模型比较输出。";
        content.AppendLine("独立 Pro 复评摘要：");
        content.AppendLine(Read(comparison, "summary"));
        content.AppendLine();
        if (comparison.TryGetProperty("impacts", out var impacts) &&
            impacts.ValueKind == JsonValueKind.Array)
        {
            foreach (var impact in impacts.EnumerateArray())
            {
                var id = Read(impact, "review_id");
                var correction = map.TryGetValue(id, out var selected)
                    ? selected : ("（无修订文本）", 0);
                content.AppendLine("PDF 第 " + correction.Item2 + " 页 · "
                    + Read(impact, "status"));
                content.AppendLine("人工核对文字：" + correction.Item1);
                content.AppendLine();
                content.AppendLine("AI 解释：" + Read(impact, "updated_interpretation"));
                content.AppendLine("判断理由：" + Read(impact, "reasoning"));
                content.AppendLine();
            }
        }
        if (comparison.TryGetProperty("limitations", out var limitations) &&
            limitations.ValueKind == JsonValueKind.Array)
        {
            content.AppendLine("需要额外核验：");
            foreach (var note in limitations.EnumerateArray())
                content.AppendLine("• " + (note.GetString() ?? ""));
        }
        content.AppendLine();
        content.AppendLine("本栏不会自动改写原始 SourceBlocks、Card 或模型缓存。");
        return content.ToString();
    }
}
