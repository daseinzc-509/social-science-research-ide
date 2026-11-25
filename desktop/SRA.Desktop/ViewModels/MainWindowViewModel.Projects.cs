using System.Collections.ObjectModel;
using System.Text;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using SRA.Desktop.Models;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// 研究专题（Projects）目录树联动、文献归属管理与零 LLM 确定性 Evidence Matrix 投影逻辑。
/// </summary>
public partial class MainWindowViewModel
{
    private readonly Dictionary<string, HashSet<string>> _projectPaperIds = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (string Status, string Note)> _activeProjectEntries = new(StringComparer.Ordinal);
    private CancellationTokenSource? _matrixCts;

    public ObservableCollection<ProjectSummaryView> Projects { get; } = [];
    public ObservableCollection<ProjectSummaryView> SelectedPaperProjects { get; } = [];
    public ObservableCollection<ProjectMatrixRowItem> MatrixRows { get; } = [];
    public ObservableCollection<ProjectNoteItem> ProjectNotes { get; } = [];

    /// <summary>
    /// 左侧目录树筛选模式：all / analyzed / pending / project。
    /// </summary>
    [ObservableProperty] private string libraryFilterMode = "all";
    [ObservableProperty] private ProjectSummaryView? activeProject;
    [ObservableProperty] private bool isMatrixViewMode;
    [ObservableProperty] private bool isLoadingMatrix;
    [ObservableProperty] private int matrixTotalPapers;
    [ObservableProperty] private int matrixAnalyzedPapers;
    [ObservableProperty] private string draftProjectNoteTitle = "";
    [ObservableProperty] private string draftProjectNoteBody = "";

    public bool IsFilterAll => LibraryFilterMode == "all";
    public bool IsFilterAnalyzed => LibraryFilterMode == "analyzed";
    public bool IsFilterPending => LibraryFilterMode == "pending";
    public bool IsProjectScopeActive => ActiveProject is not null && LibraryFilterMode == "project";
    public bool ShowSinglePaperView => !IsSearchPanelOpen && (!IsProjectScopeActive || (!IsMatrixViewMode && !IsSynthesisViewMode));
    public bool ShowProjectMatrixView => !IsSearchPanelOpen && IsProjectScopeActive && IsMatrixViewMode && !IsSynthesisViewMode;

    public bool HasProjects => Projects.Count > 0;
    public bool HasNoProjects => Projects.Count == 0;
    public bool HasSelectedPaperProjects => SelectedPaperProjects.Count > 0;
    public bool HasNoSelectedPaperProjects => SelectedPaperProjects.Count == 0;
    public bool HasMatrixRows => MatrixRows.Count > 0;
    public bool IsMatrixEmpty => MatrixRows.Count == 0 && !IsLoadingMatrix;
    public bool HasProjectNotes => ProjectNotes.Count > 0;
    public bool HasNoProjectNotes => ProjectNotes.Count == 0;
    public string ProjectNotesCount => ProjectNotes.Count.ToString();

    public string AllPapersCountLabel => _allPapers.Count.ToString();
    public string AnalyzedPapersCountLabel => _allPapers.Count(p => p.HasCard).ToString();
    public string PendingPapersCountLabel => _allPapers.Count(p => !p.HasCard).ToString();

    public string ActiveScopeHeading => IsProjectScopeActive && ActiveProject is not null
        ? $"专题 · {ActiveProject.Name}"
        : LibraryFilterMode switch
        {
            "analyzed" => "已分析论文",
            "pending" => "待分析论文",
            _ => "全部论文",
        };

    public string ActiveProjectName => ActiveProject?.Name ?? "";
    public string ActiveProjectQuestion => ActiveProject?.ResearchQuestion ?? "";
    public string ActiveProjectDescription => ActiveProject?.Description ?? "";
    public bool HasActiveProjectQuestion => !string.IsNullOrWhiteSpace(ActiveProjectQuestion);
    public bool HasActiveProjectDescription => !string.IsNullOrWhiteSpace(ActiveProjectDescription);
    public string MatrixStatsText => $"共 {MatrixTotalPapers} 篇文献 · 已分析 {MatrixAnalyzedPapers} 篇 · 零模型调用（确定性聚合）";

    public IReadOnlyList<PaperSummary> AllLibraryPapers => _allPapers;

    partial void OnLibraryFilterModeChanged(string value)
    {
        RaiseProjectScopeState();
    }

