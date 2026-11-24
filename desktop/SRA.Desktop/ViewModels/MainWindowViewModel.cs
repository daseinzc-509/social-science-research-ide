using System.Collections.ObjectModel;
using System.Text.Json;
using Avalonia.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using SRA.Desktop;
using SRA.Desktop.Models;
using SRA.Desktop.Services;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// 主工作区视图模型核心状态、初始化、更新检测与任务生命周期管理。
/// 论文库、Paper Card 投影与任务中心/维护操作已分别拆分至
/// MainWindowViewModel.Library.cs、.Projection.cs 与 .Workspace.cs。
/// </summary>
public partial class MainWindowViewModel : ObservableObject
{
    private readonly SraApiClient _api;
    private readonly Task _backendReadyTask;
    private readonly DesktopPreferencesService _preferences;
    private readonly DesktopUpdateService _updates = new();
    private readonly List<PaperSummary> _allPapers = [];
    private CancellationTokenSource? _selectionCts;

    public MainWindowViewModel(
        SraApiClient api,
        DesktopPreferencesService? preferences = null,
        DesktopPreferences? snapshot = null,
        Task? backendReadyTask = null)
    {
        _api = api;
        _backendReadyTask = backendReadyTask ?? Task.CompletedTask;
        _preferences = preferences ?? new DesktopPreferencesService();
        snapshot ??= _preferences.Load();
        themeMode = snapshot.ThemeMode;
        showInspector = snapshot.ShowInspector;
        checkForUpdatesAtStartup = snapshot.CheckForUpdatesAtStartup;
        includePrereleaseUpdates = snapshot.IncludePrereleaseUpdates;
        Settings = new SettingsWindowViewModel(api);
        App.ApplyThemeMode(themeMode);
        _ = InitializeAsync();
        if (checkForUpdatesAtStartup) _ = CheckForUpdatesAsync(silent: true);
    }

    public SraApiClient Api => _api;
    public SettingsWindowViewModel Settings { get; }
    public IReadOnlyList<string> ThemeModes { get; } = ["跟随系统", "浅色", "深色"];
    public ObservableCollection<PaperSummary> Papers { get; } = [];
    public ObservableCollection<string> Keywords { get; } = [];
    public ObservableCollection<ClaimItem> MetadataClaims { get; } = [];
    public ObservableCollection<ClaimItem> BasicFacts { get; } = [];
    public ObservableCollection<ClaimItem> ResearchQuestions { get; } = [];
    public ObservableCollection<ClaimItem> TheoryConcepts { get; } = [];
    public ObservableCollection<ClaimItem> ResearchDesignClaims { get; } = [];
    public ObservableCollection<ClaimItem> MajorFindings { get; } = [];
    public ObservableCollection<ClaimItem> AuthorExplanations { get; } = [];
    public ObservableCollection<ClaimItem> AuthorLimitations { get; } = [];
    public ObservableCollection<ClaimItem> AnalysisClaims { get; } = [];
    public ObservableCollection<ClaimItem> AiLimitations { get; } = [];
    public ObservableCollection<ClaimItem> ReadingRecommendations { get; } = [];
    public ObservableCollection<ReferenceItem> References { get; } = [];
    public ObservableCollection<TableItem> Tables { get; } = [];
    public ObservableCollection<VisualSourceItem> Formulas { get; } = [];
    public ObservableCollection<VisualSourceItem> Figures { get; } = [];

