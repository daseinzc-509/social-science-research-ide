using SRA.Desktop.Models;
using CommunityToolkit.Mvvm.Input;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// 论文库检索、列表过滤与当前选中论文的异步加载逻辑。
/// </summary>
public partial class MainWindowViewModel
{
    private CancellationTokenSource? _searchCts;

    [RelayCommand]
    public void ClearSearch() => SearchText = "";
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
            RaiseScopeCountLabels();
            await RefreshProjectsAsync();
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
        RaiseScopeCountLabels();
        await RefreshProjectsAsync();
        ApplyFilter(SearchText, preferredId);
    }

    private IEnumerable<PaperSummary> FilterByCurrentScope(IEnumerable<PaperSummary> source)
    {
        if (IsProjectScopeActive && ActiveProject is { } proj)
        {
            var memberIds = GetProjectPaperIds(proj.Id);
            return source.Where(p => memberIds.Contains(p.Id));
        }
        return LibraryFilterMode switch
        {
            "analyzed" => source.Where(p => p.HasCard),
            "pending" => source.Where(p => !p.HasCard),
            _ => source,
        };
    }

    private void ApplyFilter(string query, string? preferredId = null)
    {
        preferredId ??= SelectedPaper?.Id;
        var term = (query ?? "").Trim();
        var scoped = FilterByCurrentScope(_allPapers);
        IEnumerable<PaperSummary> filtered = scoped;
        if (!string.IsNullOrWhiteSpace(term))
        {
            filtered = scoped.Where(paper =>
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

    private async Task RunFullTextSearchFilterAsync(string query)
    {
        _searchCts?.Cancel();
        _searchCts?.Dispose();
        var term = (query ?? "").Trim();
        if (string.IsNullOrWhiteSpace(term) || _allPapers.Count == 0)
        {
            return;
        }

        var cts = new CancellationTokenSource();
        _searchCts = cts;
        try
        {
            await Task.Delay(180, cts.Token);
            var projectId = IsProjectScopeActive ? ActiveProject?.Id : null;
            var response = await _api.SearchLibraryAsync(term, projectId: projectId, limit: 120, cancellationToken: cts.Token);
            cts.Token.ThrowIfCancellationRequested();
            if (!string.Equals((SearchText ?? "").Trim(), term, StringComparison.Ordinal))
            {
                return;
            }

            var orderedIds = new List<string>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var hit in response.Hits)
            {
                if (seen.Add(hit.PaperId))
                {
                    orderedIds.Add(hit.PaperId);
                }
            }
            if (orderedIds.Count == 0)
            {
                return;
            }

            var scopedById = FilterByCurrentScope(_allPapers).ToDictionary(p => p.Id, StringComparer.Ordinal);
            var merged = new List<PaperSummary>();
            foreach (var id in orderedIds)
            {
                if (scopedById.TryGetValue(id, out var matchedPaper))
                {
                    merged.Add(matchedPaper);
                }
            }
            foreach (var existing in Papers)
            {
                if (seen.Add(existing.Id))
                {
                    merged.Add(existing);
                }
            }

            var currentSelectedId = SelectedPaper?.Id;
            Papers.Clear();
            foreach (var item in merged)
            {
                Papers.Add(item);
            }
            UpdateLibrarySummary(ftsMatched: true);
            SelectedPaper = !string.IsNullOrWhiteSpace(currentSelectedId)
                ? Papers.FirstOrDefault(p => p.Id == currentSelectedId) ?? Papers.FirstOrDefault()
                : Papers.FirstOrDefault();
        }
        catch (OperationCanceledException)
        {
        }
        catch
        {
            // 本地离线或后端忙碌时保留同步内存过滤结果，不打断用户输入
        }
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

    private async Task RefreshAfterJobAsync(string? preferredPaperId)
    {
        var papers = await _api.GetPapersAsync();
        _allPapers.Clear();
        _allPapers.AddRange(papers);
        RaiseScopeCountLabels();
        await RefreshProjectsAsync();
        ApplyFilter(SearchText, preferredPaperId ?? SelectedPaper?.Id);
        if (SelectedPaper is not null)
        {
            await LoadSelectedPaperAsync(SelectedPaper, CancellationToken.None);
        }
    }

    private void UpdateLibrarySummary(bool ftsMatched = false)
    {
        var scopeTotal = FilterByCurrentScope(_allPapers).Count();
        LibrarySummary = string.IsNullOrWhiteSpace(SearchText)
            ? (scopeTotal == 1 ? "1 篇" : $"{scopeTotal} 篇")
            : (ftsMatched
                ? $"{Papers.Count} / {scopeTotal} 篇 · 全文"
                : $"{Papers.Count} / {scopeTotal} 篇");
        IsLibraryEmpty = Papers.Count == 0;
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