    partial void OnActiveProjectChanged(ProjectSummaryView? value)
    {
        if (value is null)
        {
            IsMatrixViewMode = false;
            IsSynthesisViewMode = false;
            MatrixRows.Clear();
            ProjectNotes.Clear();
            ClearSynthesisState();
            MatrixTotalPapers = 0;
            MatrixAnalyzedPapers = 0;
            _activeProjectEntries.Clear();
            RaiseProjectNotesState();
        }
        RaiseProjectScopeState();
        RaiseSearchPanelState();
        RaiseSynthesisState();
    }

    partial void OnIsMatrixViewModeChanged(bool value)
    {
        RaiseProjectScopeState();
    }

    partial void OnIsLoadingMatrixChanged(bool value)
    {
        OnPropertyChanged(nameof(IsMatrixEmpty));
    }

    private void RaiseProjectScopeState()
    {
        OnPropertyChanged(nameof(IsFilterAll));
        OnPropertyChanged(nameof(IsFilterAnalyzed));
        OnPropertyChanged(nameof(IsFilterPending));
        OnPropertyChanged(nameof(IsProjectScopeActive));
        OnPropertyChanged(nameof(ShowSinglePaperView));
        OnPropertyChanged(nameof(ShowProjectMatrixView));
        OnPropertyChanged(nameof(ShowProjectSynthesisView));
        OnPropertyChanged(nameof(ActiveScopeHeading));
        OnPropertyChanged(nameof(ActiveProjectName));
        OnPropertyChanged(nameof(ActiveProjectQuestion));
        OnPropertyChanged(nameof(ActiveProjectDescription));
        OnPropertyChanged(nameof(HasActiveProjectQuestion));
        OnPropertyChanged(nameof(HasActiveProjectDescription));
        OnPropertyChanged(nameof(MatrixStatsText));
        OnPropertyChanged(nameof(HasMatrixRows));
        OnPropertyChanged(nameof(IsMatrixEmpty));
    }

    private void RaiseScopeCountLabels()
    {
        OnPropertyChanged(nameof(AllPapersCountLabel));
        OnPropertyChanged(nameof(AnalyzedPapersCountLabel));
        OnPropertyChanged(nameof(PendingPapersCountLabel));
    }

