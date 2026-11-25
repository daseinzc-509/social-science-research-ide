using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Input.Platform;
using Avalonia.Interactivity;
using SRA.Desktop.Controls;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 单篇论文「我的笔记（MY NOTE）」Tab 与 Claim 单条人工审定交互事件处理。
/// </summary>
public partial class MainWindow
{
    private void AttachClaimReviewNavigation()
    {
        AddHandler(ClaimCard.ClaimReviewRequestedEvent, OnClaimReviewRequested);
    }

    private async void OnClaimReviewRequested(object? sender, ClaimReviewRequestedEventArgs e)
    {
        e.Handled = true;
        if (ViewModel is null || ViewModel.SelectedPaper is null) return;

        var action = (e.Action ?? "").Trim().ToLowerInvariant();
        if (action == "clear")
        {
            await ViewModel.DeleteClaimReviewAsync(e.Claim.ClaimId);
            return;
        }

        if (action is "corrected" or "edit")
        {
            var initialStatus = action == "corrected"
                ? "corrected"
                : (string.IsNullOrWhiteSpace(e.Claim.HumanReviewStatus) ? "confirmed" : e.Claim.HumanReviewStatus);
            var dialog = new ClaimReviewEditorWindow(e.Claim, initialStatus);
            var result = await dialog.ShowDialog<ClaimReviewEditorResult?>(this);
            if (result is null) return;
            if (result.ClearReview)
            {
                await ViewModel.DeleteClaimReviewAsync(e.Claim.ClaimId);
                return;
            }
            await ViewModel.SaveClaimReviewAsync(
                e.Claim,
                result.UserStatus,
                correctedStatement: result.CorrectedStatement,
                note: result.Note);
            return;
        }

        if (action is "confirmed" or "rejected" or "unresolved")
        {
            // 再次点击当前已激活状态则一键取消审定，减少误点成本
            if (string.Equals(e.Claim.HumanReviewStatus, action, StringComparison.OrdinalIgnoreCase))
            {
                await ViewModel.DeleteClaimReviewAsync(e.Claim.ClaimId);
                return;
            }
            await ViewModel.SaveClaimReviewAsync(
                e.Claim,
                action,
                correctedStatement: "",
                note: e.Claim.HumanReviewNote);
        }
    }

    private async void SaveDraftNote_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        await ViewModel.CreateDraftNoteAsync();
    }

    private async void DraftNoteBody_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.KeyModifiers == KeyModifiers.Control && e.Key == Key.Enter)
        {
            e.Handled = true;
            if (ViewModel is not null)
            {
                await ViewModel.CreateDraftNoteAsync();
            }
        }
    }

    private async void EditNote_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        if ((sender as Control)?.Tag is not UserNoteItem note) return;
        var dialog = new NoteEditorWindow(note);
        var edited = await dialog.ShowDialog<NoteEditorResult?>(this);
        if (edited is null) return;
        await ViewModel.UpdateNoteAsync(note.Id, edited);
    }

    private async void DeleteNote_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        if ((sender as Control)?.Tag is not UserNoteItem note) return;
        var confirm = new ConfirmWindow(
            "删除个人笔记",
            $"确定删除笔记“{note.DisplayTitle}”？\n\n删除后将同时从全文检索索引中移除，此操作不可撤销。",
            "删除笔记"
        );
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.DeleteNoteAsync(note);
    }

    private async void NoteJumpPage_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is not { } paper) return;
        if ((sender as Control)?.Tag is not UserNoteItem note || note.PageNumber is not > 0) return;
        var window = new RegionReviewWindow(
            ViewModel.Api,
            paper.Id,
            paper.Title,
            initialPage: note.PageNumber.Value);
        await window.ShowDialog(this);
    }

    private async void CopyNote_Click(object? sender, RoutedEventArgs e)
    {
        if ((sender as Control)?.Tag is not UserNoteItem note) return;
        var clipboard = TopLevel.GetTopLevel(this)?.Clipboard;
        if (clipboard is null) return;
        await clipboard.SetTextAsync(note.Note);
        if (ViewModel is not null)
        {
            ViewModel.StatusText = "已复制笔记内容到剪贴板。";
        }
    }

    private async void DeleteReviewRecord_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        if ((sender as Control)?.Tag is not ClaimReviewItem item) return;
        await ViewModel.DeleteClaimReviewAsync(item.ClaimId);
    }

    private void OpenNotesTab_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        ViewModel.SetWorkspaceViewMode(false);
        PaperTabs.SelectedIndex = 2;
    }
}
