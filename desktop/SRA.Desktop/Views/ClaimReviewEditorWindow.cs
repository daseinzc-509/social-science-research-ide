using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 编辑单条 Claim 的人工审定状态、修正陈述与审定备注的 MD3 弹窗。
/// 原始陈述仅作只读对照展示，绝不覆盖原始 Paper Card。
/// </summary>
public sealed class ClaimReviewEditorWindow : Window
{
    private sealed record StatusChoice(string Value, string Label)
    {
        public override string ToString() => Label;
    }

    private static readonly StatusChoice[] StatusChoices =
    [
        new("confirmed", "✓ 已确认（核对原文无误）"),
        new("corrected", "✎ 人工修正（保留原文并写明修正表述）"),
        new("rejected", "✕ 存疑驳回（与原文不符或过度推断）"),
        new("unresolved", "? 待定（需进一步核查）"),
    ];

    private readonly ComboBox _statusCombo;
    private readonly TextBox _correctedBox;
    private readonly TextBox _noteBox;
    private readonly TextBlock _errorText;

    public ClaimReviewEditorWindow(ClaimItem claim, string initialStatus = "corrected")
    {
        Title = "单条主张人工审定与修正";
        Width = 620;
        Height = 560;
        MinWidth = 520;
        MinHeight = 460;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;

        var selectedStatus = string.IsNullOrWhiteSpace(initialStatus)
            ? (string.IsNullOrWhiteSpace(claim.HumanReviewStatus) ? "corrected" : claim.HumanReviewStatus)
            : initialStatus;

        _statusCombo = new ComboBox
        {
            ItemsSource = StatusChoices,
            SelectedItem = StatusChoices.FirstOrDefault(c => string.Equals(c.Value, selectedStatus, StringComparison.OrdinalIgnoreCase))
                ?? StatusChoices[1],
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };

        _correctedBox = new TextBox
        {
            Text = string.IsNullOrWhiteSpace(claim.HumanCorrectedStatement)
                ? (selectedStatus == "corrected" ? claim.Statement : "")
                : claim.HumanCorrectedStatement,
            PlaceholderText = "当状态为「人工修正」时必填；将与原提取陈述并列呈现，绝不覆盖原始 Paper Card。",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 96,
            MaxHeight = 160,
        };

        _noteBox = new TextBox
        {
            Text = claim.HumanReviewNote,
            PlaceholderText = "可选：记录核验页码、异议理由或写综述时的使用说明…",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 72,
            MaxHeight = 130,
        };

        _errorText = new TextBlock
        {
            IsVisible = false,
            FontSize = 12,
            TextWrapping = TextWrapping.Wrap,
        };
        _errorText.Bind(TextBlock.ForegroundProperty, this.GetResourceObservable("RedInkBrush"));

        var header = new StackPanel
        {
            Spacing = 4,
            Margin = new Thickness(24, 20, 24, 12),
            Children =
            {
                new TextBlock { Text = "HUMAN REVIEW DECISION", Classes = { "eyebrow" } },
                new TextBlock { Text = $"人工审定 · {claim.FieldLabel}", Classes = { "h1" } },
                new TextBlock
                {
                    Text = "审定记录独立保存，重跑分析不覆盖，且同步显示于专题证据矩阵与全文检索。",
                    Classes = { "muted" },
                    FontSize = 12,
                    TextWrapping = TextWrapping.Wrap,
                },
            },
        };

        var originalCard = new Border
        {
            Padding = new Thickness(14, 11),
            Classes = { "tonal-card" },
            Child = new StackPanel
            {
                Spacing = 4,
                Children =
                {
                    new TextBlock { Text = $"原始提取陈述（{claim.SourceLabel} · 只读对照）", Classes = { "eyebrow" } },
                    new TextBlock { Text = claim.Statement, FontSize = 13, LineHeight = 20, TextWrapping = TextWrapping.Wrap },
                },
            },
        };

        var formStack = new StackPanel
        {
            Spacing = 12,
            Margin = new Thickness(24, 0, 24, 14),
            Children =
            {
                originalCard,
                new Border
                {
                    Padding = new Thickness(18, 16),
                    Classes = { "card" },
                    Child = new StackPanel
                    {
                        Spacing = 12,
                        Children =
                        {
                            Field("审定状态", _statusCombo),
                            Field("研究者修正陈述（选择「人工修正」时必填）", _correctedBox),
                            Field("审定备注（可选）", _noteBox),
                            _errorText,
                        },
                    },
                },
            },
        };

        var footerGrid = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto") };
        if (claim.HasHumanReview)
        {
            var clearBtn = new Button { Content = "清除审定", Classes = { "danger", "compact" } };
            clearBtn.Click += (_, _) => Close(new ClaimReviewEditorResult("", "", "", ClearReview: true));
            Grid.SetColumn(clearBtn, 0);
            footerGrid.Children.Add(clearBtn);
        }

        var cancelBtn = new Button { Content = "取消", Classes = { "secondary" } };
        cancelBtn.Click += (_, _) => Close(null);
        var saveBtn = new Button { Content = "保存审定", Classes = { "primary" } };
        saveBtn.Click += (_, _) => Submit();

        var rightButtons = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            Spacing = 10,
            Children = { cancelBtn, saveBtn },
        };
        Grid.SetColumn(rightButtons, 2);
        footerGrid.Children.Add(rightButtons);

        var footer = new Border
        {
            Padding = new Thickness(24, 14),
            BorderThickness = new Thickness(0, 1, 0, 0),
            Child = footerGrid,
        };
        footer.Bind(Border.BackgroundProperty, this.GetResourceObservable("SurfaceContainerLowBrush"));
        footer.Bind(Border.BorderBrushProperty, this.GetResourceObservable("OutlineSubtleBrush"));

        var scroller = new ScrollViewer
        {
            VerticalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Auto,
            Content = formStack,
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

    private static StackPanel Field(string label, Control input) => new()
    {
        Spacing = 5,
        Children =
        {
            new TextBlock { Text = label, FontSize = 12, FontWeight = FontWeight.SemiBold, Classes = { "muted" } },
            input,
        },
    };

    private void Submit()
    {
        var status = (_statusCombo.SelectedItem as StatusChoice)?.Value ?? "confirmed";
        var corrected = (_correctedBox.Text ?? "").Trim();
        var note = (_noteBox.Text ?? "").Trim();
        if (status == "corrected" && string.IsNullOrWhiteSpace(corrected))
        {
            _errorText.Text = "状态为「人工修正」时必须填写修正陈述。";
            _errorText.IsVisible = true;
            _correctedBox.Focus();
            return;
        }
        Close(new ClaimReviewEditorResult(status, corrected, note));
    }
}