    public async Task RefreshProjectsAsync()
    {
        try
        {
            var list = await _api.GetProjectsAsync();
            var previousId = ActiveProject?.Id;
            Projects.Clear();
            foreach (var item in list)
            {
                Projects.Add(item);
            }
            OnPropertyChanged(nameof(HasProjects));
            OnPropertyChanged(nameof(HasNoProjects));

            // 同步缓存每个专题下的论文 ID，支持左侧筛选与右侧「所属专题」芯片即时展示
            _projectPaperIds.Clear();
            foreach (var proj in list)
            {
                try
                {
                    var detail = await _api.GetProjectDetailAsync(proj.Id);
                    var ids = new HashSet<string>(StringComparer.Ordinal);
                    if (detail.TryGetProperty("papers", out var arr) && arr.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var entry in arr.EnumerateArray())
                        {
                            var pid = JsonRead.Text(entry, "paper_id");
                            if (!string.IsNullOrWhiteSpace(pid)) ids.Add(pid);
                        }
                    }
                    _projectPaperIds[proj.Id] = ids;
                }
                catch
                {
                    _projectPaperIds[proj.Id] = [];
                }
            }

            if (!string.IsNullOrWhiteSpace(previousId))
            {
                var updated = Projects.FirstOrDefault(p => p.Id == previousId);
                if (updated is not null)
                {
                    ActiveProject = updated;
                    await LoadActiveProjectMatrixAsync(updated.Id);
                }
                else if (LibraryFilterMode == "project")
                {
                    SelectLibraryFilter("all");
                }
            }

            UpdateSelectedPaperProjects();
            RaiseProjectScopeState();
        }
        catch
        {
            // 启动阶段若后端暂未就绪，不阻断主文献库加载
        }
    }

    public void SelectLibraryFilter(string mode)
    {
        ActiveProject = null;
        IsMatrixViewMode = false;
        IsSynthesisViewMode = false;
        IsSearchPanelOpen = false;
        LibraryFilterMode = mode is "analyzed" or "pending" ? mode : "all";
        ApplyFilter(SearchText, SelectedPaper?.Id);
    }

    public async Task SelectProjectScopeAsync(ProjectSummaryView project, bool openMatrix = false)
    {
        ActiveProject = project;
        LibraryFilterMode = "project";
        IsMatrixViewMode = openMatrix;
        IsSynthesisViewMode = false;
        IsSearchPanelOpen = false;
        ApplyFilter(SearchText, SelectedPaper?.Id);
        await LoadActiveProjectMatrixAsync(project.Id);
        await LoadProjectSynthesesAsync(project.Id);
        // 矩阵返回最新成员后再次校准列表，防止其他客户端变更漏同步
        ApplyFilter(SearchText, SelectedPaper?.Id);
    }

    public void SetWorkspaceViewMode(bool matrixMode)
    {
        IsSearchPanelOpen = false;
        IsSynthesisViewMode = false;
        if (!IsProjectScopeActive)
        {
            IsMatrixViewMode = false;
            return;
        }
        IsMatrixViewMode = matrixMode;
    }

    public async Task LoadActiveProjectMatrixAsync(string projectId)
    {
        _matrixCts?.Cancel();
        _matrixCts?.Dispose();
        var cts = new CancellationTokenSource();
        _matrixCts = cts;
        IsLoadingMatrix = true;
        try
        {
            var data = await _api.GetProjectEvidenceMatrixAsync(projectId, cts.Token);
            cts.Token.ThrowIfCancellationRequested();
            if (ActiveProject?.Id != projectId) return;

            var rows = new List<ProjectMatrixRowItem>();
            var ids = new HashSet<string>(StringComparer.Ordinal);
            _activeProjectEntries.Clear();

            if (data.TryGetProperty("rows", out var rowsNode) && rowsNode.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in rowsNode.EnumerateArray())
                {
                    if (item.ValueKind != JsonValueKind.Object) continue;
                    var row = ProjectMatrixRowItem.FromJson(item);
                    rows.Add(row);
                    if (!string.IsNullOrWhiteSpace(row.PaperId))
                    {
                        ids.Add(row.PaperId);
                        _activeProjectEntries[row.PaperId] = (row.ReadingStatus, row.ProjectNote);
                    }
                }
            }

            _projectPaperIds[projectId] = ids;
            MatrixRows.Clear();
            foreach (var row in rows)
            {
                MatrixRows.Add(row);
            }
            MatrixTotalPapers = data.TryGetProperty("total_papers", out var tp) && tp.TryGetInt32(out var total)
                ? total : rows.Count;
            MatrixAnalyzedPapers = data.TryGetProperty("analyzed_papers", out var ap) && ap.TryGetInt32(out var analyzed)
                ? analyzed : rows.Count(r => r.HasCard);

            UpdateSelectedPaperProjects();
            await LoadActiveProjectNotesAsync(projectId, cts.Token);
            RaiseProjectScopeState();
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception exc)
        {
            StatusText = "加载专题证据矩阵失败：" + exc.Message;
        }
        finally
        {
            IsLoadingMatrix = false;
            OnPropertyChanged(nameof(HasMatrixRows));
            OnPropertyChanged(nameof(IsMatrixEmpty));
        }
    }

    public HashSet<string> GetProjectPaperIds(string projectId) =>
        _projectPaperIds.TryGetValue(projectId, out var set)
            ? new HashSet<string>(set, StringComparer.Ordinal)
            : new HashSet<string>(StringComparer.Ordinal);

    public (string Status, string Note) GetActiveProjectEntry(string paperId) =>
        _activeProjectEntries.TryGetValue(paperId, out var entry) ? entry : ("queued", "");

    public IReadOnlyList<ProjectSummaryView> GetProjectsContainingPaper(string paperId) =>
        Projects.Where(p => _projectPaperIds.TryGetValue(p.Id, out var set) && set.Contains(paperId)).ToList();

    private void UpdateSelectedPaperProjects()
    {
        SelectedPaperProjects.Clear();
        if (SelectedPaper is { } paper)
        {
            foreach (var proj in GetProjectsContainingPaper(paper.Id))
            {
                SelectedPaperProjects.Add(proj);
            }
        }
        OnPropertyChanged(nameof(HasSelectedPaperProjects));
        OnPropertyChanged(nameof(HasNoSelectedPaperProjects));
    }

    public async Task<ProjectSummaryView?> CreateProjectAsync(ProjectEditorResult input)
    {
        try
        {
            var created = await _api.CreateProjectAsync(input.Name, input.Description, input.ResearchQuestion);
            await RefreshProjectsAsync();
            var match = Projects.FirstOrDefault(p => p.Id == created.Id) ?? created;
            StatusText = $"已创建研究专题「{match.Name}」。";
            return match;
        }
        catch (Exception exc)
        {
            StatusText = "创建研究专题失败：" + exc.Message;
            return null;
        }
    }

    public async Task UpdateProjectAsync(string projectId, ProjectEditorResult input)
    {
        try
        {
            await _api.UpdateProjectAsync(projectId, input.Name, input.Description, input.ResearchQuestion);
            await RefreshProjectsAsync();
            StatusText = $"已更新研究专题「{input.Name}」。";
        }
        catch (Exception exc)
        {
            StatusText = "更新研究专题失败：" + exc.Message;
        }
    }

    public async Task DeleteProjectAsync(ProjectSummaryView project)
    {
        try
        {
            await _api.DeleteProjectAsync(project.Id);
            if (ActiveProject?.Id == project.Id)
            {
                SelectLibraryFilter("all");
            }
            await RefreshProjectsAsync();
            StatusText = $"已删除研究专题「{project.Name}」（仅解除关联，所有底层论文与 Paper Card 均已保留）。";
        }
        catch (Exception exc)
        {
            StatusText = "删除研究专题失败：" + exc.Message;
        }
    }

    public async Task UpsertProjectPaperAsync(
        string projectId,
        string paperId,
        string readingStatus = "queued",
        string projectNote = "")
    {
        try
        {
            await _api.UpsertProjectPaperAsync(projectId, paperId, readingStatus, projectNote);
            await RefreshProjectsAsync();
            ApplyFilter(SearchText, SelectedPaper?.Id);
            StatusText = "已更新专题文献归属与阅读状态。";
        }
        catch (Exception exc)
        {
            StatusText = "更新专题文献失败：" + exc.Message;
        }
    }

    public async Task RemovePaperFromProjectAsync(string projectId, string paperId)
    {
        try
        {
            await _api.RemoveProjectPaperAsync(projectId, paperId);
            await RefreshProjectsAsync();
            ApplyFilter(SearchText, SelectedPaper?.Id);
            StatusText = "已将该论文从当前专题移出（原论文保留在文献库中）。";
        }
        catch (Exception exc)
        {
            StatusText = "移出专题文献失败：" + exc.Message;
        }
    }

    public void FocusPaperInSingleView(string paperId)
    {
        IsSearchPanelOpen = false;
        IsMatrixViewMode = false;
        IsSynthesisViewMode = false;
        var match = Papers.FirstOrDefault(p => p.Id == paperId);
        if (match is null)
        {
            SelectLibraryFilter("all");
            match = Papers.FirstOrDefault(p => p.Id == paperId);
        }
        if (match is not null)
        {
            SelectedPaper = match;
        }
    }

    private void RaiseProjectNotesState()
    {
        OnPropertyChanged(nameof(HasProjectNotes));
        OnPropertyChanged(nameof(HasNoProjectNotes));
        OnPropertyChanged(nameof(ProjectNotesCount));
    }

    public async Task LoadActiveProjectNotesAsync(string projectId, CancellationToken cancellationToken = default)
    {
        try
        {
            var data = await _api.GetProjectNotesAsync(projectId, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            if (ActiveProject?.Id != projectId) return;
            ProjectNotes.Clear();
            if (data.TryGetProperty("notes", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in arr.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.Object)
                    {
                        ProjectNotes.Add(ProjectNoteItem.FromJson(item));
                    }
                }
            }
            RaiseProjectNotesState();
        }
        catch (OperationCanceledException)
        {
        }
        catch
        {
        }
    }

    public async Task<bool> CreateDraftProjectNoteAsync()
    {
        if (ActiveProject is not { } proj) return false;
        var body = (DraftProjectNoteBody ?? "").Trim();
        if (string.IsNullOrWhiteSpace(body))
        {
            StatusText = "请先输入专题笔记内容再保存。";
            return false;
        }
        try
        {
            await _api.CreateProjectNoteAsync(proj.Id, body, (DraftProjectNoteTitle ?? "").Trim());
            DraftProjectNoteTitle = "";
            DraftProjectNoteBody = "";
            await LoadActiveProjectNotesAsync(proj.Id);
            StatusText = $"专题研究笔记已保存至「{proj.Name}」。";
            return true;
        }
        catch (Exception exc)
        {
            StatusText = "保存专题笔记失败：" + exc.Message;
            return false;
        }
    }

    public async Task UpdateProjectNoteAsync(string noteId, NoteEditorResult edited)
    {
        if (ActiveProject is not { } proj) return;
        try
        {
            await _api.UpdateProjectNoteAsync(proj.Id, noteId, edited.Title, edited.Note);
            await LoadActiveProjectNotesAsync(proj.Id);
            StatusText = "专题笔记修改已保存。";
        }
        catch (Exception exc)
        {
            StatusText = "更新专题笔记失败：" + exc.Message;
        }
    }

    public async Task DeleteProjectNoteAsync(ProjectNoteItem note)
    {
        if (ActiveProject is not { } proj) return;
        try
        {
            await _api.DeleteProjectNoteAsync(proj.Id, note.Id);
            await LoadActiveProjectNotesAsync(proj.Id);
            StatusText = "专题研究笔记已删除。";
        }
        catch (Exception exc)
        {
            StatusText = "删除专题笔记失败：" + exc.Message;
        }
    }

    /// <summary>
    /// 将当前专题的确定性 Evidence Matrix 导出为结构化 Markdown 文档（零 LLM 调用）。
    /// </summary>
    public string BuildActiveMatrixMarkdown()
    {
        var sb = new StringBuilder();
        var name = ActiveProject?.Name ?? "研究专题";
        sb.AppendLine($"# Evidence Matrix · {name}");
        sb.AppendLine();
        if (HasActiveProjectQuestion)
        {
            sb.AppendLine($"> **核心研究问题**：{ActiveProjectQuestion}");
            sb.AppendLine();
        }
        sb.AppendLine($"- 统计：共 {MatrixTotalPapers} 篇文献，已分析 {MatrixAnalyzedPapers} 篇（纯本地确定性聚合，零 LLM 调用）");
        sb.AppendLine();
        sb.AppendLine("## 1. 研究设计总览表");
        sb.AppendLine();
        sb.AppendLine("| 论文 | 阅读状态 | 研究类型 | 方法 | 识别 / 推断策略 | 群体与数据 |");
        sb.AppendLine("| --- | --- | --- | --- | --- | --- |");
        foreach (var row in MatrixRows)
        {
            static string Esc(string s) => (s ?? "—").Replace("|", "\\|").Replace("\r", " ").Replace("\n", " ");
            sb.AppendLine($"| {Esc(row.Title)} ({Esc(row.MetaSubtitle)}) | {Esc(row.ReadingStatusLabel)} | {Esc(row.StudyTypeLabel)} | {Esc(row.MethodSummary)} | {Esc(row.IdentificationSummary)} | {Esc(row.PopulationDataSummary)} |");
        }
        sb.AppendLine();
        sb.AppendLine("## 2. 逐篇主张与证据对照（严格区分 AUTHOR / AI REVIEW）");
        sb.AppendLine();
        foreach (var row in MatrixRows)
        {
            sb.AppendLine($"### {row.Title}");
            sb.AppendLine($"- **书目**：{row.MetaSubtitle} · **状态**：{row.ReadingStatusLabel}");
            if (row.HasProjectNote) sb.AppendLine($"- **专题备注**：{row.ProjectNote}");
            if (!row.HasCard)
            {
                sb.AppendLine("- *尚未完成 Paper Card 分析。*");
                sb.AppendLine();
                continue;
            }
            void AppendGroup(string title, IReadOnlyList<MatrixClaimItem> items)
            {
                if (items.Count == 0) return;
                sb.AppendLine($"- **{title}**");
                foreach (var c in items)
                {
                    var badge = c.HasAuditBadge ? $" [{c.AuditBadgeLabel}]" : "";
                    var humanBadge = c.HasHumanReview ? $" [人工审定：{c.HumanReviewBadgeLabel}]" : "";
                    var pages = c.HasCitations
                        ? " (" + string.Join(", ", c.Citations.Where(x => x.PageNumber > 0).Select(x => $"p.{x.PageNumber}").Distinct()) + ")"
                        : "";
                    sb.AppendLine($"  - `[{c.SourceTag}]`{badge}{humanBadge} {c.Statement}{pages}");
                    if (c.HasHumanCorrectedStatement)
                    {
                        sb.AppendLine($"    - **人工修正（HUMAN_REVIEWED）**：{c.HumanCorrectedStatement}");
                    }
                    if (!string.IsNullOrWhiteSpace(c.HumanReviewNote))
                    {
                        sb.AppendLine($"    - **审定备注**：{c.HumanReviewNote}");
                    }
                }
            }
            AppendGroup("研究问题", row.ResearchQuestions);
            AppendGroup("核心发现", row.MajorFindings);
            AppendGroup("作者解释 / 机制", row.AuthorExplanations);
            AppendGroup("作者自述局限", row.AuthorLimitations);
            AppendGroup("AI 方法学审读局限", row.AiLimitations);
            sb.AppendLine();
        }
        return sb.ToString();
    }
}
