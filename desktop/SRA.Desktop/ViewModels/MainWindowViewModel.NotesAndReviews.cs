using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using SRA.Desktop.Models;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// 个人阅读笔记（MY NOTE）与 Claim 单条人工审定状态（HUMAN_REVIEWED）视图模型逻辑。
/// </summary>
public partial class MainWindowViewModel
{
    private readonly Dictionary<string, ClaimReviewItem> _claimReviewMap = new(StringComparer.Ordinal);

    public ObservableCollection<UserNoteItem> UserNotes { get; } = [];
    public ObservableCollection<ClaimReviewItem> ClaimReviews { get; } = [];
    public ObservableCollection<ClaimReviewItem> OrphanClaimReviews { get; } = [];

    [ObservableProperty] private string draftNoteTitle = "";
    [ObservableProperty] private string draftNotePage = "";
    [ObservableProperty] private string draftNoteBody = "";

    public bool HasUserNotes => UserNotes.Count > 0;
    public bool HasNoUserNotes => UserNotes.Count == 0;
    public string UserNotesCount => UserNotes.Count.ToString();
    public bool HasClaimReviews => ClaimReviews.Count > 0;
    public bool HasOrphanClaimReviews => OrphanClaimReviews.Count > 0;
    public string ReviewedClaimsCount => ClaimReviews.Count.ToString();
    public string HumanProgressSummary => $"我的笔记 {UserNotes.Count} 条 · 已审定主张 {ClaimReviews.Count} 条";

    public string ClaimReviewBreakdown
    {
        get
        {
            if (ClaimReviews.Count == 0) return "尚未对任何主张做出人工审定。在「精读」或「事实证据」卡片底部可直接一键确认或修正。";
            var confirmed = ClaimReviews.Count(r => r.IsConfirmed);
            var corrected = ClaimReviews.Count(r => r.IsCorrected);
            var rejected = ClaimReviews.Count(r => r.IsRejected);
            var unresolved = ClaimReviews.Count(r => r.IsUnresolved);
            return $"已确认 {confirmed} · 人工修正 {corrected} · 存疑驳回 {rejected} · 待定 {unresolved}";
        }
    }

    private void ProjectNotesAndReviews(PaperDetailResponse detail, HashSet<string> activeClaimIds)
    {
        UserNotes.Clear();
        foreach (var raw in detail.UserNotes)
        {
            if (raw.ValueKind == JsonValueKind.Object)
            {
                UserNotes.Add(UserNoteItem.FromJson(raw));
            }
        }

        _claimReviewMap.Clear();
        ClaimReviews.Clear();
        OrphanClaimReviews.Clear();
        foreach (var raw in detail.ClaimReviews)
        {
            if (raw.ValueKind != JsonValueKind.Object) continue;
            var claimId = JsonRead.Text(raw, "claim_id");
            var isOrphan = activeClaimIds.Count > 0 && !activeClaimIds.Contains(claimId);
            var item = ClaimReviewItem.FromJson(raw, isOrphan: isOrphan);
            if (!string.IsNullOrWhiteSpace(item.ClaimId))
            {
                _claimReviewMap[item.ClaimId] = item;
            }
            ClaimReviews.Add(item);
            if (isOrphan)
            {
                OrphanClaimReviews.Add(item);
            }
        }

        RaiseNotesAndReviewsState();
    }

    private ClaimItem AttachHumanReview(ClaimItem item) =>
        _claimReviewMap.TryGetValue(item.ClaimId, out var review)
            ? item.WithHumanReview(review)
            : item;

    private void ClearNotesAndReviews()
    {
        _claimReviewMap.Clear();
        UserNotes.Clear();
        ClaimReviews.Clear();
        OrphanClaimReviews.Clear();
        RaiseNotesAndReviewsState();
    }

