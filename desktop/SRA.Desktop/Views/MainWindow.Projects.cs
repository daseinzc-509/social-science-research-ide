using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Input.Platform;
using Avalonia.Interactivity;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

/// <summary>
/// 研究专题目录树、文献列表原生右键上下文菜单、就地专题归属与 Evidence Matrix 视图交互处理。
/// </summary>
public partial class MainWindow
{
    private void FilterAll_Click(object? sender, RoutedEventArgs e)
        => ViewModel?.SelectLibraryFilter("all");

    private void FilterAnalyzed_Click(object? sender, RoutedEventArgs e)
        => ViewModel?.SelectLibraryFilter("analyzed");

    private void FilterPending_Click(object? sender, RoutedEventArgs e)
        => ViewModel?.SelectLibraryFilter("pending");

    private async void SelectProjectNode_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        await ViewModel.SelectProjectScopeAsync(proj, openMatrix: ViewModel.IsMatrixViewMode);
    }

    private async void OpenProjectMatrixDirect_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        await ViewModel.SelectProjectScopeAsync(proj, openMatrix: true);
    }

    private void SwitchToSingleView_Click(object? sender, RoutedEventArgs e)
        => ViewModel?.SetWorkspaceViewMode(matrixMode: false);

    private void SwitchToMatrixView_Click(object? sender, RoutedEventArgs e)
        => ViewModel?.SetWorkspaceViewMode(matrixMode: true);

    private void ExitProjectScope_Click(object? sender, RoutedEventArgs e)
        => ViewModel?.SelectLibraryFilter("all");

    private async void CreateProject_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        var editor = new ProjectEditorWindow();
        var result = await editor.ShowDialog<ProjectEditorResult?>(this);
        if (result is null) return;
        var created = await ViewModel.CreateProjectAsync(result);
        if (created is not null)
        {
            await ViewModel.SelectProjectScopeAsync(created, openMatrix: true);
        }
    }

    private async void EditActiveProject_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj) return;
        await EditProjectAsync(proj);
    }

    private async void DeleteActiveProject_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj) return;
        await ConfirmAndDeleteProjectAsync(proj);
    }

    private async void AddPapersToActiveProject_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj) return;
        await ShowAddPapersToProjectDialogAsync(proj);
    }

    private async void AnalyzeActiveProjectPending_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj || ViewModel.IsBusy) return;
        var pending = ViewModel.PendingPapersInActiveProjectCount;
        if (pending <= 0)
        {
            ViewModel.StatusText = $"专题「{proj.Name}」内所有文献均已完成分析。";
            return;
        }
        var confirm = new ConfirmWindow(
            "分析当前专题待处理论文",
            $"将为专题「{proj.Name}」中的 {pending} 篇未分析论文依次运行 Lite → Pro 分析（不会触碰其他专题的论文）。\n\n此操作将调用外部模型 API 并可能产生费用，过程中可随时取消。继续？",
            $"开始分析 ({pending} 篇)");
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.RunActiveProjectPendingAnalysisAsync();
    }

    private async void ExportActiveMatrixMarkdown_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj) return;
        string md;
        try
        {
            md = await ViewModel.Api.GetProjectEvidenceMatrixMarkdownAsync(proj.Id);
        }
        catch
        {
            md = ViewModel.BuildActiveMatrixMarkdown();
        }
        await SaveTextAsync(SafeName(proj.Name) + "-evidence-matrix.md", md);
    }

    private async void ExportActiveMatrixCsv_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj) return;
        try
        {
            var csv = await ViewModel.Api.GetProjectEvidenceMatrixCsvAsync(proj.Id);
            await SaveTextAsync(SafeName(proj.Name) + "-evidence-matrix.csv", csv);
        }
        catch (Exception exc)
        {
            ViewModel.StatusText = "导出专题 CSV 失败：" + exc.Message;
        }
    }

    private async void ExportActiveProjectRis_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj) return;
        try
        {
            var ris = await ViewModel.Api.GetProjectRisAsync(proj.Id);
            await SaveTextAsync(SafeName(proj.Name) + "-noteexpress.ris", ris);
        }
        catch (Exception exc)
        {
            ViewModel.StatusText = "导出专题 RIS（NoteExpress）失败：" + exc.Message;
        }
    }

    private void MatrixReadingFilter_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null && sender is Control { Tag: string filter })
        {
            ViewModel.MatrixReadingFilter = filter;
        }
    }

    private void MatrixReviewFilter_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null && sender is Control { Tag: string filter })
        {
            ViewModel.MatrixReviewFilter = filter;
        }
    }

    private void ResetMatrixFilters_Click(object? sender, RoutedEventArgs e)
        => ViewModel?.ResetMatrixFilters();

    // ─── 专题级研究笔记（MY NOTE）就地交互 ───

    private async void SaveDraftProjectNote_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        await ViewModel.CreateDraftProjectNoteAsync();
    }

    private async void DraftProjectNoteBody_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.KeyModifiers == KeyModifiers.Control && e.Key == Key.Enter)
        {
            e.Handled = true;
            if (ViewModel is not null)
            {
                await ViewModel.CreateDraftProjectNoteAsync();
            }
        }
    }

    private async void EditProjectNote_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || (sender as Control)?.Tag is not ProjectNoteItem note) return;
        var dialog = new NoteEditorWindow(note);
        var edited = await dialog.ShowDialog<NoteEditorResult?>(this);
        if (edited is null) return;
        await ViewModel.UpdateProjectNoteAsync(note.Id, edited);
    }

    private async void DeleteProjectNote_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || (sender as Control)?.Tag is not ProjectNoteItem note) return;
        var confirm = new ConfirmWindow(
            "删除专题研究笔记",
            $"确定删除专题笔记“{note.DisplayTitle}”？此操作不可撤销。",
            "删除笔记");
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.DeleteProjectNoteAsync(note);
    }

    private async void CopyProjectNote_Click(object? sender, RoutedEventArgs e)
    {
        if ((sender as Control)?.Tag is not ProjectNoteItem note) return;
        var clipboard = TopLevel.GetTopLevel(this)?.Clipboard;
        if (clipboard is null) return;
        await clipboard.SetTextAsync(note.Note);
        if (ViewModel is not null)
        {
            ViewModel.StatusText = "已复制专题笔记到剪贴板。";
        }
    }

    // ─── 左侧专题文件夹右键菜单事件 ───

    private async void ContextOpenProjectMatrix_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        await ViewModel.SelectProjectScopeAsync(proj, openMatrix: true);
    }

    private async void ContextAddPapersToProject_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        await ShowAddPapersToProjectDialogAsync(proj);
    }

    private async void ContextEditProject_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        await EditProjectAsync(proj);
    }

    private async void ContextExportProjectMatrix_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        await ViewModel.SelectProjectScopeAsync(proj, openMatrix: true);
        string md;
        try
        {
            md = await ViewModel.Api.GetProjectEvidenceMatrixMarkdownAsync(proj.Id);
        }
        catch
        {
            md = ViewModel.BuildActiveMatrixMarkdown();
        }
        await SaveTextAsync(SafeName(proj.Name) + "-evidence-matrix.md", md);
    }

    private async void ContextExportProjectMatrixCsv_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        try
        {
            var csv = await ViewModel.Api.GetProjectEvidenceMatrixCsvAsync(proj.Id);
            await SaveTextAsync(SafeName(proj.Name) + "-evidence-matrix.csv", csv);
        }
        catch (Exception exc)
        {
            ViewModel.StatusText = "导出专题 CSV 失败：" + exc.Message;
        }
    }

    private async void ContextExportProjectRis_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        try
        {
            var ris = await ViewModel.Api.GetProjectRisAsync(proj.Id);
            await SaveTextAsync(SafeName(proj.Name) + "-noteexpress.ris", ris);
        }
        catch (Exception exc)
        {
            ViewModel.StatusText = "导出专题 RIS（NoteExpress）失败：" + exc.Message;
        }
    }

    private async void ContextDeleteProject_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectSummaryView proj }) return;
        await ConfirmAndDeleteProjectAsync(proj);
    }

    // ─── 论文列表原生右键上下文菜单事件（无需先左键选中） ───

    private PaperSummary? SelectTargetPaper(object? sender)
    {
        if (ViewModel is null) return null;
        if (sender is Control { Tag: PaperSummary paper })
        {
            if (ViewModel.SelectedPaper?.Id != paper.Id)
            {
                ViewModel.SelectedPaper = paper;
            }
            return paper;
        }
        return ViewModel.SelectedPaper;
    }

    private void ContextOpenPaperPdf_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        OpenPdf_Click(sender, e);
    }

    private void ContextAnalyzePaper_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        Analyze_Click(sender, e);
    }

    private void ContextRegionReviewPaper_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        RegionReview_Click(sender, e);
    }

    private async void ContextAssignPaperProjects_Click(object? sender, RoutedEventArgs e)
    {
        var paper = SelectTargetPaper(sender);
        if (paper is null) return;
        await ShowAssignProjectsForPaperDialogAsync(paper);
    }

    private async void AssignSelectedPaperProjects_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is not { } paper) return;
        await ShowAssignProjectsForPaperDialogAsync(paper);
    }

    private async void ContextEditPaperMetadata_Click(object? sender, RoutedEventArgs e)
    {
        var paper = SelectTargetPaper(sender);
        if (ViewModel is null || paper is null) return;
        // 右键未选中项时先确保详情已加载
        if (ViewModel.SelectedDetail is null || ViewModel.SelectedPaper?.Id != paper.Id)
        {
            await ViewModel.ReloadSelectedAsync();
        }
        EditMetadata_Click(sender, e);
    }

    private void ContextExtractPaperReferences_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        ExtractReferences_Click(sender, e);
    }

    private void ContextExportReading_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        ExportReading_Click(sender, e);
    }

    private void ContextExportCard_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        ExportCard_Click(sender, e);
    }

    private void ContextExportText_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        ExportText_Click(sender, e);
    }

    private void ContextExportBibtex_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        ExportBibtex_Click(sender, e);
    }

    private void ContextExportRis_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        ExportRis_Click(sender, e);
    }

    private void ContextReparsePaper_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        Reparse_Click(sender, e);
    }

    private void ContextDeletePaper_Click(object? sender, RoutedEventArgs e)
    {
        if (SelectTargetPaper(sender) is null) return;
        DeletePaper_Click(sender, e);
    }

    // ─── Evidence Matrix 矩阵行内交互事件 ───

    private void MatrixOpenPaper_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: ProjectMatrixRowItem row }) return;
        ViewModel.FocusPaperInSingleView(row.PaperId);
    }

    private async void MatrixEditRowStatus_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj || sender is not Control { Tag: ProjectMatrixRowItem row }) return;
        var dialog = new AssignProjectWindow(
            title: "调整专题阅读进度与备注",
            subtitle: $"文献：{row.Title}",
            choices: [(proj.Id, proj.Name, proj.ResearchQuestion, true)],
            initialReadingStatus: row.ReadingStatus,
            initialNote: row.ProjectNote,
            showCreateProjectButton: false);
        var result = await dialog.ShowDialog<PaperProjectAssignmentResult?>(this);
        if (result is null) return;
        if (result.SelectedIds.Contains(proj.Id))
        {
            await ViewModel.UpsertProjectPaperAsync(proj.Id, row.PaperId, result.ReadingStatus, result.ProjectNote);
        }
        else
        {
            await ViewModel.RemovePaperFromProjectAsync(proj.Id, row.PaperId);
        }
    }

    private async void MatrixRemoveRow_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.ActiveProject is not { } proj || sender is not Control { Tag: ProjectMatrixRowItem row }) return;
        var confirm = new ConfirmWindow(
            "从专题移出文献",
            $"将“{row.Title}”从专题「{proj.Name}」中移出？\n\n仅解除该文献与当前专题的关联，不会删除文献库中的 PDF、Paper Card 或笔记。",
            "移出专题");
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.RemovePaperFromProjectAsync(proj.Id, row.PaperId);
    }

    private async void MatrixCitationJump_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || sender is not Control { Tag: MatrixCitationItem citation }) return;
        var page = citation.PageNumber > 0 ? citation.PageNumber : 1;
        var blockId = string.IsNullOrWhiteSpace(citation.SourceBlockId) ? null : citation.SourceBlockId.Trim();
        var window = new RegionReviewWindow(
            ViewModel.Api,
            citation.PaperId,
            citation.PaperTitle,
            initialPage: page,
            highlightSourceBlockId: blockId);
        await window.ShowDialog(this);
    }

    // ─── 共用弹窗辅助逻辑 ───

    private async Task EditProjectAsync(ProjectSummaryView proj)
    {
        if (ViewModel is null) return;
        var editor = new ProjectEditorWindow(proj);
        var result = await editor.ShowDialog<ProjectEditorResult?>(this);
        if (result is null) return;
        await ViewModel.UpdateProjectAsync(proj.Id, result);
    }

    private async Task ConfirmAndDeleteProjectAsync(ProjectSummaryView proj)
    {
        if (ViewModel is null) return;
        var confirm = new ConfirmWindow(
            "删除研究专题",
            $"确定删除研究专题「{proj.Name}」？\n\n此操作仅解除该专题与文献的归属关联，绝不会删除文献库中的任何 PDF、Paper Card、人工修订或笔记。",
            "删除专题");
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.DeleteProjectAsync(proj);
    }

    private async Task ShowAssignProjectsForPaperDialogAsync(PaperSummary paper)
    {
        if (ViewModel is null) return;
        while (true)
        {
            var currentIds = ViewModel.GetProjectsContainingPaper(paper.Id)
                .Select(p => p.Id)
                .ToHashSet(StringComparer.Ordinal);
            var choices = ViewModel.Projects
                .Select(p => (
                    p.Id,
                    p.Name,
                    string.IsNullOrWhiteSpace(p.ResearchQuestion)
                        ? $"共 {p.PaperCount} 篇文献"
                        : $"{p.PaperCount} 篇 · {p.ResearchQuestion}",
                    currentIds.Contains(p.Id)))
                .ToList();

            var (initStatus, initNote) = ViewModel.GetActiveProjectEntry(paper.Id);
            var dialog = new AssignProjectWindow(
                title: "归入研究专题",
                subtitle: $"勾选「{paper.Title}」应归属的专题（取消勾选即移出该专题，不删原论文）。",
                choices: choices,
                initialReadingStatus: initStatus,
                initialNote: initNote,
                showCreateProjectButton: true);

            var result = await dialog.ShowDialog<PaperProjectAssignmentResult?>(this);
            if (result is null) return;

            if (result.RequestCreateNewProject)
            {
                var editor = new ProjectEditorWindow();
                var createdInput = await editor.ShowDialog<ProjectEditorResult?>(this);
                if (createdInput is not null)
                {
                    var created = await ViewModel.CreateProjectAsync(createdInput);
                    if (created is not null)
                    {
                        await ViewModel.UpsertProjectPaperAsync(created.Id, paper.Id, result.ReadingStatus, result.ProjectNote);
                    }
                }
                continue;
            }

            var targetIds = new HashSet<string>(result.SelectedIds, StringComparer.Ordinal);
            foreach (var proj in ViewModel.Projects)
            {
                var wasIn = currentIds.Contains(proj.Id);
                var nowIn = targetIds.Contains(proj.Id);
                if (nowIn)
                {
                    await ViewModel.UpsertProjectPaperAsync(proj.Id, paper.Id, result.ReadingStatus, result.ProjectNote);
                }
                else if (wasIn)
                {
                    await ViewModel.RemovePaperFromProjectAsync(proj.Id, paper.Id);
                }
            }
            return;
        }
    }

    private async Task ShowAddPapersToProjectDialogAsync(ProjectSummaryView proj)
    {
        if (ViewModel is null) return;
        var existing = ViewModel.GetProjectPaperIds(proj.Id);
        var choices = ViewModel.AllLibraryPapers
            .Where(p => !existing.Contains(p.Id))
            .Select(p => (
                p.Id,
                p.Title,
                $"{p.AuthorLine} · {p.StatusLine}",
                false))
            .ToList();

        var dialog = new AssignProjectWindow(
            title: $"向「{proj.Name}」添加文献",
            subtitle: choices.Count == 0
                ? "当前文献库中的全部论文都已加入该专题。"
                : "勾选要纳入此研究专题的文献，保存后自动更新专题证据矩阵（零模型调用）。",
            choices: choices,
            initialReadingStatus: "queued",
            initialNote: "",
            showCreateProjectButton: false);

        var result = await dialog.ShowDialog<PaperProjectAssignmentResult?>(this);
        if (result is null || result.SelectedIds.Count == 0) return;

        foreach (var paperId in result.SelectedIds)
        {
            await ViewModel.UpsertProjectPaperAsync(proj.Id, paperId, result.ReadingStatus, result.ProjectNote);
        }
        await ViewModel.SelectProjectScopeAsync(proj, openMatrix: ViewModel.IsMatrixViewMode);
    }
}
