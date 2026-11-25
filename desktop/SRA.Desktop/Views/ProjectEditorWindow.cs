using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 新建或编辑研究专题（项目）的 MD3 弹窗。
/// </summary>
public sealed class ProjectEditorWindow : Window
{
    private readonly TextBox _nameBox;
    private readonly TextBox _questionBox;
    private readonly TextBox _descBox;
    private readonly TextBlock _errorText;

    public ProjectEditorWindow(ProjectSummaryView? existing = null)
    {
        var isEdit = existing is not null;
        Title = isEdit ? "编辑研究专题" : "新建研究专题";
        Width = 540;
        Height = 460;
        MinWidth = 460;
        MinHeight = 400;
        CanResize = false;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;

        _nameBox = new TextBox
        {
            Text = existing?.Name ?? "",
            PlaceholderText = "例如：户籍制度改革与市民化",
        };
        _questionBox = new TextBox
        {
            Text = existing?.ResearchQuestion ?? "",
            PlaceholderText = "可选：记录本专题要比较的核心研究问题或争论焦点…",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 76,
            MaxHeight = 110,
        };
        _descBox = new TextBox
        {
            Text = existing?.Description ?? "",
            PlaceholderText = "可选：收文范围、样本标准或写作章节说明…",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 68,
            MaxHeight = 100,
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
            Margin = new Thickness(24, 20, 24, 14),
            Children =
            {
                new TextBlock { Text = "RESEARCH TOPIC", Classes = { "eyebrow" } },
                new TextBlock { Text = isEdit ? "编辑研究专题" : "新建研究专题", Classes = { "h1" } },
                new TextBlock
                {
                    Text = "专题用于聚合多篇论文并生成零模型调用的跨文献证据对比矩阵，删除专题不会删除原论文。",
                    Classes = { "muted" },
                    FontSize = 12,
                    TextWrapping = TextWrapping.Wrap,
                },
            },
        };

        var formCard = new Border
        {
            Padding = new Thickness(20, 16),
            Margin = new Thickness(24, 0, 24, 12),
            Classes = { "card" },
            Child = new StackPanel
            {
                Spacing = 12,
                Children =
                {
                    Field("专题名称（必填）", _nameBox),
                    Field("核心研究问题", _questionBox),
                    Field("专题说明（可选）", _descBox),
                    _errorText,
                },
            },
        };

        var cancelBtn = new Button { Content = "取消", Classes = { "secondary" } };
        cancelBtn.Click += (_, _) => Close(null);

        var saveBtn = new Button { Content = isEdit ? "保存修改" : "创建专题", Classes = { "primary" } };
        saveBtn.Click += (_, _) => Submit();

        var footer = new Border
        {
            Padding = new Thickness(24, 14),
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
        footer.BorderThickness = new Thickness(0, 1, 0, 0);

        var root = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto") };
        Grid.SetRow(header, 0);
        Grid.SetRow(formCard, 1);
        Grid.SetRow(footer, 2);
        root.Children.Add(header);
        root.Children.Add(formCard);
        root.Children.Add(footer);
        Content = root;

        Opened += (_, _) => _nameBox.Focus();
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
            new TextBlock { Text = label, FontSize = 12.5, FontWeight = FontWeight.SemiBold, Classes = { "muted" } },
            input,
        },
    };

    private void Submit()
    {
        var name = (_nameBox.Text ?? "").Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            _errorText.Text = "请输入研究专题名称。";
            _errorText.IsVisible = true;
            _nameBox.Focus();
            return;
        }
        Close(new ProjectEditorResult(
            name,
            (_questionBox.Text ?? "").Trim(),
            (_descBox.Text ?? "").Trim()));
    }
}
