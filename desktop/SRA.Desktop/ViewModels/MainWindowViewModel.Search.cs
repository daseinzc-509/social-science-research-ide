using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using SRA.Desktop.Models;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// 跨论文 / 专题级 SQLite FTS5 全文检索面板视图模型逻辑。
/// </summary>
public partial class MainWindowViewModel
{
    private CancellationTokenSource? _panelSearchCts;

    public ObservableCollection<SearchHitItem> FullTextHits { get; } = [];

    [ObservableProperty] private bool isSearchPanelOpen;
    [ObservableProperty] private string fullTextQuery = "";
    [ObservableProperty] private string searchSourceFilter = "ALL";
    [ObservableProperty] private bool searchWithinActiveProjectOnly = true;
    [ObservableProperty] private bool isSearchingFullText;
    [ObservableProperty] private int fullTextTotalHits;
    [ObservableProperty] private int quickSearchHitCount;

    public bool ShowSearchPanelView => IsSearchPanelOpen;
    public bool HasFullTextHits => FullTextHits.Count > 0;
    public bool HasNoFullTextHits => !IsSearchingFullText && FullTextHits.Count == 0 && !string.IsNullOrWhiteSpace(FullTextQuery);
    public bool IsFullTextPromptVisible => !IsSearchingFullText && FullTextHits.Count == 0 && string.IsNullOrWhiteSpace(FullTextQuery);
    public bool ShowQuickSearchBanner => HasSearchText && QuickSearchHitCount > 0 && !IsSearchPanelOpen;
    public string QuickSearchBannerText => $"全文命中 {QuickSearchHitCount} 条可溯源片段 · 查看检索结果 →";

    public bool IsSourceFilterAll => SearchSourceFilter == "ALL";
    public bool IsSourceFilterSourceBlock => SearchSourceFilter == "SOURCE_BLOCK";
    public bool IsSourceFilterPaperCard => SearchSourceFilter == "PAPER_CARD";
    public bool IsSourceFilterUserNote => SearchSourceFilter == "USER_NOTE";
    public bool IsSourceFilterProjectNote => SearchSourceFilter == "PROJECT_NOTE";
    public bool IsSourceFilterHumanReviewed => SearchSourceFilter == "HUMAN_REVIEWED";
    public bool IsSourceFilterMetadata => SearchSourceFilter == "METADATA";

    public bool CanToggleProjectSearchScope => IsProjectScopeActive;
    public bool IsSearchingProjectScope => IsProjectScopeActive && SearchWithinActiveProjectOnly;
    public bool IsSearchingAllLibrary => !IsSearchingProjectScope;

    public string ActiveSearchScopeLabel => IsSearchingProjectScope && ActiveProject is not null
        ? $"当前专题：{ActiveProject.Name}"
        : "全部文献库";

    public string FullTextStatusLine
    {
        get
        {
            if (IsSearchingFullText) return $"正在检索「{ActiveSearchScopeLabel}」…";
            if (string.IsNullOrWhiteSpace(FullTextQuery))
                return $"将在「{ActiveSearchScopeLabel}」内执行本地 SQLite FTS5 中英双语检索（原文 / Paper Card / 我的笔记 / 人工审定），零模型调用。";
            return $"在「{ActiveSearchScopeLabel}」中命中 {FullTextTotalHits} 条可溯源片段 · 点击条目直跳论文、PDF 页码或笔记。";
        }
    }

    partial void OnIsSearchPanelOpenChanged(bool value)
    {
        RaiseSearchPanelState();
        RaiseProjectScopeState();
    }

    partial void OnFullTextQueryChanged(string value)
    {
        RaiseSearchPanelState();
    }

    partial void OnSearchSourceFilterChanged(string value)
    {
        RaiseSearchPanelState();
    }

    partial void OnSearchWithinActiveProjectOnlyChanged(bool value)
    {
        RaiseSearchPanelState();
    }

    partial void OnIsSearchingFullTextChanged(bool value)
    {
        RaiseSearchPanelState();
    }

    partial void OnFullTextTotalHitsChanged(int value)
    {
        RaiseSearchPanelState();
    }

    partial void OnQuickSearchHitCountChanged(int value)
    {
        OnPropertyChanged(nameof(ShowQuickSearchBanner));
        OnPropertyChanged(nameof(QuickSearchBannerText));
    }

