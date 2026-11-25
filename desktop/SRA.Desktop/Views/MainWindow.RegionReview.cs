using Avalonia.Interactivity;
using SRA.Desktop.Controls;

namespace SRA.Desktop.Views;

public partial class MainWindow
{
    /// <summary>
    /// 监听精读/证据卡片冒泡的「定位 PDF 原文」请求，直接打开对应页并高亮对应 SourceBlock。
    /// </summary>
    private void AttachEvidenceNavigation()
    {
        AddHandler(ClaimCard.EvidenceJumpRequestedEvent, OnEvidenceJumpRequested);
    }

    private async void RegionReview_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is not { } paper) return;
        var window = new RegionReviewWindow(ViewModel.Api, paper.Id, paper.Title);
        await window.ShowDialog(this);
    }

    private async void OnEvidenceJumpRequested(object? sender, EvidenceJumpEventArgs e)
    {
        if (ViewModel?.SelectedPaper is not { } paper) return;
        var page = int.TryParse(e.Evidence.Page, out var parsed) && parsed > 0 ? parsed : 1;
        var blockId = string.IsNullOrWhiteSpace(e.Evidence.SourceBlockId)
            ? null
            : e.Evidence.SourceBlockId.Trim();
        e.Handled = true;
        var window = new RegionReviewWindow(
            ViewModel.Api,
            paper.Id,
            paper.Title,
            initialPage: page,
            highlightSourceBlockId: blockId);
        await window.ShowDialog(this);
    }
}
