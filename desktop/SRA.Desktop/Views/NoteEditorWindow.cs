using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 编辑已有个人阅读笔记（MY NOTE）的 MD3 弹窗。
/// </summary>
public sealed class NoteEditorWindow : Window
{
    private readonly TextBox _titleBox;
    private readonly TextBox _pageBox;
    private readonly TextBox _bodyBox;
    private readonly TextBlock _errorText;

    private readonly bool _showPageField;

    public NoteEditorWindow(UserNoteItem existing)
        : this(
            windowTitle: "编辑个人笔记",
            heading: "编辑个人阅读笔记",
            initialTitle: existing.Title,
            initialNote: existing.Note,
            initialPage: existing.PageNumber,
            showPageField: true)
    {
    }

    public NoteEditorWindow(ProjectNoteItem existing)
        : this(
            windowTitle: "编辑专题研究笔记",
            heading: "编辑专题跨文献笔记",
            initialTitle: existing.Title,
            initialNote: existing.Note,
            initialPage: null,
            showPageField: false)
    {
    }

    private NoteEditorWindow(
        string windowTitle,
        string heading,
        string initialTitle,
        string initialNote,
        int? initialPage,
        bool showPageField)
    {
        _showPageField = showPageField;
        Title = windowTitle;
        Width = 580;
        Height = 490;
        MinWidth = 480;
        MinHeight = 410;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;

        _titleBox = new TextBox
        {
            Text = initialTitle,
            PlaceholderText = showPageField
                ? "可选：笔记小标题，如「样本局限与可借鉴处」"
                : "可选：专题笔记标题，如「跨文献机制张力与综述框架」",
        };
        _pageBox = new TextBox
        {
            Text = initialPage?.ToString() ?? "",
            PlaceholderText = "页码（可选）",
        };
        _bodyBox = new TextBox
        {
            Text = initialNote,
            PlaceholderText = "写下阅读心得、批判思考或引文思路…",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 160,
        };
        _errorText = new TextBlock
        {
            IsVisible = false,
            FontSize = 12,
            TextWrapping = TextWrapping.Wrap,
        };
        _errorText.Bind(TextBlock.ForegroundProperty, this.GetResourceObservable("RedInkBrush"));

        Control topControl;
        if (showPageField)
        {
            var topRow = new Grid
            {
                ColumnDefinitions = new ColumnDefinitions("*,130"),
                ColumnSpacing = 12,
            };
            var titleField = Field("标题（可选）", _titleBox);
            var pageField = Field("关联 PDF 页码", _pageBox);
            Grid.SetColumn(titleField, 0);
            Grid.SetColumn(pageField, 1);
            topRow.Children.Add(titleField);
            topRow.Children.Add(pageField);
            topControl = topRow;
        }
        else
        {
            topControl = Field("标题（可选）", _titleBox);
        }

        var header = new StackPanel
        {
            Spacing = 4,
            Margin = new Thickness(24, 20, 24, 12),
            Children =
            {
                new TextBlock { Text = "MY NOTE", Classes = { "eyebrow" } },
                new TextBlock { Text = heading, Classes = { "h1" } },
            },
        };

        var card = new Border
        {
            Padding = new Thickness(20, 16),
            Margin = new Thickness(24, 0, 24, 12),
            Classes = { "card" },
            Child = new StackPanel
            {
                Spacing = 12,
                Children =
                {
                    topControl,
                    Field("笔记正文（必填）", _bodyBox),
                    _errorText,
                },
            },
        };

        var cancelBtn = new Button { Content = "取消", Classes = { "secondary" } };
        cancelBtn.Click += (_, _) => Close(null);
        var saveBtn = new Button { Content = "保存修改", Classes = { "primary" } };
        saveBtn.Click += (_, _) => Submit();

        var footer = new Border
        {
            Padding = new Thickness(24, 14),
            BorderThickness = new Thickness(0, 1, 0, 0),
            Child = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
                Spacing = 10,
                Children = { cancelBtn, saveBtn },
            },
        };
        footer.Bind(Border.BackgroundProperty, this.GetResourceObservable("SurfaceContainerLowBrush"));
        footer.Bind(Border.BorderBrushProperty, this.GetResourceObservable("OutlineSubtleBrush"));

        var root = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto") };
        Grid.SetRow(header, 0);
        Grid.SetRow(card, 1);
        Grid.SetRow(footer, 2);
        root.Children.Add(header);
        root.Children.Add(card);
        root.Children.Add(footer);
        Content = root;

        Opened += (_, _) => _bodyBox.Focus();
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
        var body = (_bodyBox.Text ?? "").Trim();
        if (string.IsNullOrWhiteSpace(body))
        {
            _errorText.Text = "笔记正文不能为空。";
            _errorText.IsVisible = true;
            _bodyBox.Focus();
            return;
        }
        int? page = null;
        if (_showPageField)
        {
            var rawPage = (_pageBox.Text ?? "").Trim();
            if (!string.IsNullOrWhiteSpace(rawPage))
            {
                if (!int.TryParse(rawPage, out var parsed) || parsed < 1)
                {
                    _errorText.Text = "关联页码必须是大于 0 的整数，或留空。";
                    _errorText.IsVisible = true;
                    _pageBox.Focus();
                    return;
                }
                page = parsed;
            }
        }
        Close(new NoteEditorResult((_titleBox.Text ?? "").Trim(), body, page));
    }
}
