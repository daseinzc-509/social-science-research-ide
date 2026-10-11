using System.Globalization;
using System.Text;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Controls.Shapes;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using SRA.Desktop.Services;

namespace SRA.Desktop.Views;

/// <summary>
/// Displays the authoritative PDF raster beside the original Docling source
/// blocks. A selected rectangle can be OCR'd into an unverified proposal;
/// the researcher's confirmation is an independent, revocable annotation.
/// Original PDF/source blocks/Paper Card are never modified by this window.
/// </summary>
public sealed class RegionReviewWindow : Window
{
    private sealed record VisibleBlock(string Id, string Text, Rect Box);

    private readonly SraApiClient _api;
    private readonly string _paperId;
    private readonly Canvas _canvas = new() { Background = Brushes.Transparent };
    private readonly Image _image = new() { Stretch = Stretch.None };
    private readonly Rectangle _selectionShape = new()
    {
        Stroke = Brushes.DeepSkyBlue,
        StrokeThickness = 2,
        Fill = new SolidColorBrush(Color.FromArgb(38, 0, 135, 255)),
        IsVisible = false,
    };
    private readonly TextBlock _pageLabel = new() { Text = "载入 PDF…" };
    private readonly TextBlock _status = new()
    {
        Text = "框选左侧 PDF 的一段文字或表格，再运行局部 OCR。",
        TextWrapping = TextWrapping.Wrap,
    };
    private readonly TextBox _source = ReadOnlyBox(130);
    private readonly TextBox _selectedSource = ReadOnlyBox(115);
    private readonly TextBox _ocrResult = new()
    {
        Watermark = "识别后的候选文字将在此显示；请对照左侧原文手动校正。",
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MinHeight = 120,
        MaxHeight = 300,
    };
    private readonly TextBox _note = new()
    {
        Watermark = "可选：写下修正理由，例如「原文中的样本量为 4,519」。",
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MinHeight = 44,
    };
    private readonly TextBox _history = ReadOnlyBox(120);
    private readonly Button _ocrButton = new() { Content = "识别选中区域", IsEnabled = false };
    private readonly Button _manualButton = new() { Content = "仅人工录入（跳过 OCR）", IsEnabled = false };
    private readonly Button _saveButton = new() { Content = "确认并保存人工修订", IsEnabled = false };
    private readonly Button _undoButton = new() { Content = "撤回刚保存的修订", IsEnabled = false };

    private Bitmap? _bitmap;
    private readonly List<VisibleBlock> _blocks = [];
    private CancellationTokenSource? _pageCancellation;
    private Point? _dragStart;
    private Rect? _selected;
    private string? _previewId;
    private string? _lastReviewId;
    private int _page = 1;
    private int _totalPages = 1;
    private double _imageWidth;
    private double _imageHeight;
    private double _pageWidthPoints;
    private double _pageHeightPoints;
    private bool _loading;
    private int _selectionRevision;

    public RegionReviewWindow(SraApiClient api, string paperId, string title)
    {
        _api = api;
        _paperId = paperId;
        Title = "SRA · 原文对照 / 局部 OCR · " + title;
        Width = 1370;
        Height = 880;
        MinWidth = 980;
        MinHeight = 680;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;

        _canvas.Children.Add(_image);
        _canvas.Children.Add(_selectionShape);
        _canvas.PointerPressed += CanvasPressed;
        _canvas.PointerMoved += CanvasMoved;
        _canvas.PointerReleased += CanvasReleased;

        var previous = new Button { Content = "上一页" };
        previous.Click += async (_, _) => await LoadPageAsync(Math.Max(1, _page - 1));
        var next = new Button { Content = "下一页" };
        next.Click += async (_, _) => await LoadPageAsync(Math.Min(_totalPages, _page + 1));
        var refresh = new Button { Content = "刷新" };
        refresh.Click += async (_, _) => await LoadPageAsync(_page);
        _ocrButton.Click += async (_, _) => await RunOcrAsync(manualOnly: false);
        _manualButton.Click += async (_, _) => await RunOcrAsync(manualOnly: true);
        _saveButton.Click += async (_, _) => await ConfirmAsync();
        _undoButton.Click += async (_, _) => await RevokeLastAsync();
        var tools = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            Spacing = 10,
            Margin = new Thickness(12, 10),
            Children = { previous, _pageLabel, next, refresh },
        };