    private void RaiseSearchPanelState()
    {
        OnPropertyChanged(nameof(ShowSearchPanelView));
        OnPropertyChanged(nameof(HasFullTextHits));
        OnPropertyChanged(nameof(HasNoFullTextHits));
        OnPropertyChanged(nameof(IsFullTextPromptVisible));
        OnPropertyChanged(nameof(ShowQuickSearchBanner));
        OnPropertyChanged(nameof(QuickSearchBannerText));
        OnPropertyChanged(nameof(IsSourceFilterAll));
        OnPropertyChanged(nameof(IsSourceFilterSourceBlock));
        OnPropertyChanged(nameof(IsSourceFilterPaperCard));
        OnPropertyChanged(nameof(IsSourceFilterUserNote));
        OnPropertyChanged(nameof(IsSourceFilterProjectNote));
        OnPropertyChanged(nameof(IsSourceFilterHumanReviewed));
        OnPropertyChanged(nameof(IsSourceFilterMetadata));
        OnPropertyChanged(nameof(CanToggleProjectSearchScope));
        OnPropertyChanged(nameof(IsSearchingProjectScope));
        OnPropertyChanged(nameof(IsSearchingAllLibrary));
        OnPropertyChanged(nameof(ActiveSearchScopeLabel));
        OnPropertyChanged(nameof(FullTextStatusLine));
    }

    public async Task OpenFullTextSearchPanelAsync(string? seedQuery = null, bool preferProjectScope = true)
    {
        if (seedQuery is not null)
        {
            FullTextQuery = seedQuery.Trim();
        }
        else if (string.IsNullOrWhiteSpace(FullTextQuery) && !string.IsNullOrWhiteSpace(SearchText))
        {
            FullTextQuery = SearchText.Trim();
        }
        SearchWithinActiveProjectOnly = preferProjectScope && IsProjectScopeActive;
        IsSearchPanelOpen = true;
        if (!string.IsNullOrWhiteSpace(FullTextQuery))
        {
            await ExecuteFullTextSearchAsync();
        }
    }

    public void CloseFullTextSearchPanel()
    {
        IsSearchPanelOpen = false;
    }

    public async Task SetSearchSourceFilterAsync(string filter)
    {
        SearchSourceFilter = string.IsNullOrWhiteSpace(filter) ? "ALL" : filter.Trim().ToUpperInvariant();
        if (!string.IsNullOrWhiteSpace(FullTextQuery))
        {
            await ExecuteFullTextSearchAsync();
        }
    }

    public async Task SetSearchScopeProjectOnlyAsync(bool projectOnly)
    {
        SearchWithinActiveProjectOnly = projectOnly && IsProjectScopeActive;
        if (!string.IsNullOrWhiteSpace(FullTextQuery))
        {
            await ExecuteFullTextSearchAsync();
        }
    }

    public async Task ExecuteFullTextSearchAsync()
    {
        _panelSearchCts?.Cancel();
        _panelSearchCts?.Dispose();
        var term = (FullTextQuery ?? "").Trim();
        if (string.IsNullOrWhiteSpace(term))
        {
            FullTextHits.Clear();
            FullTextTotalHits = 0;
            IsSearchingFullText = false;
            RaiseSearchPanelState();
            return;
        }

        var cts = new CancellationTokenSource();
        _panelSearchCts = cts;
        IsSearchingFullText = true;
        try
        {
            var projectId = IsSearchingProjectScope ? ActiveProject?.Id : null;
            IEnumerable<string>? kinds = SearchSourceFilter switch
            {
                "ALL" => null,
                "USER_NOTE" => ["USER_NOTE", "PROJECT_NOTE"],
                _ => [SearchSourceFilter],
            };
            var response = await _api.SearchLibraryAsync(
                term,
                projectId: projectId,
                sourceKinds: kinds,
                limit: 120,
                cancellationToken: cts.Token);
            cts.Token.ThrowIfCancellationRequested();

            FullTextHits.Clear();
            foreach (var hit in response.Hits)
            {
                FullTextHits.Add(SearchHitItem.FromView(hit, highlightQuery: term));
            }
            FullTextTotalHits = response.Total;
            QuickSearchHitCount = response.Total;
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception exc)
        {
            StatusText = "全文检索失败：" + exc.Message;
        }
        finally
        {
            IsSearchingFullText = false;
            RaiseSearchPanelState();
        }
    }
}
