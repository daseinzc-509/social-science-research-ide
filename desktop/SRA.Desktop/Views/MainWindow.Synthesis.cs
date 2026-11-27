using Avalonia.Controls;
using Avalonia.Input.Platform;
using Avalonia.Interactivity;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 专题级多文献综合（共识 / 冲突 / 方法论差异 / 研究缺口）工作区交互与溯源跳页处理。
/// </summary>
public partial class MainWindow
{
    private async void SwitchToSynthesisView_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        await ViewModel.OpenProjectSynthesisViewAsync();
    }

    private async void ContextOpenProjectSynthesis_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        await ViewModel.SelectProjectScopeAsync(proj, openMatrix: false);
        await ViewModel.OpenProjectSynthesisViewAsync();
    }

    private async void PreviewAndRunSynthesis_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || ViewModel.IsRunningSynthesis) return;
        var preview = await ViewModel.PreviewActiveProjectSynthesisAsync();
        if (preview is null) return;

        var dialog = new SynthesisPreviewWindow(preview);
        var consent = await dialog.ShowDialog<SynthesisConsentResult?>(this);
        if (consent is null || !consent.ApproveExternalSend || !consent.AcknowledgeApiCost) return;

        await ViewModel.RunApprovedProjectSynthesisAsync(preview, consent);
    }

    private async void CopyActiveSynthesisMarkdown_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj || ViewModel.ActiveSynthesisDetail is not { } detail) return;
        var clipboard = TopLevel.GetTopLevel(this)?.Clipboard;
        if (clipboard is null) return;
        string md;
        try
        {
            md = await ViewModel.Api.GetProjectSynthesisMarkdownAsync(proj.Id, detail.Id);
        }
        catch
        {
            md = detail.Markdown;
        }
        await clipboard.SetTextAsync(md);
        ViewModel.StatusText = "已复制当前多文献综合 Markdown 到剪贴板。";
    }

    private async void ExportActiveSynthesisMarkdown_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj || ViewModel.ActiveSynthesisDetail is not { } detail) return;
        string md;
        try
        {
            md = await ViewModel.Api.GetProjectSynthesisMarkdownAsync(proj.Id, detail.Id);
        }
        catch
        {
            md = detail.Markdown;
        }
        await SaveTextAsync($"{SafeName(proj.Name)}-synthesis-{detail.Id}.md", md);
    }

    private async void DeleteActiveSynthesis_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveSynthesisDetail is null) return;
        var confirm = new ConfirmWindow(
            "删除历史综合快照",
            "确定删除当前这条多文献综合历史快照？\n\n仅删除此 AI 综合快照，绝不影响原论文、Evidence Matrix、个人笔记或任何人工审定记录。",
            "删除快照");
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.DeleteActiveSynthesisAsync();
    }

    private async void SynthesisCitationJump_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || (sender as Control)?.Tag is not SynthesisClaimCitationItem cite) return;
        if (cite.PageNumber is int page && page > 0)
        {
            var blockId = string.IsNullOrWhiteSpace(cite.SourceBlockId) ? null : cite.SourceBlockId.Trim();
            var window = new RegionReviewWindow(
                ViewModel.Api,
                cite.PaperId,
                cite.PaperTitle,
                initialPage: page,
                highlightSourceBlockId: blockId);
            await window.ShowDialog(this);
            return;
        }
        ViewModel.FocusPaperInSingleView(cite.PaperId);
        PaperTabs.SelectedIndex = 0;
    }

    private void SynthesisCitationOpenPaper_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || (sender as Control)?.Tag is not SynthesisClaimCitationItem cite) return;
        ViewModel.FocusPaperInSingleView(cite.PaperId);
        PaperTabs.SelectedIndex = 0;
    }
}