    [ObservableProperty] private PaperSummary? selectedPaper;
    [ObservableProperty] private PaperDetailResponse? selectedDetail;
    [ObservableProperty] private string searchText = "";
    [ObservableProperty] private string apiStatusText = "正在连接本地 API…";
    [ObservableProperty] private string statusText = "准备就绪";
    [ObservableProperty] private bool isBusy;
    [ObservableProperty] private string librarySummary = "0 papers";
    [ObservableProperty] private bool isLibraryEmpty = true;
    [ObservableProperty] private string selectedTitle = "选择一篇论文";
    [ObservableProperty] private string selectedSubtitle = "从左侧论文库开始你的阅读。";
    [ObservableProperty] private string selectedStudyType = "—";
    [ObservableProperty] private string selectedStudyMethod = "—";
    [ObservableProperty] private string selectedIdentification = "—";
    [ObservableProperty] private string studyRationale = "";
    [ObservableProperty] private string selectedParseStatus = "尚未选择文档";
    [ObservableProperty] private string selectedAnalysisState = "未选择";
    [ObservableProperty] private string selectedFactsCount = "—";
    [ObservableProperty] private string selectedEvidenceCount = "—";
    [ObservableProperty] private string selectedReviewsCount = "—";
    [ObservableProperty] private string metadataTitle = "—";
    [ObservableProperty] private string metadataAuthors = "—";
    [ObservableProperty] private string metadataJournal = "—";
    [ObservableProperty] private string metadataYear = "—";
    [ObservableProperty] private string metadataVolumeIssue = "—";
    [ObservableProperty] private string metadataPages = "—";
    [ObservableProperty] private string metadataDoi = "—";
    [ObservableProperty] private string abstractText = "尚未提取摘要。";
    [ObservableProperty] private string warningsText = "";
    [ObservableProperty] private string warningsSummary = "暂无提取警告";
    [ObservableProperty] private bool hasWarnings;
    [ObservableProperty] private bool hasManualMetadata;
    [ObservableProperty] private string manualMetadataNote = "";
    [ObservableProperty] private string reviewPlanText = "—";
    [ObservableProperty] private string referenceStatusText = "尚未提取参考文献。";
    [ObservableProperty] private string rawCardJson = "尚未选择论文。";
    [ObservableProperty] private string jobLog = "没有正在运行的任务。";
    [ObservableProperty] private string jobStage = "准备就绪";
    [ObservableProperty] private double jobProgress;
    [ObservableProperty] private bool jobIndeterminate;
    [ObservableProperty] private string? activeJobId;
    [ObservableProperty] private bool isCancellingJob;
    [ObservableProperty] private string researchContext = "";
    [ObservableProperty] private string excludeAfterText = "";
    [ObservableProperty] private string themeMode = "跟随系统";
    [ObservableProperty] private bool showInspector = true;
    [ObservableProperty] private bool checkForUpdatesAtStartup = true;
    [ObservableProperty] private bool includePrereleaseUpdates;
    [ObservableProperty] private bool isCheckingForUpdates;
    [ObservableProperty] private bool hasUpdateAvailable;
    [ObservableProperty] private string updateStatusText = "尚未检查更新。";
    [ObservableProperty] private string? updateReleaseUrl;
    [ObservableProperty] private string dashboardPapers = "—";
    [ObservableProperty] private string dashboardAnalyzed = "—";
    [ObservableProperty] private string dashboardPending = "—";
    [ObservableProperty] private string dashboardReview = "—";
    [ObservableProperty] private string dashboardReferences = "—";
    [ObservableProperty] private string dashboardOutput = "任务中心尚未加载。";
    [ObservableProperty] private string maintenanceOutput = "数据库健康状态尚未加载。";

    public string InstalledDesktopVersion => _updates.InstalledVersionLabel;
    public bool CanOpenUpdateRelease => !string.IsNullOrWhiteSpace(UpdateReleaseUrl);
    public bool HasSelection => SelectedPaper is not null;
    public bool HasCard => SelectedPaper?.HasCard == true;
    public bool HasStudyRationale => !string.IsNullOrWhiteSpace(StudyRationale);
    public bool HasNoTables => Tables.Count == 0;
    public bool HasNoVisuals => Formulas.Count == 0 && Figures.Count == 0;
    public bool CanRunPaperActions => HasSelection && !IsBusy;
    public bool CanCancelJob => !string.IsNullOrWhiteSpace(ActiveJobId) && !IsCancellingJob;
    public string AnalyzeButtonText => HasCard ? "重新分析" : "开始分析";
    public Uri? SelectedPdfUri => SelectedPaper is null ? null : _api.PaperPdfUri(SelectedPaper.Id);

    private async Task InitializeAsync()
    {
        try
        {
            if (!_backendReadyTask.IsCompleted) StatusText = "正在启动本地研究引擎…";
            await _backendReadyTask;
            var health = await _api.GetHealthAsync();
            ApiStatusText = health.Status == "ok" ? $"API 已连接 · {health.ApiVersion}" : $"API 状态：{health.Status}";
            await RefreshAsync();
        }
        catch (Exception exc)
        {
            ApiStatusText = "API 未连接";
            StatusText = "本地研究引擎启动失败：" + exc.Message;
            JobLog = exc.ToString();
        }
    }

    partial void OnSelectedPaperChanged(PaperSummary? value)
    {
        RaiseSelectionState();
        _selectionCts?.Cancel();
        _selectionCts?.Dispose();
        _selectionCts = new CancellationTokenSource();
        _ = LoadSelectedPaperAsync(value, _selectionCts.Token);
    }

