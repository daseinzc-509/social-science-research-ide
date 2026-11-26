using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Input.Platform;
using Avalonia.Interactivity;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 跨论文 / 专题级 SQLite FTS5 全文检索面板交互与直达跳转逻辑。
/// </summary>
public partial class MainWindow
{
    private async void OpenFullTextSearch_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        NavigateTo("papers");
        await ViewModel.OpenFullTextSearchPanelAsync();
        FullTextSearchInput.Focus();
        FullTextSearchInput.SelectAll();
    }

    private void CloseFullTextSearch_Click(object? sender, RoutedEventArgs e)
        => ViewModel?.CloseFullTextSearchPanel();

    private async void LibrarySearchBox_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key != Key.Enter || ViewModel is null) return;
        if (string.IsNullOrWhiteSpace(ViewModel.SearchText)) return;
        e.Handled = true;
        await ViewModel.OpenFullTextSearchPanelAsync(seedQuery: ViewModel.SearchText);
        FullTextSearchInput.Focus();
    }

    private async void FullTextSearchInput_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && ViewModel is not null)
        {
            e.Handled = true;
            await ViewModel.ExecuteFullTextSearchAsync();
        }
        else if (e.Key == Key.Escape && ViewModel is not null)
        {
            e.Handled = true;
            ViewModel.CloseFullTextSearchPanel();
        }
    }

    private async void RunFullTextSearch_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        await ViewModel.ExecuteFullTextSearchAsync();
    }

    private async void ClearFullTextSearch_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        ViewModel.FullTextQuery = "";
        await ViewModel.ExecuteFullTextSearchAsync();
        FullTextSearchInput.Focus();
    }

    private async void SearchScopeProject_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        await ViewModel.SetSearchScopeProjectOnlyAsync(true);
    }

    private async void SearchScopeAll_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        await ViewModel.SetSearchScopeProjectOnlyAsync(false);
    }

    private async void SearchSourceFilter_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: string filter }) return;
        await ViewModel.SetSearchSourceFilterAsync(filter);
    }

    private async void SearchHitPrimaryJump_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || (sender as Control)?.Tag is not SearchHitItem hit) return;

        if (hit.IsProjectNote && !string.IsNullOrWhiteSpace(hit.TargetProjectId))
        {
            var targetProj = ViewModel.Projects.FirstOrDefault(p => p.Id == hit.TargetProjectId);
            if (targetProj is not null)
            {
                await ViewModel.SelectProjectScopeAsync(targetProj, openMatrix: true);
                return;
            }
        }

        if (hit.IsUserNote || hit.IsClaimReviewHit)
        {
            ViewModel.FocusPaperInSingleView(hit.PaperId);
            PaperTabs.SelectedIndex = 2; // 直达「我的笔记」与人工审定总览
            return;
        }

        if (hit.IsSourceBlock || (hit.IsHumanReviewed && hit.HasPageNumber))
        {
            ViewModel.FocusPaperInSingleView(hit.PaperId);
            var window = new RegionReviewWindow(
                ViewModel.Api,
                hit.PaperId,
                hit.PaperTitle,
                initialPage: hit.PageNumber ?? 1,
                highlightSourceBlockId: hit.SourceBlockId);
            await window.ShowDialog(this);
            return;
        }

        if (hit.IsMetadata)
        {
            ViewModel.FocusPaperInSingleView(hit.PaperId);
            PaperTabs.SelectedIndex = 1; // 直达「概览」书目页
            return;
        }

        ViewModel.FocusPaperInSingleView(hit.PaperId);
        PaperTabs.SelectedIndex = 0; // 直达「精读」页
    }

    private async void SearchHitJumpPdf_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || (sender as Control)?.Tag is not SearchHitItem hit) return;
        ViewModel.FocusPaperInSingleView(hit.PaperId);
        var window = new RegionReviewWindow(
            ViewModel.Api,
            hit.PaperId,
            hit.PaperTitle,
            initialPage: hit.PageNumber ?? 1,
            highlightSourceBlockId: hit.SourceBlockId);
        await window.ShowDialog(this);
    }

    private void SearchHitOpenPaper_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || (sender as Control)?.Tag is not SearchHitItem hit) return;
        ViewModel.FocusPaperInSingleView(hit.PaperId);
        PaperTabs.SelectedIndex = 0;
    }

    private void SearchHitOpenNotes_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || (sender as Control)?.Tag is not SearchHitItem hit) return;
        ViewModel.FocusPaperInSingleView(hit.PaperId);
        PaperTabs.SelectedIndex = 2;
    }

    private async void SearchHitCopySnippet_Click(object? sender, RoutedEventArgs e)
    {
        if ((sender as Control)?.Tag is not SearchHitItem hit) return;
        var clipboard = TopLevel.GetTopLevel(this)?.Clipboard;
        if (clipboard is null) return;
        var cite = hit.HasPageNumber ? $"（{hit.PaperTitle}，PDF p.{hit.PageNumber}）" : $"（{hit.PaperTitle}）";
        await clipboard.SetTextAsync($"{hit.Snippet}\n{cite}");
        if (ViewModel is not null)
        {
            ViewModel.StatusText = "已复制检索命中片段与来源页码到剪贴板。";
        }
    }
}
