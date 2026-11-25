using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using SRA.Desktop.Models;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// 基于已审定 Evidence Matrix 的多文献综合（Consensus / Conflict / Methodological Divergence / Research Gaps）视图模型逻辑。
/// </summary>
public partial class MainWindowViewModel
{
    private CancellationTokenSource? _synthesisCts;

    public ObservableCollection<SynthesisHistorySummary> ProjectSyntheses { get; } = [];
    public ObservableCollection<SynthesisPointItem> SynthesisConsensus { get; } = [];
    public ObservableCollection<SynthesisPointItem> SynthesisConflicts { get; } = [];
    public ObservableCollection<SynthesisPointItem> SynthesisMethodDifferences { get; } = [];
    public ObservableCollection<SynthesisPointItem> SynthesisResearchGaps { get; } = [];
    public ObservableCollection<string> SynthesisLimitations { get; } = [];

    [ObservableProperty] private bool isSynthesisViewMode;
    [ObservableProperty] private bool isRunningSynthesis;
    [ObservableProperty] private bool synthesisOnlyReviewed;
    [ObservableProperty] private bool synthesisIncludeProjectNotes = true;
    [ObservableProperty] private string synthesisFocusInstruction = "";
    [ObservableProperty] private SynthesisHistorySummary? selectedSynthesisSummary;
    [ObservableProperty] private SynthesisDetailItem? activeSynthesisDetail;
    [ObservableProperty] private string synthesisStatusMessage = "";

    public bool ShowProjectSynthesisView => !IsSearchPanelOpen && IsProjectScopeActive && IsSynthesisViewMode;
    public bool HasSynthesisHistory => ProjectSyntheses.Count > 0;
    public bool HasActiveSynthesis => ActiveSynthesisDetail is not null;
    public bool HasNoActiveSynthesis => ActiveSynthesisDetail is null && !IsRunningSynthesis;
    public bool HasSynthesisStatusMessage => !string.IsNullOrWhiteSpace(SynthesisStatusMessage);

    public string ActiveSynthesisExecutiveSummary => ActiveSynthesisDetail?.ExecutiveSummary ?? "";
    public string ActiveSynthesisAuditStats => ActiveSynthesisDetail?.AuditStatsLine ?? "";
    public bool HasSynthesisConsensus => SynthesisConsensus.Count > 0;
    public bool HasSynthesisConflicts => SynthesisConflicts.Count > 0;
    public bool HasSynthesisMethodDifferences => SynthesisMethodDifferences.Count > 0;
    public bool HasSynthesisResearchGaps => SynthesisResearchGaps.Count > 0;
    public bool HasSynthesisLimitations => SynthesisLimitations.Count > 0;

    public string SynthesisConsensusCount => SynthesisConsensus.Count.ToString();
    public string SynthesisConflictsCount => SynthesisConflicts.Count.ToString();
    public string SynthesisMethodDifferencesCount => SynthesisMethodDifferences.Count.ToString();
    public string SynthesisResearchGapsCount => SynthesisResearchGaps.Count.ToString();

    partial void OnIsSynthesisViewModeChanged(bool value)
    {
        RaiseProjectScopeState();
        RaiseSynthesisState();
    }

    partial void OnIsRunningSynthesisChanged(bool value) => RaiseSynthesisState();
    partial void OnActiveSynthesisDetailChanged(SynthesisDetailItem? value) => RaiseSynthesisState();
    partial void OnSynthesisStatusMessageChanged(string value) => OnPropertyChanged(nameof(HasSynthesisStatusMessage));

    partial void OnSelectedSynthesisSummaryChanged(SynthesisHistorySummary? value)
    {
        if (value is null || ActiveProject is null) return;
        if (ActiveSynthesisDetail?.Id == value.Id) return;
        _ = LoadSynthesisDetailAsync(ActiveProject.Id, value.Id);
    }

    private void RaiseSynthesisState()
    {
        OnPropertyChanged(nameof(ShowProjectSynthesisView));
        OnPropertyChanged(nameof(HasSynthesisHistory));
        OnPropertyChanged(nameof(HasActiveSynthesis));
        OnPropertyChanged(nameof(HasNoActiveSynthesis));
        OnPropertyChanged(nameof(ActiveSynthesisExecutiveSummary));
        OnPropertyChanged(nameof(ActiveSynthesisAuditStats));
        OnPropertyChanged(nameof(HasSynthesisConsensus));
        OnPropertyChanged(nameof(HasSynthesisConflicts));
        OnPropertyChanged(nameof(HasSynthesisMethodDifferences));
        OnPropertyChanged(nameof(HasSynthesisResearchGaps));
        OnPropertyChanged(nameof(HasSynthesisLimitations));
        OnPropertyChanged(nameof(SynthesisConsensusCount));
        OnPropertyChanged(nameof(SynthesisConflictsCount));
        OnPropertyChanged(nameof(SynthesisMethodDifferencesCount));
        OnPropertyChanged(nameof(SynthesisResearchGapsCount));
    }

    private void ClearSynthesisState()
    {
        _synthesisCts?.Cancel();
        _synthesisCts?.Dispose();
        _synthesisCts = null;
        ProjectSyntheses.Clear();
        SelectedSynthesisSummary = null;
        ApplySynthesisDetail(null);
        SynthesisStatusMessage = "";
        RaiseSynthesisState();
    }

