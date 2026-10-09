using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SRA.Desktop;
using SRA.Desktop.Models;
using SRA.Desktop.Services;

namespace SRA.Desktop.ViewModels;

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
    public bool CanRunPaperActions => HasSelection && !IsBusy;
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

    partial void OnStudyRationaleChanged(string value) => OnPropertyChanged(nameof(HasStudyRationale));

    partial void OnSearchTextChanged(string value)
    {
        ApplyFilter(value);
    }

    partial void OnThemeModeChanged(string value)
    {
        App.ApplyThemeMode(value);
        SaveDesktopPreferences();
    }

    partial void OnShowInspectorChanged(bool value)
    {
        SaveDesktopPreferences();
    }

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

    [RelayCommand]
    public async Task RefreshAsync()
    {
        if (IsBusy)
        {
            return;
        }

        var selectedId = SelectedPaper?.Id;
        try
        {
            StatusText = "正在刷新论文库…";
            var papers = await _api.GetPapersAsync();
            _allPapers.Clear();
            _allPapers.AddRange(papers);
            ApplyFilter(SearchText, selectedId);
            ApiStatusText = "API 已连接 · v1";
            StatusText = _allPapers.Count == 0 ? "论文库为空" : $"论文库已刷新：{_allPapers.Count} 篇";
        }
        catch (Exception exc)
        {
            ApiStatusText = "API 未连接";
            StatusText = exc.Message;
        }
    }

    public async Task ReloadSelectedAsync()
    {
        if (SelectedPaper is null)
        {
            return;
        }
        await LoadSelectedPaperAsync(SelectedPaper, CancellationToken.None);
        await ReloadLibrarySummaryAsync(SelectedPaper.Id);
    }

    private async Task ReloadLibrarySummaryAsync(string preferredId)
    {
        var papers = await _api.GetPapersAsync();
        _allPapers.Clear();
        _allPapers.AddRange(papers);
        ApplyFilter(SearchText, preferredId);
    }

    private void ApplyFilter(string query, string? preferredId = null)
    {
        preferredId ??= SelectedPaper?.Id;
        var term = (query ?? "").Trim();
        IEnumerable<PaperSummary> filtered = _allPapers;
        if (!string.IsNullOrWhiteSpace(term))
        {
            filtered = _allPapers.Where(paper =>
                paper.Title.Contains(term, StringComparison.OrdinalIgnoreCase) ||
                paper.OriginalName.Contains(term, StringComparison.OrdinalIgnoreCase) ||
                paper.Authors.Any(author => author.Contains(term, StringComparison.OrdinalIgnoreCase)) ||
                (paper.Journal?.Contains(term, StringComparison.OrdinalIgnoreCase) ?? false) ||
                (paper.Year?.ToString().Contains(term, StringComparison.OrdinalIgnoreCase) ?? false));
        }

        Papers.Clear();
        foreach (var paper in filtered)
        {
            Papers.Add(paper);
        }
        UpdateLibrarySummary();

        SelectedPaper = !string.IsNullOrWhiteSpace(preferredId)
            ? Papers.FirstOrDefault(p => p.Id == preferredId) ?? Papers.FirstOrDefault()
            : Papers.FirstOrDefault();
    }

    private async Task LoadSelectedPaperAsync(PaperSummary? paper, CancellationToken cancellationToken)
    {
        ClearProjection();
        if (paper is null)
        {
            SelectedDetail = null;
            SelectedTitle = "选择一篇论文";
            SelectedSubtitle = "从左侧论文库开始你的阅读。";
            SelectedParseStatus = "尚未选择文档";
            SelectedAnalysisState = "未选择";
            RawCardJson = "尚未选择论文。";
            return;
        }

        SelectedTitle = paper.Title;
        SelectedSubtitle = BuildSubtitle(paper);
        SelectedFactsCount = paper.Facts.ToString();
        SelectedEvidenceCount = paper.EvidenceSpans.ToString();
        SelectedReviewsCount = paper.AnalysisClaims.ToString();
        SelectedParseStatus = $"{paper.ParseStatus} · {paper.PageCount} PDF pages";
        SelectedAnalysisState = paper.HasCard ? "已生成 Paper Card" : "等待分析";
        MetadataTitle = paper.Title;
        MetadataAuthors = paper.AuthorLine;
        MetadataJournal = paper.Journal ?? "—";
        MetadataYear = paper.Year?.ToString() ?? "—";
        RawCardJson = paper.HasCard ? "正在读取 Paper Card…" : "尚无 Paper Card。";

        try
        {
            var detail = await _api.GetPaperAsync(paper.Id, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            SelectedDetail = detail;
            ProjectDetail(detail);
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception exc)
        {
            SelectedStudyType = "读取失败";
            SelectedAnalysisState = "需要检查";
            RawCardJson = exc.ToString();
            StatusText = exc.Message;
        }
    }

    private void ProjectDetail(PaperDetailResponse detail)
    {
        References.Clear();
        foreach (var reference in detail.References)
        {
            if (reference.ValueKind == JsonValueKind.Object)
            {
                References.Add(ReferenceItem.FromJson(reference));
            }
        }
        ReferenceStatusText = References.Count == 0
            ? "尚未提取参考文献。"
            : $"已保存 {References.Count} 条参考文献，发现 {detail.CitationMentions.Count} 个正文引用位置。";

        if (detail.MetadataOverrides.ValueKind == JsonValueKind.Object)
        {
            var valuesCount = 0;
            if (detail.MetadataOverrides.TryGetProperty("values", out var values) && values.ValueKind == JsonValueKind.Object)
            {
                valuesCount = values.EnumerateObject().Count();
            }
            HasManualMetadata = valuesCount > 0;
            ManualMetadataNote = JsonRead.Text(detail.MetadataOverrides, "source_note");
        }

        if (detail.Card is not { ValueKind: JsonValueKind.Object } card)
        {
            SelectedStudyType = "未分析";
            SelectedAnalysisState = "等待分析";
            AbstractText = "这篇 PDF 已导入并解析，但还没有 Paper Card。";
            RawCardJson = "尚无 Paper Card。";
            return;
        }

        RawCardJson = JsonSerializer.Serialize(card, new JsonSerializerOptions { WriteIndented = true });
        SelectedAnalysisState = "可审计分析已完成";
        ProjectMetadata(card);
        ProjectStudyProfile(card);
        ProjectClaims(card);
        ProjectTables(card);
        ProjectWarnings(card);
    }

    private void ProjectMetadata(JsonElement card)
    {
        if (!card.TryGetProperty("metadata", out var metadata) || metadata.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        MetadataTitle = JsonRead.Text(metadata, "title", SelectedTitle);
        var authors = JsonRead.StringList(metadata, "authors");
        MetadataAuthors = authors.Count == 0 ? "—" : string.Join("、", authors);
        MetadataJournal = JsonRead.Text(metadata, "journal", "—");
        MetadataYear = JsonRead.Text(metadata, "year", "—");
        var volume = JsonRead.Text(metadata, "volume");
        var issue = JsonRead.Text(metadata, "issue");
        MetadataVolumeIssue = string.IsNullOrWhiteSpace(volume) && string.IsNullOrWhiteSpace(issue)
            ? "—"
            : $"{(string.IsNullOrWhiteSpace(volume) ? "" : $"Vol. {volume}")}{(!string.IsNullOrWhiteSpace(volume) && !string.IsNullOrWhiteSpace(issue) ? " · " : "")}{(string.IsNullOrWhiteSpace(issue) ? "" : $"No. {issue}")}";
        MetadataPages = JsonRead.Text(metadata, "pages", "—");
        MetadataDoi = JsonRead.Text(metadata, "doi", "—");
        AbstractText = JsonRead.Text(metadata, "abstract", "未提取摘要。");
        Keywords.Clear();
        foreach (var keyword in JsonRead.StringList(metadata, "keywords"))
        {
            Keywords.Add(keyword);
        }
    }

    private void ProjectStudyProfile(JsonElement card)
    {
        if (!card.TryGetProperty("study_profile", out var profile) || profile.ValueKind != JsonValueKind.Object)
        {
            SelectedStudyType = "unknown";
            return;
        }
        SelectedStudyType = JsonRead.Text(profile, "study_type", "unknown");
        SelectedStudyMethod = JsonRead.Text(profile, "method", "—");
        SelectedIdentification = JsonRead.Text(profile, "identification_strategy", "—");
        StudyRationale = JsonRead.Text(profile, "rationale");
        if (card.TryGetProperty("review_plan", out var reviewPlan) && reviewPlan.ValueKind == JsonValueKind.Array)
        {
            ReviewPlanText = string.Join(" · ", reviewPlan.EnumerateArray().Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : item.ToString()).Where(item => !string.IsNullOrWhiteSpace(item)));
        }
    }

    private void ProjectClaims(JsonElement card)
    {
        var metadataClaims = ParseClaims(card, "metadata_claims");
        var basicFacts = ParseClaims(card, "basic_facts");
        var analysis = ParseClaims(card, "analysis");
        var limitations = ParseClaims(card, "limitations");
        var recommendation = ParseSingleClaim(card, "reading_recommendation");

        Replace(MetadataClaims, metadataClaims);
        Replace(BasicFacts, basicFacts);
        Replace(ResearchQuestions, basicFacts.Where(item => item.FieldName == "research_question"));
        Replace(TheoryConcepts, basicFacts.Where(item => item.FieldName == "theory_concept"));
        Replace(ResearchDesignClaims, basicFacts.Where(item => item.FieldName is "research_method" or "research_sample" or "measurement_indicator"));

        var auditById = new Dictionary<string, JsonElement>();
        if (card.TryGetProperty("claim_audits", out var audits) && audits.ValueKind == JsonValueKind.Array)
        {
            foreach (var audit in audits.EnumerateArray())
            {
                var claimId = JsonRead.Text(audit, "claim_id");
                if (!string.IsNullOrWhiteSpace(claimId))
                {
                    auditById[claimId] = audit;
                }
            }
        }

        var findings = new List<ClaimItem>();
        if (card.TryGetProperty("basic_facts", out var rawFacts) && rawFacts.ValueKind == JsonValueKind.Array)
        {
            foreach (var raw in rawFacts.EnumerateArray())
            {
                if (JsonRead.Text(raw, "field_name") != "major_finding")
                {
                    continue;
                }
                var baseItem = ClaimItem.FromJson(raw);
                var claimId = JsonRead.Text(raw, "claim_id");
                if (!string.IsNullOrWhiteSpace(claimId) && auditById.TryGetValue(claimId, out var audit))
                {
                    findings.Add(MergeAudit(baseItem, audit));
                }
                else
                {
                    findings.Add(baseItem);
                }
            }
        }

        Replace(MajorFindings, findings);
        Replace(AuthorExplanations, basicFacts.Where(item => item.FieldName == "author_explanation"));
        Replace(AuthorLimitations, basicFacts.Where(item => item.FieldName == "research_limitations"));
        Replace(AnalysisClaims, analysis);
        Replace(AiLimitations, limitations);
        Replace(ReadingRecommendations, recommendation is null ? [] : [recommendation]);
    }

    private static ClaimItem MergeAudit(ClaimItem source, JsonElement audit)
    {
        var evidence = source.Evidence;
        if (audit.TryGetProperty("evidence", out var evidenceNode) && evidenceNode.ValueKind == JsonValueKind.Array)
        {
            var auditEvidence = evidenceNode.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object).Select(EvidenceItem.FromJson).ToList();
            if (auditEvidence.Count > 0)
            {
                evidence = auditEvidence;
            }
        }
        return new ClaimItem
        {
            FieldName = source.FieldName,
            FieldLabel = source.FieldLabel,
            Statement = source.Statement,
            Provenance = source.Provenance,
            Verification = source.Verification,
            ClaimType = source.ClaimType,
            Scope = source.Scope,
            SemanticSupport = JsonRead.Text(audit, "semantic_support", source.SemanticSupport),
            Rationale = JsonRead.Text(audit, "rationale", source.Rationale),
            Evidence = evidence,
        };
    }

    private void ProjectTables(JsonElement card)
    {
        Tables.Clear();
        if (!card.TryGetProperty("tables", out var tables) || tables.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var table in tables.EnumerateArray())
        {
            if (table.ValueKind == JsonValueKind.Object)
            {
                Tables.Add(TableItem.FromJson(table));
            }
        }
    }

    private void ProjectWarnings(JsonElement card)
    {
        var warnings = JsonRead.StringList(card, "warnings");
        HasWarnings = warnings.Count > 0;
        WarningsText = warnings.Count == 0 ? "Paper Card 当前为 0 warnings。" : string.Join(Environment.NewLine, warnings);
    }

    private static List<ClaimItem> ParseClaims(JsonElement card, string propertyName)
    {
        if (!card.TryGetProperty(propertyName, out var node) || node.ValueKind != JsonValueKind.Array)
        {
            return [];
        }
        return node.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object).Select(ClaimItem.FromJson).ToList();
    }

    private static ClaimItem? ParseSingleClaim(JsonElement card, string propertyName)
    {
        if (!card.TryGetProperty(propertyName, out var node) || node.ValueKind != JsonValueKind.Object)
        {
            return null;
        }
        return ClaimItem.FromJson(node);
    }

    public async Task LoadSettingsAsync()
    {
        await Settings.LoadAsync();
    }

    public async Task LoadDashboardAsync()
    {
        try
        {
            var data = await _api.GetDashboardAsync();
            DashboardPapers = JsonValue(data, "papers");
            DashboardAnalyzed = JsonValue(data, "analyzed");
            DashboardPending = JsonValue(data, "pending_analysis");
            DashboardReview = JsonValue(data, "needs_review");
            DashboardReferences = JsonValue(data, "references");
            DashboardOutput = "批量分析会逐篇执行 Lite → Pro；单篇失败不会中断整个队列。";
        }
        catch (Exception exc)
        {
            DashboardOutput = exc.Message;
        }
    }

    public async Task RunBatchAnalysisAsync()
    {
        if (IsBusy) return;
        IsBusy = true;
        DashboardOutput = "正在启动批量分析…";
        try
        {
            var accepted = await _api.StartBatchAnalysisAsync();
            await _api.WaitForJobAsync(accepted.JobId, snapshot =>
            {
                DashboardOutput = snapshot.Messages.Count == 0
                    ? $"{snapshot.Kind}: {snapshot.Status}"
                    : string.Join(Environment.NewLine, snapshot.Messages);
            });
            var papers = await _api.GetPapersAsync();
            _allPapers.Clear();
            _allPapers.AddRange(papers);
            ApplyFilter(SearchText, SelectedPaper?.Id);
            await LoadDashboardAsync();
        }
        catch (Exception exc)
        {
            DashboardOutput += Environment.NewLine + Environment.NewLine + exc.Message;
        }
        finally
        {
            IsBusy = false;
        }
    }

    public async Task RunBatchReferencesAsync()
    {
        if (IsBusy) return;
        IsBusy = true;
        DashboardOutput = "正在批量提取参考文献…";
        try
        {
            var result = await _api.ExtractReferencesBatchAsync();
            DashboardOutput = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
            var papers = await _api.GetPapersAsync();
            _allPapers.Clear();
            _allPapers.AddRange(papers);
            ApplyFilter(SearchText, SelectedPaper?.Id);
            await LoadDashboardAsync();
        }
        catch (Exception exc)
        {
            DashboardOutput = exc.Message;
        }
        finally
        {
            IsBusy = false;
        }
    }

    public async Task RunDoctorAsync(bool deep)
    {
        try
        {
            MaintenanceOutput = deep ? "正在执行深度检查…" : "正在检查数据库…";
            var result = await _api.GetDoctorAsync(deep);
            MaintenanceOutput = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            MaintenanceOutput = exc.Message;
        }
    }

    public async Task PreviewPruneCacheAsync()
    {
        try
        {
            var result = await _api.PruneCacheAsync(false, false);
            MaintenanceOutput = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            MaintenanceOutput = exc.Message;
        }
    }

    public async Task ApplyPruneCacheAsync()
    {
        try
        {
            var result = await _api.PruneCacheAsync(true, true);
            MaintenanceOutput = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            MaintenanceOutput = exc.Message;
        }
    }

    private static string JsonValue(JsonElement element, string name)
    {
        return element.TryGetProperty(name, out var value) ? value.ToString() : "—";
    }

    public async Task AnalyzeAsync(AnalysisRequest request)
    {
        if (SelectedPaper is null || IsBusy)
        {
            return;
        }
        var paperId = SelectedPaper.Id;
        ResearchContext = request.ResearchContext ?? "";
        ExcludeAfterText = request.ExcludeAfterText ?? "";
        await RunJobAsync(
            "正在启动论文分析…",
            async () => await _api.StartAnalysisAsync(paperId, request),
            paperId,
            "论文分析完成"
        );
    }

    public async Task ReparseAsync()
    {
        if (SelectedPaper is null || IsBusy)
        {
            return;
        }
        var paperId = SelectedPaper.Id;
        await RunJobAsync(
            "正在重建 PDF 解析…",
            async () => await _api.StartReparseAsync(paperId),
            paperId,
            "PDF 解析已重建；旧 Card 与模型缓存已清理"
        );
    }

    public async Task ExtractReferencesAsync()
    {
        if (SelectedPaper is null || IsBusy)
        {
            return;
        }
        IsBusy = true;
        try
        {
            StatusText = "正在提取参考文献…";
            await _api.ExtractReferencesAsync(SelectedPaper.Id);
            await ReloadSelectedAsync();
            StatusText = "参考文献提取完成";
        }
        catch (Exception exc)
        {
            StatusText = $"参考文献提取失败：{exc.Message}";
        }
        finally
        {
            IsBusy = false;
        }
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
            successMessage: $"{label}完成"
        );
    }

    public async Task DeleteSelectedAsync()
    {
        if (SelectedPaper is null || IsBusy)
        {
            return;
        }
        IsBusy = true;
        var deletingId = SelectedPaper.Id;
        try
        {
            var result = await _api.DeletePaperAsync(deletingId, true);
            StatusText = string.IsNullOrWhiteSpace(result.FileWarning) ? "论文已删除" : result.FileWarning;
            await RefreshAsync();
        }
        catch (Exception exc)
        {
            StatusText = $"删除失败：{exc.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task RunJobAsync(
        string initialMessage,
        Func<Task<JobAccepted>> starter,
        string? preferredPaperId,
        string successMessage,
        bool selectResultPaper = false
    )
    {
        IsBusy = true;
        StatusText = initialMessage;
        JobLog = initialMessage;
        JobStage = "准备中";
        JobProgress = 6;
        JobIndeterminate = true;
        try
        {
            var accepted = await starter();
            var completed = await TrackJobAsync(accepted.JobId);
            if (selectResultPaper)
            {
                preferredPaperId = completed.ResultString("paper_id") ?? preferredPaperId;
            }
            StatusText = successMessage;
            JobStage = "完成";
            JobProgress = 100;
            JobIndeterminate = false;
            await RefreshAfterJobAsync(preferredPaperId);
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

    private async Task RefreshAfterJobAsync(string? preferredPaperId)
    {
        var papers = await _api.GetPapersAsync();
        _allPapers.Clear();
        _allPapers.AddRange(papers);
        ApplyFilter(SearchText, preferredPaperId ?? SelectedPaper?.Id);
        if (SelectedPaper is not null)
        {
            await LoadSelectedPaperAsync(SelectedPaper, CancellationToken.None);
        }
    }

    private static (string Stage, double Progress, bool Indeterminate) JobVisual(IReadOnlyList<string> messages, string status)
    {
        var last = messages.LastOrDefault() ?? "";
        var all = string.Join("\n", messages);
        if (status == "done") return ("完成", 100, false);
        if (status == "error") return ("发生错误", 100, false);
        if (all.Contains("Saving Paper Card", StringComparison.OrdinalIgnoreCase) || all.Contains("Paper Card saved", StringComparison.OrdinalIgnoreCase)) return ("保存结果", 96, false);
        if (last.Contains("Pro", StringComparison.OrdinalIgnoreCase) || all.Contains("Pro independent review", StringComparison.OrdinalIgnoreCase)) return ("Pro 深度审读", 82, last.Contains("sending request", StringComparison.OrdinalIgnoreCase));
        if (last.Contains("Metadata layout recovery", StringComparison.OrdinalIgnoreCase) || last.Contains("Preparing evidence ledger", StringComparison.OrdinalIgnoreCase)) return ("整理证据与元数据", 52, false);
        if (last.Contains("Lite request", StringComparison.OrdinalIgnoreCase) || all.Contains("Lite request", StringComparison.OrdinalIgnoreCase)) return ("Lite 事实提取", 62, last.Contains("sending request", StringComparison.OrdinalIgnoreCase));
        if (last.Contains("Docling:", StringComparison.OrdinalIgnoreCase)) return ("Docling 文档解析", 28, true);
        if (last.Contains("Rebuild:", StringComparison.OrdinalIgnoreCase)) return ("重建 PDF 解析", 18, true);
        if (last.Contains("Import:", StringComparison.OrdinalIgnoreCase) || last.Contains("Importing", StringComparison.OrdinalIgnoreCase)) return ("导入 PDF", 14, true);
        return ("准备中", 6, true);
    }

    private void ClearProjection()
    {
        MetadataClaims.Clear();
        BasicFacts.Clear();
        ResearchQuestions.Clear();
        TheoryConcepts.Clear();
        ResearchDesignClaims.Clear();
        MajorFindings.Clear();
        AuthorExplanations.Clear();
        AuthorLimitations.Clear();
        AnalysisClaims.Clear();
        AiLimitations.Clear();
        ReadingRecommendations.Clear();
        References.Clear();
        Tables.Clear();
        Keywords.Clear();
        SelectedStudyType = "—";
        SelectedStudyMethod = "—";
        SelectedIdentification = "—";
        StudyRationale = "";
        MetadataTitle = "—";
        MetadataAuthors = "—";
        MetadataJournal = "—";
        MetadataYear = "—";
        MetadataVolumeIssue = "—";
        MetadataPages = "—";
        MetadataDoi = "—";
        AbstractText = "尚未提取摘要。";
        WarningsText = "";
        HasWarnings = false;
        HasManualMetadata = false;
        ManualMetadataNote = "";
        ReviewPlanText = "—";
        ReferenceStatusText = "尚未提取参考文献。";
    }

    private void UpdateLibrarySummary()
    {
        LibrarySummary = string.IsNullOrWhiteSpace(SearchText)
            ? (_allPapers.Count == 1 ? "1 paper" : $"{_allPapers.Count} papers")
            : $"{Papers.Count} / {_allPapers.Count} papers";
        IsLibraryEmpty = Papers.Count == 0;
    }

    private static void Replace<T>(ObservableCollection<T> target, IEnumerable<T> values)
    {
        target.Clear();
        foreach (var value in values)
        {
            target.Add(value);
        }
    }

    private static string BuildSubtitle(PaperSummary paper)
    {
        var parts = new List<string>();
        if (paper.Authors.Count > 0) parts.Add(string.Join("、", paper.Authors));
        if (paper.Year is not null) parts.Add(paper.Year.Value.ToString());
        if (!string.IsNullOrWhiteSpace(paper.Journal)) parts.Add(paper.Journal!);
        return parts.Count == 0 ? paper.OriginalName : string.Join(" · ", parts);
    }
}
