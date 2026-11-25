using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 发起专题跨文献综合前的 MD3 透明预检与双重显式授权弹窗。
/// 在发送任何请求到外部 Pro 模型前，明确展示外发内容、模型/服务商与人工审定过滤统计。
/// </summary>
public sealed class SynthesisPreviewWindow : Window
{
    public SynthesisPreviewWindow(SynthesisPreviewInfo preview)
    {
        Title = "跨文献综合 · 透明外发预检与授权确认";
        Width = 680;
        Height = 640;
        MinWidth = 560;
        MinHeight = 520;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;

        var header = new StackPanel
        {
            Spacing = 4,
            Margin = new Thickness(24, 20, 24, 12),
            Children =
            {
                new TextBlock { Text = "EXPLICIT PRO SYNTHESIS PREVIEW", Classes = { "eyebrow" } },
                new TextBlock { Text = $"确认对专题「{preview.ProjectName}」发起一次跨文献综合", Classes = { "h1" }, TextWrapping = TextWrapping.Wrap },
                new TextBlock
                {
                    Text = preview.Note,
                    Classes = { "muted" },
                    FontSize = 12,
                    TextWrapping = TextWrapping.Wrap,
                },
            },
        };

        var statsGrid = new Grid
        {
            ColumnDefinitions = new ColumnDefinitions("*,*,*,*"),
            ColumnSpacing = 8,
        };
        void AddMetric(int col, string label, string value)
        {
            var cell = new Border
            {
                Classes = { "metric-cell" },
                Padding = new Thickness(10, 9),
                Child = new StackPanel
                {
                    Spacing = 2,
                    HorizontalAlignment = HorizontalAlignment.Center,
                    Children =
                    {
                        new TextBlock { Text = label, Classes = { "eyebrow" }, HorizontalAlignment = HorizontalAlignment.Center },
                        new TextBlock { Text = value, FontSize = 17, FontWeight = FontWeight.Bold, HorizontalAlignment = HorizontalAlignment.Center },
                    },
                },
            };
            Grid.SetColumn(cell, col);
            statsGrid.Children.Add(cell);
        }
        AddMetric(0, "纳入论文", $"{preview.IncludedPaperCount} 篇");
        AddMetric(1, "纳入主张", $"{preview.IncludedClaimCount} 条");
        AddMetric(2, "采用修正", $"{preview.CorrectedClaimCount} 条");
        AddMetric(3, "剔除驳回", $"{preview.RejectedClaimCount} 条");

        var detailCard = new Border
        {
            Classes = { "card" },
            Padding = new Thickness(18, 14),
            Child = new StackPanel
            {
                Spacing = 8,
                Children =
                {
                    MetaRow("Pro 模型", preview.ProModel),
                    MetaRow("目标服务商", preview.Provider),
                    MetaRow("输入策略", preview.IncludeUnreviewed
                        ? $"全部未驳回主张（优先采纳人工修正；已确认 {preview.ConfirmedClaimCount} 条）"
                        : $"严格模式：仅纳入已人工确认/修正主张（已跳过未审定 {preview.SkippedUnreviewedCount} 条）"),
                    MetaRow("估算输入规模", $"约 {preview.ApproximateInputChars:N0} 字符 · 输出上限 {preview.MaximumOutputTokens} tokens · 单次请求"),
                    MetaRow("快照摘要 SHA-256", preview.SnapshotDigest[..Math.Min(20, preview.SnapshotDigest.Length)] + "…"),
                    MetaRow("数据安全边界", "仅发送结构化主张摘要与人工修正文本，绝不上传完整 PDF，也不修改任何原始 Paper Card"),
                },
            },
        };

        var payloadBox = new TextBox
        {
            Text = preview.OutboundPayload,
            IsReadOnly = true,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            FontFamily = new FontFamily("Consolas"),
            FontSize = 10.5,
            MaxHeight = 200,
        };
        var payloadExpander = new Expander
        {
            Header = "查看即将发送至外部 Pro 模型的完整结构化载荷（只读审计）",
            IsExpanded = false,
            Content = payloadBox,
        };

        var sendConsent = new CheckBox
        {
            Content = "我已核对上方外发范围，同意将上述结构化矩阵摘要发送至我配置的 Pro 模型接口。",
        };
        var costConsent = new CheckBox
        {
            Content = "我知晓本次将发起 1 次 Pro 模型请求并可能由我的 API 服务商计费，且结果仅作为不可变 AI_INFERRED_SYNTHESIS 快照独立保存。",
        };

        var consentCard = new Border
        {
            Classes = { "tonal-card" },
            Padding = new Thickness(16, 12),
            Child = new StackPanel
            {
                Spacing = 8,
                Children = { sendConsent, costConsent },
            },
        };

        var body = new StackPanel
        {
            Spacing = 12,
            Margin = new Thickness(24, 0, 24, 16),
            Children = { statsGrid, detailCard, payloadExpander, consentCard },
        };

        var cancelBtn = new Button { Content = "取消", Classes = { "secondary" } };
        cancelBtn.Click += (_, _) => Close(null);

        var runBtn = new Button
        {
            Content = "确认并发起一次 Pro 跨文献综合",
            Classes = { "primary" },
            IsEnabled = false,
        };
        void RefreshEnabled() => runBtn.IsEnabled = sendConsent.IsChecked == true && costConsent.IsChecked == true;
        sendConsent.IsCheckedChanged += (_, _) => RefreshEnabled();
        costConsent.IsCheckedChanged += (_, _) => RefreshEnabled();
        runBtn.Click += (_, _) => Close(new SynthesisConsentResult(true, true));

        var footer = new Border
        {
            Padding = new Thickness(24, 14),
            BorderThickness = new Thickness(0, 1, 0, 0),
            Child = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
                Spacing = 10,
                Children = { cancelBtn, runBtn },
            },
        };
        footer.Bind(Border.BackgroundProperty, this.GetResourceObservable("SurfaceContainerLowBrush"));
        footer.Bind(Border.BorderBrushProperty, this.GetResourceObservable("OutlineSubtleBrush"));

        var scroller = new ScrollViewer
        {
            VerticalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Auto,
            Content = body,
        };

        var root = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto") };
        Grid.SetRow(header, 0);
        Grid.SetRow(scroller, 1);
        Grid.SetRow(footer, 2);
        root.Children.Add(header);
        root.Children.Add(scroller);
        root.Children.Add(footer);
        Content = root;

        KeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape)
            {
                Close(null);
                e.Handled = true;
            }
        };
    }

    private static Grid MetaRow(string label, string value)
    {
        var grid = new Grid
        {
            ColumnDefinitions = new ColumnDefinitions("125,*"),
            ColumnSpacing = 10,
        };
        var l = new TextBlock { Text = label, Classes = { "eyebrow" }, VerticalAlignment = VerticalAlignment.Top };
        var v = new TextBlock { Text = value, FontSize = 12.5, TextWrapping = TextWrapping.Wrap, LineHeight = 19 };
        Grid.SetColumn(l, 0);
        Grid.SetColumn(v, 1);
        grid.Children.Add(l);
        grid.Children.Add(v);
        return grid;
    }
}