    private void ApplySynthesisDetail(SynthesisDetailItem? detail)
    {
        ActiveSynthesisDetail = detail;
        SynthesisConsensus.Clear();
        SynthesisConflicts.Clear();
        SynthesisMethodDifferences.Clear();
        SynthesisResearchGaps.Clear();
        SynthesisLimitations.Clear();
        if (detail is not null)
        {
            foreach (var item in detail.Consensus) SynthesisConsensus.Add(item);
            foreach (var item in detail.Conflicts) SynthesisConflicts.Add(item);
            foreach (var item in detail.MethodologicalDifferences) SynthesisMethodDifferences.Add(item);
            foreach (var item in detail.ResearchGaps) SynthesisResearchGaps.Add(item);
            foreach (var lim in detail.Limitations) SynthesisLimitations.Add(lim);
        }
        RaiseSynthesisState();
    }

    public async Task OpenProjectSynthesisViewAsync()
    {
        if (!IsProjectScopeActive || ActiveProject is not { } proj) return;
        IsSearchPanelOpen = false;
        IsMatrixViewMode = false;
        IsSynthesisViewMode = true;
        await LoadProjectSynthesesAsync(proj.Id);
    }

    public async Task LoadProjectSynthesesAsync(string projectId, string? preferredSynthesisId = null)
    {
        _synthesisCts?.Cancel();
        _synthesisCts?.Dispose();
        var cts = new CancellationTokenSource();
        _synthesisCts = cts;
        try
        {
            var data = await _api.ListProjectSynthesesAsync(projectId, cts.Token);
            cts.Token.ThrowIfCancellationRequested();
            if (ActiveProject?.Id != projectId) return;

            ProjectSyntheses.Clear();
            if (data.TryGetProperty("syntheses", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in arr.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.Object)
                    {
                        ProjectSyntheses.Add(SynthesisHistorySummary.FromJson(item));
                    }
                }
            }
            RaiseSynthesisState();

            var target = !string.IsNullOrWhiteSpace(preferredSynthesisId)
                ? ProjectSyntheses.FirstOrDefault(s => s.Id == preferredSynthesisId) ?? ProjectSyntheses.FirstOrDefault()
                : ProjectSyntheses.FirstOrDefault();

            if (target is not null)
            {
                SelectedSynthesisSummary = target;
                await LoadSynthesisDetailAsync(projectId, target.Id, cts.Token);
            }
            else
            {
                SelectedSynthesisSummary = null;
                ApplySynthesisDetail(null);
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception exc)
        {
            SynthesisStatusMessage = "加载历史综合快照失败：" + exc.Message;
        }
    }

    public async Task LoadSynthesisDetailAsync(string projectId, string synthesisId, CancellationToken cancellationToken = default)
    {
        try
        {
            var data = await _api.GetProjectSynthesisAsync(projectId, synthesisId, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            if (ActiveProject?.Id != projectId) return;
            ApplySynthesisDetail(SynthesisDetailItem.FromJson(data));
            SynthesisStatusMessage = "";
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception exc)
        {
            SynthesisStatusMessage = "加载综合快照详情失败：" + exc.Message;
        }
    }

    public async Task<SynthesisPreviewInfo?> PreviewActiveProjectSynthesisAsync()
    {
        if (ActiveProject is not { } proj) return null;
        SynthesisStatusMessage = "";
        try
        {
            var raw = await _api.PreviewProjectSynthesisAsync(
                proj.Id,
                includeUnreviewed: !SynthesisOnlyReviewed,
                includeProjectNotes: SynthesisIncludeProjectNotes,
                focusInstruction: SynthesisFocusInstruction);
            return SynthesisPreviewInfo.FromJson(raw);
        }
        catch (Exception exc)
        {
            SynthesisStatusMessage = "无法发起跨文献综合预检：" + exc.Message;
            StatusText = SynthesisStatusMessage;
            return null;
        }
    }

    public async Task RunApprovedProjectSynthesisAsync(SynthesisPreviewInfo preview, SynthesisConsentResult consent)
    {
        if (ActiveProject is not { } proj) return;
        IsRunningSynthesis = true;
        SynthesisStatusMessage = $"正在调用 {preview.ProModel} 对 {preview.IncludedPaperCount} 篇文献的 {preview.IncludedClaimCount} 条主张执行跨文献综合，请稍候…";
        StatusText = SynthesisStatusMessage;
        try
        {
            var raw = await _api.RunProjectSynthesisAsync(
                proj.Id,
                expectedSnapshotDigest: preview.SnapshotDigest,
                approveExternalSend: consent.ApproveExternalSend,
                acknowledgeApiCost: consent.AcknowledgeApiCost,
                includeUnreviewed: preview.IncludeUnreviewed,
                includeProjectNotes: preview.IncludeProjectNotes,
                focusInstruction: preview.FocusInstruction);
            var detail = SynthesisDetailItem.FromJson(raw);
            ApplySynthesisDetail(detail);
            await LoadProjectSynthesesAsync(proj.Id, preferredSynthesisId: detail.Id);
            SynthesisStatusMessage = "";
            StatusText = $"跨文献综合已完成并独立归档（{detail.IncludedPaperCount} 篇文献 · {detail.IncludedClaimCount} 条主张，全部引文已通过白名单校验）。";
        }
        catch (Exception exc)
        {
            SynthesisStatusMessage = "跨文献综合执行失败：" + exc.Message;
            StatusText = SynthesisStatusMessage;
        }
        finally
        {
            IsRunningSynthesis = false;
        }
    }

    public async Task DeleteActiveSynthesisAsync()
    {
        if (ActiveProject is not { } proj || ActiveSynthesisDetail is not { } detail) return;
        try
        {
            await _api.DeleteProjectSynthesisAsync(proj.Id, detail.Id);
            await LoadProjectSynthesesAsync(proj.Id);
            StatusText = "已删除该条历史综合快照（不影响原论文、证据矩阵或人工审定记录）。";
        }
        catch (Exception exc)
        {
            StatusText = "删除综合快照失败：" + exc.Message;
        }
    }
}