    private void RaiseNotesAndReviewsState()
    {
        OnPropertyChanged(nameof(HasUserNotes));
        OnPropertyChanged(nameof(HasNoUserNotes));
        OnPropertyChanged(nameof(UserNotesCount));
        OnPropertyChanged(nameof(HasClaimReviews));
        OnPropertyChanged(nameof(HasOrphanClaimReviews));
        OnPropertyChanged(nameof(ReviewedClaimsCount));
        OnPropertyChanged(nameof(HumanProgressSummary));
        OnPropertyChanged(nameof(ClaimReviewBreakdown));
    }

    public async Task<bool> CreateDraftNoteAsync()
    {
        if (SelectedPaper is not { } paper) return false;
        var body = (DraftNoteBody ?? "").Trim();
        if (string.IsNullOrWhiteSpace(body))
        {
            StatusText = "请先输入笔记内容再保存。";
            return false;
        }
        int? page = null;
        var rawPage = (DraftNotePage ?? "").Trim();
        if (!string.IsNullOrWhiteSpace(rawPage))
        {
            if (!int.TryParse(rawPage, out var parsed) || parsed < 1)
            {
                StatusText = "关联页码必须是大于 0 的整数，或留空。";
                return false;
            }
            page = parsed;
        }

        try
        {
            await _api.CreatePaperNoteAsync(paper.Id, body, (DraftNoteTitle ?? "").Trim(), page);
            DraftNoteTitle = "";
            DraftNotePage = "";
            DraftNoteBody = "";
            await LoadSelectedPaperAsync(paper, CancellationToken.None);
            StatusText = "个人笔记已保存并同步到全文检索。";
            return true;
        }
        catch (Exception exc)
        {
            StatusText = "保存个人笔记失败：" + exc.Message;
            return false;
        }
    }

    public async Task UpdateNoteAsync(string noteId, NoteEditorResult edited)
    {
        if (SelectedPaper is not { } paper) return;
        try
        {
            await _api.UpdatePaperNoteAsync(paper.Id, noteId, edited.Title, edited.Note, edited.PageNumber);
            await LoadSelectedPaperAsync(paper, CancellationToken.None);
            StatusText = "笔记修改已保存。";
        }
        catch (Exception exc)
        {
            StatusText = "更新笔记失败：" + exc.Message;
        }
    }

    public async Task DeleteNoteAsync(UserNoteItem note)
    {
        if (SelectedPaper is not { } paper) return;
        try
        {
            await _api.DeletePaperNoteAsync(paper.Id, note.Id);
            await LoadSelectedPaperAsync(paper, CancellationToken.None);
            StatusText = "个人笔记已删除。";
        }
        catch (Exception exc)
        {
            StatusText = "删除笔记失败：" + exc.Message;
        }
    }

    public async Task SaveClaimReviewAsync(
        ClaimItem claim,
        string userStatus,
        string correctedStatement = "",
        string note = "")
    {
        if (SelectedPaper is not { } paper) return;
        try
        {
            await _api.UpsertClaimReviewAsync(
                paper.Id,
                claim.ClaimId,
                userStatus,
                fieldName: claim.FieldName,
                originalStatement: claim.Statement,
                correctedStatement: correctedStatement,
                note: note);
            await LoadSelectedPaperAsync(paper, CancellationToken.None);
            if (IsProjectScopeActive && ActiveProject is { } proj)
            {
                await LoadActiveProjectMatrixAsync(proj.Id);
            }
            StatusText = $"已保存人工审定：{ClaimReviewItem.StatusToLabel(userStatus)}（不修改原始 Paper Card）。";
        }
        catch (Exception exc)
        {
            StatusText = "保存人工审定失败：" + exc.Message;
        }
    }

    public async Task DeleteClaimReviewAsync(string claimId)
    {
        if (SelectedPaper is not { } paper) return;
        try
        {
            await _api.DeleteClaimReviewAsync(paper.Id, claimId);
            await LoadSelectedPaperAsync(paper, CancellationToken.None);
            if (IsProjectScopeActive && ActiveProject is { } proj)
            {
                await LoadActiveProjectMatrixAsync(proj.Id);
            }
            StatusText = "已清除该条主张的人工审定记录。";
        }
        catch (Exception exc)
        {
            StatusText = "清除人工审定失败：" + exc.Message;
        }
    }
}