        var pdfScroller = new ScrollViewer
        {
            HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = _canvas,
        };
        var leftPane = new Grid { RowDefinitions = new RowDefinitions("Auto,*") };
        Grid.SetRow(tools, 0);
        Grid.SetRow(pdfScroller, 1);
        leftPane.Children.Add(tools);
        leftPane.Children.Add(pdfScroller);

        var side = new StackPanel
        {
            Spacing = 10,
            Margin = new Thickness(14, 12),
            Children =
            {
                Heading("对照与局部复核"),
                Caption("左侧为 PDF 原始页面；右侧原始文字来自 Docling，不因新 OCR 自动改变。"),
                Heading("该页已提取的原始文字"),
                _source,
                Heading("当前框选区域对应的原始证据"),
                _selectedSource,
                _ocrButton,
                _manualButton,
                Heading("OCR 候选 / 人工录入（需核对）"),
                _ocrResult,
                Heading("研究者修订说明"),
                _note,
                _saveButton,
                Caption("保存的是独立的人工修订记录，不覆盖 PDF、Docling SourceBlock 或旧 Paper Card。"),
                _undoButton,
                Heading("该论文最近的人工修订"),
                _history,
            },
        };
        var sideScroller = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = side,
        };
        var content = new Grid { ColumnDefinitions = new ColumnDefinitions("3*,2*") };
        Grid.SetColumn(leftPane, 0);
        Grid.SetColumn(sideScroller, 1);
        content.Children.Add(leftPane);
        content.Children.Add(sideScroller);
        var layout = new Grid { RowDefinitions = new RowDefinitions("*,Auto") };
        Grid.SetRow(content, 0);
        Grid.SetRow(_status, 1);
        _status.Margin = new Thickness(14, 8);
        layout.Children.Add(content);
        layout.Children.Add(_status);
        Content = layout;
        Opened += async (_, _) => await LoadPageAsync(1);
        Closed += (_, _) =>
        {
            _pageCancellation?.Cancel();
            _pageCancellation?.Dispose();
            _bitmap?.Dispose();
        };
    }

    private static TextBlock Heading(string text) => new()
    {
        Text = text,
        FontWeight = FontWeight.SemiBold,
        FontSize = 14,
    };

    private static TextBlock Caption(string text) => new()
    {
        Text = text,
        TextWrapping = TextWrapping.Wrap,
        Opacity = 0.75,
        FontSize = 11,
    };

    private static TextBox ReadOnlyBox(double minHeight) => new()
    {
        IsReadOnly = true,
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MinHeight = minHeight,
        MaxHeight = 260,
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
            _status.Text = "加载原始 PDF 页面与 SourceBlocks…";
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
            _status.Text = "请框选 PDF 区域。原始文字在右侧，人工确认不会改变原始证据。";
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

    private void ClearSelection()
    {
        _selectionRevision++;
        _dragStart = null;
        _selected = null;
        _previewId = null;
        _selectedSource.Text = "请先框选左侧区域";
        _ocrResult.Text = "";
        _note.Text = "";
        _selectionShape.IsVisible = false;
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

    private async Task RefreshHistoryAsync()
    {
        try
        {
            var json = await _api.GetPdfRegionReviewsAsync(_paperId);
            var lines = new List<string>();
            if (json.TryGetProperty("reviews", out var items) && items.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in items.EnumerateArray().Take(30))
                {
                    var page = item.GetProperty("page_number").GetInt32();
                    var status = item.GetProperty("provenance").GetString();
                    var text = item.GetProperty("corrected_text").GetString() ?? "";
                    lines.Add($"PDF p.{page} · {status}\n{text}");
                }
            }
            _history.Text = lines.Count == 0 ? "暂无人工确认记录。" : string.Join("\n\n", lines);
        }
        catch (Exception exc)
        {
            _history.Text = "无法读取复核历史：" + exc.Message;
        }
    }
}
