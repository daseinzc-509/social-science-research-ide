using SRA.Desktop.Models;
using CommunityToolkit.Mvvm.Input;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// 论文库检索、列表过滤与当前选中论文的异步加载逻辑。
/// </summary>
public partial class MainWindowViewModel
{
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

    private void UpdateLibrarySummary()
    {
        LibrarySummary = string.IsNullOrWhiteSpace(SearchText)
            ? (_allPapers.Count == 1 ? "1 paper" : $"{_allPapers.Count} papers")
            : $"{Papers.Count} / {_allPapers.Count} papers";
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
