using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Controls.Shapes;
using Avalonia.Input;
using Avalonia.Input.Platform;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using SRA.Desktop.Services;

namespace SRA.Desktop.Views;

/// <summary>
/// 展示权威 PDF 页面栅格与原始 Docling SourceBlocks 的对照窗口。
/// 框选区域可执行局部 OCR 生成未验证候选；研究者确认后保存为独立、可撤回的人工修订。
/// 本窗口绝不修改原始 PDF、SourceBlocks 或既有 Paper Card。
/// </summary>
public sealed class RegionReviewWindow : Window
{
    private sealed record VisibleBlock(string Id, string Text, Rect Box);

    private sealed record ReviewChoice(
        string Id, int Revision, int Page, string Text, string Note, string LinkStatus)
    {
        public override string ToString()
        {
            var excerpt = Text.Replace("\r", " ").Replace("\n", " ");
            if (excerpt.Length > 50) excerpt = excerpt[..50] + "…";
            return $"PDF p.{Page} · v{Revision} · {excerpt}";
        }
    }

    private readonly SraApiClient _api;
    private readonly string _paperId;
    private readonly int _initialPage;
    private string? _pendingHighlightBlockId;

    private readonly Canvas _canvas = new() { Background = Brushes.Transparent };
    private readonly Image _image = new() { Stretch = Stretch.None };
    private readonly Rectangle _highlightShape = new()
    {
        Stroke = new SolidColorBrush(Color.FromArgb(225, 217, 119, 6)),
        StrokeThickness = 2.5,
        Fill = new SolidColorBrush(Color.FromArgb(58, 245, 158, 11)),
        RadiusX = 4,
        RadiusY = 4,
        IsVisible = false,
        IsHitTestVisible = false,
    };
    private readonly Rectangle _selectionShape = new()
    {
        Stroke = new SolidColorBrush(Color.FromRgb(103, 80, 164)),
        StrokeThickness = 2,
        Fill = new SolidColorBrush(Color.FromArgb(44, 103, 80, 164)),
        RadiusX = 3,
        RadiusY = 3,
        IsVisible = false,
        IsHitTestVisible = false,
    };
    private readonly ScrollViewer _pdfScroller;
    private readonly TabControl _sideTabs;

    private readonly TextBlock _pageLabel = new()
    {
        Text = "载入 PDF…",
        FontSize = 12.5,
        FontWeight = FontWeight.SemiBold,
        VerticalAlignment = VerticalAlignment.Center,
    };
    private readonly TextBlock _status = new()
    {
        Text = "框选左侧 PDF 的一段文字或表格，再运行局部 OCR。",
        TextWrapping = TextWrapping.Wrap,
        FontSize = 12,
    };
    private readonly TextBox _source = ReadOnlyBox(115, 210);
    private readonly TextBox _selectedSource = ReadOnlyBox(105, 200);
    private readonly TextBox _ocrResult = new()
    {
        PlaceholderText = "识别后的候选文字将在此显示；请对照左侧原文手动校正。",
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MinHeight = 115,
        MaxHeight = 260,
    };
    private readonly TextBox _note = new()
    {
        PlaceholderText = "可选：写下修正理由，例如「原文中的样本量为 4,519」。",
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MinHeight = 46,
    };
    private readonly TextBox _history = ReadOnlyBox(180, 380);

    private readonly Button _ocrButton = StyledButton("识别选中区域", "primary", enabled: false);
    private readonly Button _manualButton = StyledButton("仅人工录入（跳过 OCR）", "tonal", enabled: false);
    private readonly Button _saveButton = StyledButton("确认并保存人工修订", "primary", enabled: false);
    private readonly Button _undoButton = StyledButton("撤回刚保存的修订", "danger", "compact", enabled: false);
    private readonly ComboBox _revisionSelector = new()
    {
        MinWidth = 240,
        HorizontalAlignment = HorizontalAlignment.Stretch,
    };
    private readonly Button _loadRevisionButton = StyledButton("载入选中修订，准备修改", "tonal", enabled: false);
    private readonly Button _saveRevisionButton = StyledButton("保存为新版本（保留历史）", "tonal", enabled: false);
    private readonly Button _copyPacketButton = StyledButton("复制人工复核材料（不会自动分析）", "secondary");

    private readonly Button _reviewPlanButton = StyledButton("① 预检复评范围（不调用 API）", "tonal");
    private readonly TextBlock _reviewPlanDescription = new()
    {
        Text = "尚未预检。需要先确认人工修订与原始证据关联均有效。",
        TextWrapping = TextWrapping.Wrap,
        FontSize = 12.5,
        LineHeight = 20,
    };
    private readonly TextBox _outboundPayloadPreview = new()
    {
        IsReadOnly = true,
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MaxHeight = 225,
        MinHeight = 100,
        FontFamily = new FontFamily("Consolas"),
        FontSize = 11,
    };
    private readonly CheckBox _externalConsent = new()
    {
        Content = "我确认发送列出的原文摘录、原 Card 主张和人工修订到已配置的 Pro API，并知晓可能产生费用。",
        IsChecked = false,
    };
    private readonly Button _reviewRunButton = StyledButton("② 明确授权并执行一次 Pro 独立复评", "primary", enabled: false);
    private readonly Button _latestReviewButton = StyledButton("打开最近一次独立复评（左右对照）", "secondary");
    private string? _reviewRunPlanDigest;

