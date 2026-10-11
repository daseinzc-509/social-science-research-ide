using System.Globalization;
using System.Text;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input.Platform;
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
        PlaceholderText = "识别后的候选文字将在此显示；请对照左侧原文手动校正。",
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MinHeight = 120,
        MaxHeight = 300,
    };
    private readonly TextBox _note = new()
    {
        PlaceholderText = "可选：写下修正理由，例如「原文中的样本量为 4,519」。",
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MinHeight = 44,
    };
    private readonly TextBox _history = ReadOnlyBox(120);
    private readonly Button _ocrButton = new() { Content = "识别选中区域", IsEnabled = false };
    private readonly Button _manualButton = new() { Content = "仅人工录入（跳过 OCR）", IsEnabled = false };
    private readonly Button _saveButton = new() { Content = "确认并保存人工修订", IsEnabled = false };
    private readonly Button _undoButton = new() { Content = "撤回刚保存的修订", IsEnabled = false };
    private readonly ComboBox _revisionSelector = new() { MinWidth = 240 };
    private readonly Button _loadRevisionButton = new()
    {
        Content = "载入选中修订，准备修改", IsEnabled = false,
    };
    private readonly Button _saveRevisionButton = new()
    {
        Content = "保存为新版本（保留历史）", IsEnabled = false,
    };
    private readonly Button _copyPacketButton = new()
    {
        Content = "复制人工复核材料（不会自动分析）",
    };

    private readonly Button _reviewPlanButton = new()
    {
        Content = "① 预检复评范围（不调用 API）",
    };
    private readonly TextBlock _reviewPlanDescription = new()
    {
        Text = "尚未预检。需要先确认人工修订与原始证据关联均有效。",
        TextWrapping = TextWrapping.Wrap,
    };
    private readonly TextBox _outboundPayloadPreview = new()
    {
        IsReadOnly = true,
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        MaxHeight = 225,
        MinHeight = 100,
    };
    private readonly CheckBox _externalConsent = new()
    {
        Content = "我确认发送列出的原文摘录、原 Card 主张和人工修订到已配置的 Pro API，并知晓可能产生费用。",
        IsChecked = false,
    };
    private readonly Button _reviewRunButton = new()
    {
        Content = "② 明确授权并执行一次 Pro 独立复评",
        IsEnabled = false,
    };
    private readonly Button _latestReviewButton = new()
    {
        Content = "打开最近一次独立复评（左右对照）",
    };
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
        _loadRevisionButton.Click += (_, _) => LoadSelectedRevision();
        _saveRevisionButton.Click += async (_, _) => await SaveNewRevisionAsync();
        _copyPacketButton.Click += async (_, _) => await CopyEvidencePacketAsync();
        _reviewPlanButton.Click += async (_, _) => await PrepareReviewedComparisonAsync();
        _reviewRunButton.Click += async (_, _) => await RunReviewedComparisonAsync();
        _latestReviewButton.Click += async (_, _) => await OpenLatestReviewedComparisonAsync();
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
                Heading("历史版本与证据关联"),
                Caption("旧版本保留可追溯，重新解析会标记关联失效。只允许修改仍指向当前 SourceBlock 的有效修订。"),
                _revisionSelector,
                _loadRevisionButton,
                _saveRevisionButton,
                _copyPacketButton,
                Caption("复制的是研究者人工复核补充材料，不是作者原文；不会改写 Paper Card 或自动调用 Lite/Pro。"),
                Heading("该论文最近的人工修订（含过期关联）"),
                _history,
                Heading("v5 · 人工复核后独立复评（Pro API）"),
                Caption("此阶段不会重跑整篇 Lite/Pro 提取，不创建正式的新 Paper Card。"
                        + " 它只对原 Card 与人工确认的证据差异做一次独立 Pro 复评，结果保存为第二份可审计对照卡。"),
                _reviewPlanButton,
                _reviewPlanDescription,
                new Expander
                {
                    Header = "查看即将发送给 Pro API 的完整文字（本地预检）",
                    IsExpanded = false,
                    Content = _outboundPayloadPreview,
                },
                _externalConsent,
                _reviewRunButton,
                _latestReviewButton,
                Caption("预检不连接云端。点击执行后仅发送预检列出的原主张、相关原文与人工修订。"
                        + " 如果请求超时，请先查询历史记录，避免重复产生 API 费用。"),
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
        _loadedRevisionId = null;
        _saveRevisionButton.IsEnabled = false;
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
        _loadedRevisionId = null;
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
            // After a model timeout, do not blindly enable Retry: it could
            // duplicate a billed request. Check history first.
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
        // Loading is deliberate: do not clobber a new OCR candidate just
        // because the history selection changes.
        _loadedRevisionId = choice.Id;
        _loadedRevisionNumber = choice.Revision;
        _previewId = null;
        _saveButton.IsEnabled = false;
        _ocrResult.Text = choice.Text;
        _note.Text = choice.Note;
        _saveRevisionButton.IsEnabled = true;
        _status.Text = $"已载入 PDF p.{choice.Page} 的 v{choice.Revision}。修改文字并保存为 v{choice.Revision + 1}；旧版本会保留。";
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
        // Revising/revoking/selecting new evidence invalidates the old plan.
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