    partial void OnIsBusyChanged(bool value) => RaiseSelectionState();
    partial void OnActiveJobIdChanged(string? value) => OnPropertyChanged(nameof(CanCancelJob));
    partial void OnIsCancellingJobChanged(bool value) => OnPropertyChanged(nameof(CanCancelJob));
    partial void OnStudyRationaleChanged(string value) => OnPropertyChanged(nameof(HasStudyRationale));
    partial void OnSearchTextChanged(string value) => ApplyFilter(value);

    partial void OnThemeModeChanged(string value)
    {
        App.ApplyThemeMode(value);
        SaveDesktopPreferences();
    }

    partial void OnShowInspectorChanged(bool value) => SaveDesktopPreferences();
    partial void OnCheckForUpdatesAtStartupChanged(bool value) => SaveDesktopPreferences();
    partial void OnIncludePrereleaseUpdatesChanged(bool value) => SaveDesktopPreferences();
    partial void OnUpdateReleaseUrlChanged(string? value) => OnPropertyChanged(nameof(CanOpenUpdateRelease));

    /// <summary>Check the public GitHub release list only. Never install silently.</summary>
    public async Task CheckForUpdatesAsync(bool silent = false)
    {
        if (IsCheckingForUpdates) return;
        IsCheckingForUpdates = true;
        if (!silent) UpdateStatusText = "正在检查 GitHub 版本…";
        try
        {
            var latest = await _updates.FindUpdateAsync(IncludePrereleaseUpdates);
            UpdateReleaseUrl = latest?.PageUri.AbsoluteUri;
            HasUpdateAvailable = latest is not null;
            UpdateStatusText = latest is null
                ? $"已是最新的可用版本（当前 {InstalledDesktopVersion}）。"
                : $"发现新版 {latest.Tag}，请在 GitHub 下载经校验的安装包。";
        }
        catch (Exception exception)
        {
            if (!silent) UpdateStatusText = $"检查失败：{exception.Message}";
            // Offline/GitHub down must never prevent the research workspace from starting.
        }
        finally
        {
            IsCheckingForUpdates = false;
        }
    }

    private void SaveDesktopPreferences()
    {
        _preferences.Save(new DesktopPreferences
        {
            ThemeMode = ThemeMode,
            ShowInspector = ShowInspector,
            CheckForUpdatesAtStartup = CheckForUpdatesAtStartup,
            IncludePrereleaseUpdates = IncludePrereleaseUpdates,
        });
    }

    private void RaiseSelectionState()
    {
        OnPropertyChanged(nameof(HasSelection));
        OnPropertyChanged(nameof(HasCard));
        OnPropertyChanged(nameof(CanRunPaperActions));
        OnPropertyChanged(nameof(AnalyzeButtonText));
        OnPropertyChanged(nameof(SelectedPdfUri));
    }

    private void ProjectTables(JsonElement card)
    {
        Tables.Clear();
        if (card.TryGetProperty("tables", out var tables) && tables.ValueKind == JsonValueKind.Array)
        {
            foreach (var table in tables.EnumerateArray())
            {
                if (table.ValueKind == JsonValueKind.Object)
                {
                    Tables.Add(TableItem.FromJson(table));
                }
            }
        }
        OnPropertyChanged(nameof(HasNoTables));
    }

    public async Task PreviewFigureAsync(VisualSourceItem figure)
    {
        if (SelectedPaper is null || string.IsNullOrWhiteSpace(figure.SourceId))
        {
            figure.AiResult = "请先选择具有可靠图像来源 ID 的论文。";
            return;
        }
        var paperId = SelectedPaper.Id;
        figure.AiResult = "正在本地预览图像区域（不调用模型、不收费）…";
        try
        {
            var data = await _api.GetFigurePreviewAsync(paperId, figure.SourceId);
            if (SelectedPaper?.Id != paperId) return;
            using var stream = new MemoryStream(data, writable: false);
            figure.PreviewImage = new Bitmap(stream);
            figure.AiResult = "本地预览成功。确认裁剪区域正确后才需要付费调用 Vision。";
        }
        catch (Exception exc)
        {
            figure.AiResult = "本地图片预览失败：" + exc.Message;
        }
    }