    private Bitmap? _bitmap;
    private readonly List<VisibleBlock> _blocks = [];
    private CancellationTokenSource? _pageCancellation;
    private Point? _dragStart;
    private Rect? _selected;
    private string? _previewId;
    private string? _lastReviewId;
    private string? _loadedRevisionId;
    private int _loadedRevisionNumber;
    private int _page = 1;
    private int _totalPages = 1;
    private double _imageWidth;
    private double _imageHeight;
    private double _pageWidthPoints;
    private double _pageHeightPoints;
    private bool _loading;
    private int _selectionRevision;

    public RegionReviewWindow(
        SraApiClient api,
        string paperId,
        string title,
        int initialPage = 1,
        string? highlightSourceBlockId = null)
    {
        _api = api;
        _paperId = paperId;
        _initialPage = Math.Max(1, initialPage);
        _totalPages = _initialPage;
        _pendingHighlightBlockId = string.IsNullOrWhiteSpace(highlightSourceBlockId)
            ? null
            : highlightSourceBlockId.Trim();

        Title = "SRA · 原文对照 / 局部 OCR · " + title;
        Width = 1400;
        Height = 900;
        MinWidth = 1020;
        MinHeight = 680;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;

        _canvas.Children.Add(_image);
        _canvas.Children.Add(_highlightShape);
        _canvas.Children.Add(_selectionShape);
        _canvas.PointerPressed += CanvasPressed;
        _canvas.PointerMoved += CanvasMoved;
        _canvas.PointerReleased += CanvasReleased;

        var previous = StyledButton("‹ 上一页", "secondary", "compact");
        previous.Click += async (_, _) => await LoadPageAsync(Math.Max(1, _page - 1));
        var next = StyledButton("下一页 ›", "secondary", "compact");
        next.Click += async (_, _) => await LoadPageAsync(Math.Min(_totalPages, _page + 1));
        var refresh = StyledButton("刷新", "ghost", "compact");
        refresh.Click += async (_, _) => await LoadPageAsync(_page);

        _ocrButton.Click += async (_, _) => await RunOcrAsync(manualOnly: false);
        _manualButton.Click += async (_, _) => await RunOcrAsync(manualOnly: true);
        _saveButton.Click += async (_, _) => await ConfirmAsync();
        _undoButton.Click += async (_, _) => await RevokeLastAsync();
        _loadRevisionButton.Click += (_, _) => LoadSelectedRevision();
        _saveRevisionButton.Click += async (_, _) => await SaveNewRevisionAsync();
        _copyPacketButton.Click += async (_, _) => await CopyEvidencePacketAsync();
        _reviewPlanButton.Click += async (_, _) => await PrepareReviewedComparisonAsync();
        _reviewRunButton.Click += async (_, _) => await RunReviewedComparisonAsync();
        _latestReviewButton.Click += async (_, _) => await OpenLatestReviewedComparisonAsync();

        var pageBadge = new Border
        {
            Padding = new Thickness(12, 4),
            VerticalAlignment = VerticalAlignment.Center,
            Child = _pageLabel,
        };
        pageBadge.Classes.Add("badge-accent");

        var toolsBar = new Border
        {
            Padding = new Thickness(14, 10),
            Margin = new Thickness(14, 14, 8, 10),
            Child = new Grid
            {
                ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto"),
                Children =
                {
                    Put(new StackPanel
                    {
                        Orientation = Orientation.Horizontal,
                        Spacing = 8,
                        VerticalAlignment = VerticalAlignment.Center,
                        Children = { previous, pageBadge, next },
                    }, col: 0),
                    Put(new TextBlock
                    {
                        Text = "在 PDF 上拖拽框选局部区域进行 OCR 或人工校订",
                        FontSize = 12,
                        VerticalAlignment = VerticalAlignment.Center,
                        HorizontalAlignment = HorizontalAlignment.Center,
                        Classes = { "muted" },
                    }, col: 1),
                    Put(refresh, col: 2),
                },
            },
        };
        toolsBar.Classes.Add("soft-card");

        _pdfScroller = new ScrollViewer
        {
            HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = new Border
            {
                Padding = new Thickness(16),
                HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Top,
                Child = _canvas,
            },
        };
        var pdfSurface = new Border
        {
            Margin = new Thickness(14, 0, 8, 12),
            ClipToBounds = true,
            Child = _pdfScroller,
        };
        pdfSurface.Classes.Add("tonal-card");

        var leftPane = new Grid { RowDefinitions = new RowDefinitions("Auto,*") };
        Grid.SetRow(toolsBar, 0);
        Grid.SetRow(pdfSurface, 1);
        leftPane.Children.Add(toolsBar);
        leftPane.Children.Add(pdfSurface);

        // 右侧重构为三个清晰 MD3 子分页，彻底消除超长单列滚筒
        var reviewTabContent = new StackPanel
        {
            Spacing = 14,
            Margin = new Thickness(14, 14, 16, 22),
            Children =
            {
                Card("soft-card",
                    Heading("原文证据对照"),
                    Caption("左侧为 PDF 原始页面；下方原始文字来自 Docling，不因局部 OCR 自动改变。"),
                    Label("当前框选 / 定位的原始证据"),
                    _selectedSource,
                    new Expander
                    {
                        Header = "查看该页全部 Docling 提取文字",
                        IsExpanded = false,
                        Content = _source,
                    }),
                Card("card",
                    Heading("局部 OCR 与人工校订"),
                    Caption("框选左侧区域后可运行局部 OCR 或直接人工抄录；确认后保存为独立人工修订记录。"),
                    ButtonRow(_ocrButton, _manualButton),
                    Label("OCR 候选 / 人工录入（需对照左侧原文核对）"),
                    _ocrResult,
                    Label("研究者修订说明（可选）"),
                    _note,
                    ButtonRow(_saveButton, _saveRevisionButton),
                    Caption("保存的是独立的人工修订记录，不覆盖 PDF、Docling SourceBlock 或旧 Paper Card。"),
                    ButtonRow(_undoButton)),
            },
        };

        var historyTabContent = new StackPanel
        {
            Spacing = 14,
            Margin = new Thickness(14, 14, 16, 22),
            Children =
            {
                Card("card",
                    Heading("版本管理与修订延续"),
                    Caption("旧版本保留可追溯，重新解析会标记关联失效。选择有效版本载入后会自动切回「框选校订」页编辑并保存为新版本。"),
                    Label("选择有效修订版本"),
                    _revisionSelector,
                    ButtonRow(_loadRevisionButton)),
                Card("soft-card",
                    Heading("复核材料导出与审计历史"),
                    Caption("复制的是研究者人工复核补充材料，不是作者原文；不会改写 Paper Card 或自动调用 Lite/Pro。"),
                    ButtonRow(_copyPacketButton),
                    Label("该论文最近的人工修订（含过期关联）"),
                    _history),
            },
        };

        var proReviewTabContent = new StackPanel
        {
            Spacing = 14,
            Margin = new Thickness(14, 14, 16, 22),
            Children =
            {
                Card("tonal-card",
                    Heading("v5 · 人工复核后独立复评（Pro API）"),
                    Caption("此阶段不会重跑整篇 Lite/Pro 提取，不创建正式的新 Paper Card。"
                            + " 它只对原 Card 与人工确认的证据差异做一次独立 Pro 复评，结果保存为第二份可审计对照卡。"),
                    ButtonRow(_reviewPlanButton),
                    _reviewPlanDescription,
                    new Expander
                    {
                        Header = "查看即将发送给 Pro API 的完整文字（本地预检）",
                        IsExpanded = false,
                        Content = _outboundPayloadPreview,
                    }),
                Card("card",
                    Heading("明确授权与执行"),
                    _externalConsent,
                    ButtonRow(_reviewRunButton),
                    ButtonRow(_latestReviewButton),
                    Caption("预检不连接云端。点击执行后仅发送预检列出的原主张、相关原文与人工修订。"
                            + " 如果请求超时，请先查询历史记录，避免重复产生 API 费用。")),
            },
        };

        _sideTabs = new TabControl
        {
            Classes = { "paper-tabs" },
            ItemsSource = new[]
            {
                new TabItem { Header = "① 框选校订", Content = WrapScroll(reviewTabContent) },
                new TabItem { Header = "② 版本历史", Content = WrapScroll(historyTabContent) },
                new TabItem { Header = "③ 独立复评 (Pro)", Content = WrapScroll(proReviewTabContent) },
            },
            SelectedIndex = 0,
        };

        var sideSurface = new Border
        {
            Margin = new Thickness(6, 14, 14, 12),
            ClipToBounds = true,
            Child = _sideTabs,
        };
        sideSurface.Classes.Add("workspace-surface");

        var content = new Grid { ColumnDefinitions = new ColumnDefinitions("3*,2*") };
        Grid.SetColumn(leftPane, 0);
        Grid.SetColumn(sideSurface, 1);
        content.Children.Add(leftPane);
        content.Children.Add(sideSurface);

        var statusBar = new Border
        {
            Padding = new Thickness(16, 8),
            Child = _status,
        };
        statusBar.Classes.Add("statusbar");

        var layout = new Grid { RowDefinitions = new RowDefinitions("*,Auto") };
        Grid.SetRow(content, 0);
        Grid.SetRow(statusBar, 1);
        layout.Children.Add(content);
        layout.Children.Add(statusBar);
        Content = layout;

        KeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape && _selected is not null)
            {
                ClearSelection();
                _status.Text = "已清除当前框选。";
                e.Handled = true;
            }
        };
        Opened += async (_, _) => await LoadPageAsync(_initialPage);
        Closed += (_, _) =>
        {
            _pageCancellation?.Cancel();
            _pageCancellation?.Dispose();
            _bitmap?.Dispose();
        };
    }

    private static T Put<T>(T control, int col) where T : Control
    {
        Grid.SetColumn(control, col);
        return control;
    }

    private static ScrollViewer WrapScroll(Control content) => new()
    {
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        Content = content,
    };

    private static Border Card(string styleClass, params Control[] children)
    {
        var stack = new StackPanel { Spacing = 10 };
        foreach (var child in children) stack.Children.Add(child);
        var border = new Border
        {
            Padding = new Thickness(16, 15),
            Child = stack,
        };
        border.Classes.Add(styleClass);
        return border;
    }

    private static WrapPanel ButtonRow(params Control[] children)
    {
        var panel = new WrapPanel { ItemSpacing = 8, LineSpacing = 8 };
        foreach (var child in children) panel.Children.Add(child);
        return panel;
    }

    private static Button StyledButton(string text, string primaryClass, string? extraClass = null, bool enabled = true)
    {
        var button = new Button { Content = text, IsEnabled = enabled };
        button.Classes.Add(primaryClass);
        if (!string.IsNullOrEmpty(extraClass)) button.Classes.Add(extraClass);
        return button;
    }

    private static TextBlock Heading(string text) => new()
    {
        Text = text,
        FontWeight = FontWeight.SemiBold,
        FontSize = 15,
    };

    private static TextBlock Label(string text) => new()
    {
        Text = text,
        FontWeight = FontWeight.SemiBold,
        FontSize = 12,
        Classes = { "muted" },
    };

    private static TextBlock Caption(string text) => new()
    {
        Text = text,
        TextWrapping = TextWrapping.Wrap,
        FontSize = 11.5,
        LineHeight = 18,
        Classes = { "muted" },
    };

    private static TextBox ReadOnlyBox(double minHeight, double maxHeight = 240) => new()
    {
        IsReadOnly = true,
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MinHeight = minHeight,
        MaxHeight = maxHeight,
    };

    private async Task LoadPageAsync(int nextPage)
    {
        if (_loading || nextPage < 1 || nextPage > _totalPages) return;
        _loading = true;
        _pageCancellation?.Cancel();
        _pageCancellation?.Dispose();
        _pageCancellation = new CancellationTokenSource();
        try
        {
            var token = _pageCancellation.Token;
            _status.Text = $"正在载入 PDF 第 {nextPage} 页与 SourceBlocks…";
            var bitmapData = await _api.GetRegionReviewPageAsync(_paperId, nextPage, token);
            using var bytes = new MemoryStream(bitmapData.Png);
            var bitmap = new Bitmap(bytes);
            var source = await _api.GetRegionReviewBlocksAsync(_paperId, nextPage, token);
            _bitmap?.Dispose();
            _bitmap = bitmap;
            _image.Source = bitmap;
            _imageWidth = bitmap.PixelSize.Width;
            _imageHeight = bitmap.PixelSize.Height;
            _pageWidthPoints = bitmapData.PageWidth;
            _pageHeightPoints = bitmapData.PageHeight;
            _image.Width = _imageWidth;
            _image.Height = _imageHeight;
            _canvas.Width = _imageWidth;
            _canvas.Height = _imageHeight;
            _totalPages = Math.Max(1, bitmapData.Pages);
            _page = nextPage;
            _pageLabel.Text = $"PDF 第 {_page} / {_totalPages} 页";
            _blocks.Clear();
            var allPageTexts = new List<string>();
            if (source.TryGetProperty("source_blocks", out var blocks) &&
                blocks.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in blocks.EnumerateArray())
                {
                    var sourceText = item.GetProperty("text").GetString() ?? "";
                    allPageTexts.Add(sourceText);
                    if (!item.TryGetProperty("bbox", out var bbox) ||
                        bbox.ValueKind != JsonValueKind.Array || bbox.GetArrayLength() != 4)
                        continue;
                    var coords = bbox.EnumerateArray().Select(x => x.GetDouble()).ToArray();
                    if (coords.Any(x => !double.IsFinite(x))) continue;
                    _blocks.Add(new VisibleBlock(
                        item.GetProperty("id").GetString() ?? "",
                        sourceText,
                        new Rect(coords[0], coords[1],
                            Math.Max(0, coords[2] - coords[0]),
                            Math.Max(0, coords[3] - coords[1]))));
                }
            }
            _source.Text = string.Join("\n\n", allPageTexts);
            if (source.TryGetProperty("truncated", out var truncated) && truncated.GetBoolean())
                _source.Text += "\n\n[该页过长，前 120 个证据块之外的部分未列出]";
            ClearSelection();
            await RefreshHistoryAsync();
            if (!TryApplyEvidenceHighlight())
            {
                _status.Text = "请框选 PDF 区域。原始文字在右侧，人工确认不会改变原始证据。";
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception exc)
        {
            _status.Text = "无法载入 PDF 页面：" + exc.Message;
        }
        finally
        {
            _loading = false;
        }
    }

    /// <summary>
    /// 从 ClaimCard 点击证据跳转打开时，自动在 PDF 栅格上以琥珀高亮标记对应 SourceBlock 并滚入视野。
    /// </summary>
    private bool TryApplyEvidenceHighlight()
    {
        if (string.IsNullOrWhiteSpace(_pendingHighlightBlockId))
        {
            _highlightShape.IsVisible = false;
            return false;
        }

        var targetId = _pendingHighlightBlockId;
        _pendingHighlightBlockId = null;

        var match = _blocks.FirstOrDefault(b =>
            string.Equals(b.Id, targetId, StringComparison.OrdinalIgnoreCase));
        if (match is null || _pageWidthPoints <= 0 || _pageHeightPoints <= 0)
        {
            _highlightShape.IsVisible = false;
            _status.Text = $"已跳转到 PDF 第 {_page} 页；该证据块未记录页面坐标，请在左侧页内直接核验。";
            return true;
        }

        var scaleX = _imageWidth / _pageWidthPoints;
        var scaleY = _imageHeight / _pageHeightPoints;
        var left = Math.Max(0, match.Box.X * scaleX - 4);
        var top = Math.Max(0, match.Box.Y * scaleY - 4);
        var width = Math.Clamp(match.Box.Width * scaleX + 8, 12, Math.Max(12, _imageWidth - left));
        var height = Math.Clamp(match.Box.Height * scaleY + 8, 12, Math.Max(12, _imageHeight - top));

        Canvas.SetLeft(_highlightShape, left);
        Canvas.SetTop(_highlightShape, top);
        _highlightShape.Width = width;
        _highlightShape.Height = height;
        _highlightShape.IsVisible = true;

        _selectedSource.Text = $"[已定位原文证据块 · PDF 第 {_page} 页]\n\n{match.Text}";
        _status.Text = $"已定位到 PDF 第 {_page} 页的原文证据块（琥珀色高亮区域）。可直接核对或拖拽框选校订。";

        Dispatcher.UIThread.Post(() =>
        {
            _pdfScroller.Offset = new Vector(
                Math.Max(0, left - 48),
                Math.Max(0, top - 120));
        }, DispatcherPriority.Background);
        return true;
    }

    private void ClearSelection()
    {
        _selectionRevision++;
        _dragStart = null;
        _selected = null;
        _previewId = null;
        _loadedRevisionId = null;
        _saveRevisionButton.IsEnabled = false;
        _selectedSource.Text = "请先在左侧拖拽框选区域（或由证据卡片跳转定位）";
        _ocrResult.Text = "";
        _note.Text = "";
        _selectionShape.IsVisible = false;
        _highlightShape.IsVisible = false;
        _ocrButton.IsEnabled = false;
        _manualButton.IsEnabled = false;
        _saveButton.IsEnabled = false;
    }

    private Point Clamp(Point point) => new(
        Math.Clamp(point.X, 0, _imageWidth),
        Math.Clamp(point.Y, 0, _imageHeight));

    private void CanvasPressed(object? sender, PointerPressedEventArgs eventArgs)
    {
        if (_imageWidth <= 0 || _imageHeight <= 0 ||
            !eventArgs.GetCurrentPoint(_canvas).Properties.IsLeftButtonPressed)
            return;
        _selectionRevision++;
        _dragStart = Clamp(eventArgs.GetPosition(_canvas));
        _previewId = null;
        _loadedRevisionId = null;
        _highlightShape.IsVisible = false;
        _saveRevisionButton.IsEnabled = false;
        _saveButton.IsEnabled = false;
        _ocrButton.IsEnabled = false;
        _manualButton.IsEnabled = false;
        _ocrResult.Text = "";
        eventArgs.Pointer.Capture(_canvas);
    }

    private void CanvasMoved(object? sender, PointerEventArgs eventArgs)
    {
        if (_dragStart is not { } start) return;
        var current = Clamp(eventArgs.GetPosition(_canvas));
        var rectangle = new Rect(
            Math.Min(start.X, current.X),
            Math.Min(start.Y, current.Y),
            Math.Abs(current.X - start.X),
            Math.Abs(current.Y - start.Y));
        Canvas.SetLeft(_selectionShape, rectangle.X);
        Canvas.SetTop(_selectionShape, rectangle.Y);
        _selectionShape.Width = rectangle.Width;
        _selectionShape.Height = rectangle.Height;
        _selectionShape.IsVisible = true;
    }

    private void CanvasReleased(object? sender, PointerReleasedEventArgs eventArgs)
    {
        if (_dragStart is null) return;
        CanvasMoved(sender, eventArgs);
        eventArgs.Pointer.Capture(null);
        _dragStart = null;
        if (_selectionShape.Width < 14 || _selectionShape.Height < 14)
        {
            ClearSelection();
            _status.Text = "框选区域过小，请重新框选。";
            return;
        }
        _selected = new Rect(
            Canvas.GetLeft(_selectionShape) / _imageWidth,
            Canvas.GetTop(_selectionShape) / _imageHeight,
            _selectionShape.Width / _imageWidth,
            _selectionShape.Height / _imageHeight);
        _ocrButton.IsEnabled = true;
        _manualButton.IsEnabled = true;
        _sideTabs.SelectedIndex = 0;
        UpdateSelectedSource();
    }

    private void UpdateSelectedSource()
    {
        if (_selected is not { } rectangle) return;
        var pdfRect = new Rect(
            rectangle.X * _pageWidthPoints,
            rectangle.Y * _pageHeightPoints,
            rectangle.Width * _pageWidthPoints,
            rectangle.Height * _pageHeightPoints);
        var overlapping = _blocks.Where(b => b.Box.Intersects(pdfRect)).ToArray();
        _selectedSource.Text = overlapping.Length == 0
            ? "该区域没有可定位的 Docling SourceBlock。可以识别后人工核对，但不能据此推断原文内容。"
            : string.Join("\n\n", overlapping.Select(b => b.Text));
        _status.Text = $"框选了 PDF 第 {_page} 页的一块区域，关联 {overlapping.Length} 个来源块。";
    }

    private async Task RunOcrAsync(bool manualOnly)
    {
        if (_selected is not { } rect) return;
        var revision = _selectionRevision;
        var pageAtStart = _page;
        _ocrButton.IsEnabled = false;
        _manualButton.IsEnabled = false;
        _previewId = null;
        _saveButton.IsEnabled = false;
        _status.Text = manualOnly
            ? "创建人工抄录草稿；不启动 OCR 模型…"
            : "只识别当前框选区域；OCR 候选不会自动写入研究库…";
        try
        {
            var result = await _api.PreviewPdfRegionAsync(
                _paperId, pageAtStart, rect.X, rect.Y, rect.Right, rect.Bottom,
                manualOnly: manualOnly);
            if (_selectionRevision != revision || _page != pageAtStart)
            {
                _status.Text = "框选区域已变更：旧识别结果已丢弃，请对新区域重新识别。";
                return;
            }
            _previewId = result.GetProperty("preview_id").GetString();
            _ocrResult.Text = result.GetProperty("ocr_candidate").GetString() ?? "";
            _selectedSource.Text = result.GetProperty("original_text").GetString() ?? "";
            _saveButton.IsEnabled = !string.IsNullOrEmpty(_previewId);
            _status.Text = string.IsNullOrWhiteSpace(_ocrResult.Text)
                ? "OCR 未识别到文字；如已从 PDF 人工核实，可以直接输入修订后确认。"
                : "OCR 已生成候选。请对照左侧 PDF 逐字检查，确认后才会保存为研究者修订。";
        }
        catch (Exception exc)
        {
            _status.Text = "局部 OCR 失败：" + exc.Message;
        }
        finally
        {
            _ocrButton.IsEnabled = _selected is not null;
            _manualButton.IsEnabled = _selected is not null;
        }
    }

    private async Task ConfirmAsync()
    {
        if (_previewId is null || string.IsNullOrWhiteSpace(_ocrResult.Text))
        {
            _status.Text = "请填写并核对修订文字后再保存。";
            return;
        }
        _saveButton.IsEnabled = false;
        try
        {
            var response = await _api.ConfirmPdfRegionAsync(
                _paperId, _previewId, _ocrResult.Text, _note.Text ?? "");
            _lastReviewId = response.GetProperty("id").GetString();
            _previewId = null;
            _undoButton.IsEnabled = !string.IsNullOrEmpty(_lastReviewId);
            _status.Text = "已保存人工复核记录。原始 SourceBlock 和 Paper Card 未被修改。";
            await RefreshHistoryAsync();
        }
        catch (Exception exc)
        {
            _saveButton.IsEnabled = _previewId is not null;
            _status.Text = "保存修订失败：" + exc.Message;
        }
    }

    private async Task RevokeLastAsync()
    {
        if (string.IsNullOrEmpty(_lastReviewId)) return;
        try
        {
            await _api.RevokePdfRegionReviewAsync(_paperId, _lastReviewId);
            _lastReviewId = null;
            _undoButton.IsEnabled = false;
            _status.Text = "该人工修订已撤回；撤回时间保留在审计记录中。";
            await RefreshHistoryAsync();
        }
        catch (Exception exc)
        {
            _status.Text = "撤回失败：" + exc.Message;
        }
    }

    private void ResetReviewRunPlan()
    {
        _reviewRunPlanDigest = null;
        _reviewRunButton.IsEnabled = false;
        _externalConsent.IsChecked = false;
        _outboundPayloadPreview.Text = "";
        _reviewPlanDescription.Text = "人工修订或 PDF 状态可能已更新，请重新预检复评范围。";
    }

    private async Task PrepareReviewedComparisonAsync()
    {
        _reviewPlanButton.IsEnabled = false;
        ResetReviewRunPlan();
        try
        {
            var plan = await _api.PreviewReviewedComparisonAsync(_paperId);
            var digest = plan.GetProperty("snapshot_digest").GetString();
            var count = plan.GetProperty("review_count").GetInt32();
            var model = plan.GetProperty("pro_model").GetString();
            var provider = plan.GetProperty("provider").GetString();
            var input = plan.GetProperty("approximate_input_chars").GetInt32();
            var maxOutput = plan.GetProperty("maximum_output_tokens").GetInt32();
            if (string.IsNullOrWhiteSpace(digest))
                throw new InvalidOperationException("预检未返回有效的内容指纹。");
            _reviewRunPlanDigest = digest;
            _outboundPayloadPreview.Text =
                plan.GetProperty("outbound_payload").GetString() ?? "";
            _reviewPlanDescription.Text =
                $"模型：{model} · 服务：{provider}\n"
                + $"人工修订：{count} 条 · 输入约 {input:N0} 字符 · "
                + $"输出上限 {maxOutput:N0} tokens。\n"
                + "费用以供应商实际 token 计费为准。不会上传完整 PDF，"
                + "也不会覆盖原 Paper Card。";
            _reviewRunButton.IsEnabled = true;
            _status.Text = "预检完成，尚未调用任何模型。请审查范围并单独确认外发与费用。";
        }
        catch (Exception exc)
        {
            _reviewPlanDescription.Text = "复评预检未通过：" + exc.Message;
            _status.Text = "请先解决缺失或过期的人工修订，然后重新预检。";
        }
        finally
        {
            _reviewPlanButton.IsEnabled = true;
        }
    }

    private async Task RunReviewedComparisonAsync()
    {
        if (string.IsNullOrEmpty(_reviewRunPlanDigest))
        {
            _status.Text = "请先预检，获取当前论文、原 Card 和人工修订的内容指纹。";
            return;
        }
        if (_externalConsent.IsChecked != true)
        {
            _status.Text = "未同意发送研究摘录及可能产生的 API 费用，未发起请求。";
            return;
        }
        _reviewRunButton.IsEnabled = false;
        _reviewPlanButton.IsEnabled = false;
        var digest = _reviewRunPlanDigest;
        try
        {
            _status.Text = "正在调用 Pro API 做一次独立复评；可能需要几分钟，勿重复提交…";
            var saved = await _api.RunReviewedComparisonAsync(
                _paperId, digest, true, true);
            var reportId = saved.GetProperty("id").GetString();
            if (string.IsNullOrWhiteSpace(reportId))
                throw new InvalidOperationException("服务器未返回保存的复评 ID。");
            var detail = await _api.GetReviewedComparisonAsync(_paperId, reportId);
            new ReviewedComparisonWindow(detail).Show(this);
            _status.Text = "独立复评已保存；原 Paper Card 与研究证据没有改变。";
            ResetReviewRunPlan();
        }
        catch (Exception exc)
        {
            // 模型超时后绝不自动启用重试，避免重复扣费；请先检查历史记录。
            ResetReviewRunPlan();
            _status.Text = "复评未确认成功：" + exc.Message
                           + "。请先查看历史记录，勿直接重试扣费请求。";
        }
        finally
        {
            _reviewPlanButton.IsEnabled = true;
        }
    }

    private async Task OpenLatestReviewedComparisonAsync()
    {
        _latestReviewButton.IsEnabled = false;
        try
        {
            var history = await _api.ListReviewedComparisonsAsync(_paperId);
            if (!history.TryGetProperty("reports", out var reports) ||
                reports.ValueKind != JsonValueKind.Array ||
                reports.GetArrayLength() == 0)
            {
                _status.Text = "尚无保存的独立 Pro 复评。先完成预检与明确授权。";
                return;
            }
            var first = reports.EnumerateArray().First();
            var id = first.GetProperty("id").GetString() ?? "";
            var detail = await _api.GetReviewedComparisonAsync(_paperId, id);
            new ReviewedComparisonWindow(detail).Show(this);
            _status.Text = "已打开历史对照报告；它是保存时的分析快照。";
        }
        catch (Exception exc)
        {
            _status.Text = "无法打开复评报告：" + exc.Message;
        }
        finally
        {
            _latestReviewButton.IsEnabled = true;
        }
    }

    private void LoadSelectedRevision()
    {
        if (_revisionSelector.SelectedItem is not ReviewChoice choice)
        {
            _status.Text = "请先选择需要修改的有效修订版本。";
            return;
        }
        // 显式载入历史版本，并切回「框选校订」页便于直接编辑和保存为新版本。
        _loadedRevisionId = choice.Id;
        _loadedRevisionNumber = choice.Revision;
        _previewId = null;
        _saveButton.IsEnabled = false;
        _ocrResult.Text = choice.Text;
        _note.Text = choice.Note;
        _saveRevisionButton.IsEnabled = true;
        _sideTabs.SelectedIndex = 0;
        _status.Text = $"已载入 PDF p.{choice.Page} 的 v{choice.Revision}。请编辑后点击「保存为新版本（保留历史）」生成 v{choice.Revision + 1}。";
    }

    private async Task SaveNewRevisionAsync()
    {
        if (string.IsNullOrWhiteSpace(_loadedRevisionId) || string.IsNullOrWhiteSpace(_ocrResult.Text))
        {
            _status.Text = "请先载入一条有效修订，并填写核对后的文字。";
            return;
        }
        _saveRevisionButton.IsEnabled = false;
        try
        {
            var updated = await _api.RevisePdfRegionReviewAsync(
                _paperId, _loadedRevisionId, _loadedRevisionNumber,
                _ocrResult.Text, _note.Text ?? "");
            _lastReviewId = updated.GetProperty("id").GetString();
            _undoButton.IsEnabled = !string.IsNullOrEmpty(_lastReviewId);
            _loadedRevisionId = null;
            _status.Text = "新版人工修订已保存；上一版本仍保留。原始来源和既有 AI 分析没有改变。";
            await RefreshHistoryAsync();
        }
        catch (Exception exc)
        {
            _saveRevisionButton.IsEnabled = true;
            _status.Text = "修订失败（可能论文已重新解析，请刷新记录）：" + exc.Message;
        }
    }

    private async Task CopyEvidencePacketAsync()
    {
        _copyPacketButton.IsEnabled = false;
        try
        {
            var report = await _api.GetPdfRegionReviewPacketAsync(_paperId);
            var markdown = report.GetProperty("markdown").GetString() ?? "";
            var clipboard = TopLevel.GetTopLevel(this)?.Clipboard;
            if (clipboard is null)
                throw new InvalidOperationException("当前桌面环境不可访问剪贴板");
            await clipboard.SetTextAsync(markdown);
            var included = report.GetProperty("included").GetInt32();
            var held = report.GetProperty("held_for_review").GetInt32();
            _status.Text = $"已复制人工复核材料：{included} 条关联有效，{held} 条需要重核。不会自动调用 AI。";
        }
        catch (Exception exc)
        {
            _status.Text = "复制复核材料失败：" + exc.Message;
        }
        finally
        {
            _copyPacketButton.IsEnabled = true;
        }
    }

    private async Task RefreshHistoryAsync()
    {
        // 修订、撤回或重新选择证据均使旧预检计划失效。
        ResetReviewRunPlan();
        try
        {
            var json = await _api.GetPdfRegionReviewsAsync(_paperId);
            var lines = new List<string>();
            var choices = new List<ReviewChoice>();
            if (json.TryGetProperty("reviews", out var items) && items.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in items.EnumerateArray().Take(40))
                {
                    var page = item.GetProperty("page_number").GetInt32();
                    var status = item.GetProperty("provenance").GetString() ?? "";
                    var text = item.GetProperty("corrected_text").GetString() ?? "";
                    var note = item.GetProperty("researcher_note").GetString() ?? "";
                    var id = item.GetProperty("id").GetString() ?? "";
                    var version = item.TryGetProperty("revision_no", out var v)
                        ? v.GetInt32() : 1;
                    var link = item.TryGetProperty("link_status", out var linkValue)
                        ? linkValue.GetString() ?? "UNKNOWN" : "UNKNOWN";
                    var active = item.TryGetProperty("active", out var activeValue)
                        && activeValue.ValueKind == JsonValueKind.True;
                    lines.Add($"PDF p.{page} · v{version} · {status} · 来源 {link}\n{text}");
                    if (active && (link == "CURRENT" || link == "PAGE_ONLY"))
                        choices.Add(new ReviewChoice(id, version, page, text, note, link));
                }
            }
            _history.Text = lines.Count == 0 ? "暂无人工确认记录。" : string.Join("\n\n", lines);
            _revisionSelector.ItemsSource = choices;
            _revisionSelector.SelectedIndex = choices.Count > 0 ? 0 : -1;
            _loadRevisionButton.IsEnabled = choices.Count > 0;
        }
        catch (Exception exc)
        {
            _history.Text = "无法读取复核历史：" + exc.Message;
        }
    }
}
