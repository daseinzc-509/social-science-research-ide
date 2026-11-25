using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 双向文献-专题归属 MD3 弹窗：
/// - 从单篇论文打开：勾选该论文应归属的专题列表（支持顺手新建专题、设置阅读状态与专题备注）；
/// - 从某个专题打开：从全库挑选多篇文献批量加入该专题。
/// </summary>
public sealed record PaperProjectAssignmentResult(
    IReadOnlyList<string> SelectedIds,
    string ReadingStatus,
    string ProjectNote,
    bool RequestCreateNewProject = false);

public sealed class AssignProjectWindow : Window
{
    private readonly List<(string Id, string SearchHaystack, CheckBox Box)> _items = [];
    private readonly StackPanel _listPanel = new() { Spacing = 8 };
    private readonly TextBox _filterBox;
    private readonly ComboBox _statusCombo;
    private readonly TextBox _noteBox;

    public AssignProjectWindow(
        string title,
        string subtitle,
        IReadOnlyList<(string Id, string Title, string Subtitle, bool IsChecked)> choices,
        string initialReadingStatus = "queued",
        string initialNote = "",
        bool showCreateProjectButton = false)
    {
        Title = title;
        Width = 560;
        Height = 560;
        MinWidth = 460;
        MinHeight = 440;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;

        _filterBox = new TextBox
        {
            PlaceholderText = "快速筛选…",
        };
        _filterBox.TextChanged += (_, _) => FilterList();

        _statusCombo = new ComboBox
        {
            ItemsSource = ProjectReadingStatusOption.All,
            SelectedItem = ProjectReadingStatusOption.All.FirstOrDefault(
                o => string.Equals(o.Value, initialReadingStatus, StringComparison.OrdinalIgnoreCase))
                ?? ProjectReadingStatusOption.All[0],
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        _noteBox = new TextBox
        {
            Text = initialNote,
            PlaceholderText = "可选：填写该文献在专题中的角色或备注，如「基准 DID 文献」",
        };

        foreach (var choice in choices)
        {
            var cb = new CheckBox
            {
                IsChecked = choice.IsChecked,
                Content = new StackPanel
                {
                    Spacing = 2,
                    Children =
                    {
                        new TextBlock
                        {
                            Text = choice.Title,
                            FontWeight = FontWeight.SemiBold,
                            FontSize = 13,
                            TextWrapping = TextWrapping.Wrap,
                        },
                        new TextBlock
                        {
                            Text = choice.Subtitle,
                            FontSize = 11,
                            Classes = { "muted" },
                            TextTrimming = TextTrimming.CharacterEllipsis,
                        },
                    },
                },
            };
            var card = new Border
            {
                Padding = new Thickness(12, 9),
                Classes = { "soft-card" },
                Child = cb,
            };
            cb.Tag = card;
            _items.Add((choice.Id, $"{choice.Title} {choice.Subtitle}", cb));
            _listPanel.Children.Add(card);
        }

        var header = new StackPanel
        {
            Spacing = 4,
            Margin = new Thickness(24, 20, 24, 12),
            Children =
            {
                new TextBlock { Text = "TOPIC ASSIGNMENT", Classes = { "eyebrow" } },
                new TextBlock { Text = title, Classes = { "h1" } },
                new TextBlock
                {
                    Text = subtitle,
                    Classes = { "muted" },
                    FontSize = 12,
                    TextWrapping = TextWrapping.Wrap,
                },
            },
        };

        var bodyStack = new Grid
        {
            RowDefinitions = new RowDefinitions("Auto,*,Auto"),
            RowSpacing = 12,
            Margin = new Thickness(24, 0, 24, 12),
        };
        Grid.SetRow(_filterBox, 0);
        bodyStack.Children.Add(_filterBox);

        Control listHost = choices.Count == 0
            ? new Border
            {
                Classes = { "soft-card" },
                Padding = new Thickness(20),
                Child = new TextBlock
                {
                    Text = "当前没有可选项。",
                    Classes = { "muted" },
                    HorizontalAlignment = HorizontalAlignment.Center,
                    VerticalAlignment = VerticalAlignment.Center,
                },
            }
            : new ScrollViewer
            {
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Content = _listPanel,
            };
        Grid.SetRow(listHost, 1);
        bodyStack.Children.Add(listHost);

        var metaGrid = new Grid
        {
            ColumnDefinitions = new ColumnDefinitions("160,*"),
            ColumnSpacing = 12,
        };
        var statusCol = new StackPanel
        {
            Spacing = 5,
            Children =
            {
                new TextBlock { Text = "阅读进度", FontSize = 12, FontWeight = FontWeight.SemiBold, Classes = { "muted" } },
                _statusCombo,
            },
        };
        var noteCol = new StackPanel
        {
            Spacing = 5,
            Children =
            {
                new TextBlock { Text = "专题备注（可选）", FontSize = 12, FontWeight = FontWeight.SemiBold, Classes = { "muted" } },
                _noteBox,
            },
        };
        Grid.SetColumn(statusCol, 0);
        Grid.SetColumn(noteCol, 1);
        metaGrid.Children.Add(statusCol);
        metaGrid.Children.Add(noteCol);

        var metaCard = new Border
        {
            Padding = new Thickness(14, 12),
            Classes = { "tonal-card" },
            Child = metaGrid,
        };
        Grid.SetRow(metaCard, 2);
        bodyStack.Children.Add(metaCard);

        var footerGrid = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto") };
        if (showCreateProjectButton)
        {
            var createBtn = new Button { Content = "＋ 新建专题", Classes = { "tonal", "compact" } };
            createBtn.Click += (_, _) => Close(new PaperProjectAssignmentResult(
                GetCheckedIds(), SelectedStatus(), (_noteBox.Text ?? "").Trim(), RequestCreateNewProject: true));
            Grid.SetColumn(createBtn, 0);
            footerGrid.Children.Add(createBtn);
        }

        var cancelBtn = new Button { Content = "取消", Classes = { "secondary" } };
        cancelBtn.Click += (_, _) => Close(null);
        var confirmBtn = new Button { Content = "保存归属", Classes = { "primary" } };
        confirmBtn.Click += (_, _) => Close(new PaperProjectAssignmentResult(
            GetCheckedIds(), SelectedStatus(), (_noteBox.Text ?? "").Trim()));

        var rightButtons = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            Spacing = 10,
            Children = { cancelBtn, confirmBtn },
        };
        Grid.SetColumn(rightButtons, 2);
        footerGrid.Children.Add(rightButtons);

        var footer = new Border
        {
            Padding = new Thickness(24, 14),
            Child = footerGrid,
            BorderThickness = new Thickness(0, 1, 0, 0),
        };
        footer.Bind(Border.BackgroundProperty, this.GetResourceObservable("SurfaceContainerLowBrush"));
        footer.Bind(Border.BorderBrushProperty, this.GetResourceObservable("OutlineSubtleBrush"));

        var root = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto") };
        Grid.SetRow(header, 0);
        Grid.SetRow(bodyStack, 1);
        Grid.SetRow(footer, 2);
        root.Children.Add(header);
        root.Children.Add(bodyStack);
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

    private void FilterList()
    {
        var term = (_filterBox.Text ?? "").Trim();
        foreach (var (_, haystack, box) in _items)
        {
            if (box.Tag is Control card)
            {
                card.IsVisible = string.IsNullOrWhiteSpace(term) ||
                    haystack.Contains(term, StringComparison.OrdinalIgnoreCase);
            }
        }
    }

    private IReadOnlyList<string> GetCheckedIds() =>
        _items.Where(item => item.Box.IsChecked == true).Select(item => item.Id).ToList();

    private string SelectedStatus() =>
        (_statusCombo.SelectedItem as ProjectReadingStatusOption)?.Value ?? "queued";
}