    public async Task AnalyzeFigureAsync(VisualSourceItem figure)
    {
        if (SelectedPaper is null)
        {
            figure.AiResult = "请先选择论文。";
            return;
        }
        if (string.IsNullOrWhiteSpace(figure.SourceId))
        {
            figure.AiResult = "没有可信的图片来源 ID，无法发送。";
            return;
        }
        if (!figure.HasPreview)
        {
            figure.AiResult = "请先点击「本地预览」，确认区域正确后再提交付费请求。";
            return;
        }
        var paperId = SelectedPaper.Id;
        figure.AiResult = "正在分析已预览区域（可能产生 API 费用）…";
        try
        {
            var response = await _api.AnalyzeFigureAsync(paperId, figure.SourceId);
            if (SelectedPaper?.Id != paperId)
                return;
            var description = JsonRead.Text(response, "description");
            figure.AiResult = "AI 推断 · 未核验（请对照 PDF）："
                + Environment.NewLine + description;
        }
        catch (Exception exc)
        {
            figure.AiResult = "图片分析失败：" + exc.Message;
        }
    }

    private void ProjectVisuals(JsonElement card)
    {
        Formulas.Clear();
        foreach (var previous in Figures)
            previous.PreviewImage = null;
        Figures.Clear();

        if (card.TryGetProperty("formulas", out var formulas) && formulas.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in formulas.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.Object)
                    Formulas.Add(VisualSourceItem.FromFormula(item));
            }
        }
        if (card.TryGetProperty("figures", out var figures) && figures.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in figures.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.Object)
                    Figures.Add(VisualSourceItem.FromFigure(item));
            }
        }
        OnPropertyChanged(nameof(HasNoVisuals));
    }

    private void ProjectWarnings(JsonElement card)
    {
        var warnings = JsonRead.StringList(card, "warnings");
        HasWarnings = warnings.Count > 0;
        WarningsSummary = warnings.Count == 0
            ? "暂无提取警告"
            : $"{warnings.Count} 项信息需要核验 · 点击展开详情";
        // Preserve every raw warning; only the on-screen initial state is collapsed.
        WarningsText = warnings.Count == 0
            ? ""
            : string.Join(Environment.NewLine, warnings);
    }

    public async Task ImportPdfAsync(Stream stream, string fileName)
    {
        if (IsBusy)
        {
            return;
        }
        await RunJobAsync(
            $"正在导入 {fileName}…",
            async () => await _api.ImportPaperAsync(stream, fileName),
            preferredPaperId: null,
            successMessage: "PDF 导入完成",
            selectResultPaper: true
        );
    }

    public async Task ImportBatchAsync(IReadOnlyList<(Stream Stream, string FileName)> files, string label)
    {
        if (files.Count == 0 || IsBusy)
        {
            return;
        }
        await RunJobAsync(
            $"正在{label}…",
            async () => await _api.ImportPapersBatchAsync(files),
            preferredPaperId: null,
            successMessage: $"{label}完成",
            completionMessage: snapshot => DescribeBatchImport(snapshot, label)
        );
    }

    private static string DescribeBatchImport(JobSnapshot snapshot, string label)
    {
        // The Python batch worker reports per-file failures inside a terminal
        // job with status=done. Treating done as unconditional success hid
        // 0 imported / N failed scenarios from users of the packaged app.
        if (snapshot.Result is null || snapshot.Result.Value.ValueKind != JsonValueKind.Object)
            throw new InvalidOperationException("批量导入任务没有返回导入统计，请查看任务日志。");
        var payload = snapshot.Result.Value;

        int ReadCount(string field)
        {
            if (!payload.TryGetProperty(field, out var value) || value.ValueKind != JsonValueKind.Number ||
                !value.TryGetInt32(out var count) || count < 0)
                throw new InvalidOperationException($"批量导入任务缺少有效的 {field} 统计，请查看任务日志。");
            return count;
        }
        var imported = ReadCount("imported");
        var duplicates = ReadCount("duplicates");
        var failed = ReadCount("failed");
        var summary = $"{label}：新增 {imported} 篇，已存在 {duplicates} 篇，失败 {failed} 篇。";

        if (failed > 0 && payload.TryGetProperty("results", out var results) && results.ValueKind == JsonValueKind.Array)
        {
            var reasons = new List<string>();
            foreach (var item in results.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.Object ||
                    !item.TryGetProperty("error", out var error) || error.ValueKind != JsonValueKind.String)
                    continue;
                var reason = error.GetString();
                if (string.IsNullOrWhiteSpace(reason)) continue;
                var filename = item.TryGetProperty("filename", out var name) && name.ValueKind == JsonValueKind.String
                    ? name.GetString() : "某个 PDF";
                reasons.Add($"{filename}: {reason}");
                if (reasons.Count == 3) break;
            }
            if (reasons.Count > 0) summary += Environment.NewLine + string.Join(Environment.NewLine, reasons);
        }
        if (failed > 0 && imported == 0 && duplicates == 0)
            throw new InvalidOperationException(summary);
        return summary;
    }

    public async Task CancelActiveJobAsync()
    {
        var jobId = ActiveJobId;
        if (string.IsNullOrWhiteSpace(jobId) || IsCancellingJob)
        {
            return;
        }
        IsCancellingJob = true;
        StatusText = "正在请求取消后台任务，将在下一个安全检查点停止…";
        JobStage = "正在取消";
        try
        {
            await _api.CancelJobAsync(jobId);
        }
        catch (Exception exc)
        {
            StatusText = "取消任务请求失败：" + exc.Message;
            IsCancellingJob = false;
        }
    }

    private async Task RunJobAsync(
        string initialMessage,
        Func<Task<JobAccepted>> starter,
        string? preferredPaperId,
        string successMessage,
        bool selectResultPaper = false,
        Func<JobSnapshot, string>? completionMessage = null
    )
    {
        IsBusy = true;
        IsCancellingJob = false;
        ActiveJobId = null;
        StatusText = initialMessage;
        JobLog = initialMessage;
        JobStage = "准备中";
        JobProgress = 6;
        JobIndeterminate = true;
        try
        {
            var accepted = await starter();
            ActiveJobId = accepted.JobId;
            var completed = await TrackJobAsync(accepted.JobId);
            if (selectResultPaper)
            {
                preferredPaperId = completed.ResultString("paper_id") ?? preferredPaperId;
            }
            var actualMessage = completionMessage is null ? successMessage : completionMessage(completed);
            StatusText = actualMessage;
            JobStage = completionMessage is not null &&
                int.TryParse(completed.ResultString("failed"), out var batchFailures) && batchFailures > 0
                ? "部分完成" : "完成";
            JobProgress = 100;
            JobIndeterminate = false;
            JobLog += Environment.NewLine + Environment.NewLine + actualMessage;
            try
            {
                await RefreshAfterJobAsync(preferredPaperId);
            }
            catch (Exception refreshError)
            {
                // Backend confirmed DONE: a later refresh failure is not a
                // failed import. Never repeat PDF uploads automatically.
                JobStage = "处理完成 · 列表待刷新";
                StatusText = actualMessage
                    + "；论文处理已完成，但列表暂时无法刷新。"
                    + " 请在 API 恢复后手动刷新，勿重复导入。";
                JobLog += Environment.NewLine + Environment.NewLine
                    + "刷新时连接断开：" + refreshError.Message;
            }
        }
        catch (SraJobCancelledException exc)
        {
            StatusText = exc.Message;
            JobStage = "已取消";
            JobIndeterminate = false;
            JobLog += Environment.NewLine + Environment.NewLine + exc.Message;
            try
            {
                await RefreshAfterJobAsync(preferredPaperId);
            }
            catch (Exception)
            {
            }
        }
        catch (Exception exc) when (exc is HttpRequestException or TaskCanceledException)
        {
            // Uncertain POST/GET transport result. The backend may already
            // have saved the PDF even though the desktop lost its connection.
            StatusText = "本地后端连接中断；尚无法确认后台任务结果。"
                + " PDF 可能已保存，请恢复连接并刷新论文库，勿立即重复导入。"
                + " 详情：" + exc.Message;
            JobStage = "任务结果待核实";
            JobIndeterminate = false;
            JobLog += Environment.NewLine + Environment.NewLine + StatusText;
        }
        catch (Exception exc)
        {
            StatusText = exc.Message;
            JobStage = "发生错误";
            JobProgress = 100;
            JobIndeterminate = false;
            JobLog += Environment.NewLine + Environment.NewLine + exc.Message;
        }
        finally
        {
            ActiveJobId = null;
            IsCancellingJob = false;
            IsBusy = false;
        }
    }

    private async Task<JobSnapshot> TrackJobAsync(string jobId)
    {
        return await _api.WaitForJobAsync(
            jobId,
            snapshot =>
            {
                JobLog = snapshot.Messages.Count == 0 ? $"{snapshot.Kind}: {snapshot.Status}" : string.Join(Environment.NewLine, snapshot.Messages);
                var last = snapshot.Messages.LastOrDefault() ?? $"任务状态：{snapshot.Status}";
                StatusText = last;
                var visual = JobVisual(snapshot.Messages, snapshot.Status);
                JobStage = visual.Stage;
                JobProgress = visual.Progress;
                JobIndeterminate = visual.Indeterminate;
            }
        );
    }
}
