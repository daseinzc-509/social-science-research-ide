using System.Text.Json;
using SRA.Desktop.Models;
using SRA.Desktop.Services;

namespace SRA.Desktop.ViewModels;

/// <summary>
/// 任务中心仪表盘、维护诊断以及单篇/批量研究操作编排逻辑。
/// </summary>
public partial class MainWindowViewModel
{
    public async Task LoadSettingsAsync()
    {
        await Settings.LoadAsync();
    }

    public async Task LoadDashboardAsync()
    {
        try
        {
            var data = await _api.GetDashboardAsync();
            DashboardPapers = JsonValue(data, "papers");
            DashboardAnalyzed = JsonValue(data, "analyzed");
            DashboardPending = JsonValue(data, "pending_analysis");
            DashboardReview = JsonValue(data, "needs_review");
            DashboardReferences = JsonValue(data, "references");
            DashboardOutput = "批量分析会逐篇执行 Lite → Pro；单篇失败不会中断整个队列。";
        }
        catch (Exception exc)
        {
            DashboardOutput = exc.Message;
        }
    }

    public async Task RunBatchAnalysisAsync()
    {
        if (IsBusy) return;
        IsBusy = true;
        DashboardOutput = "正在启动批量分析…";
        try
        {
            var accepted = await _api.StartBatchAnalysisAsync();
            ActiveJobId = accepted.JobId;
            await _api.WaitForJobAsync(accepted.JobId, snapshot =>
            {
                DashboardOutput = snapshot.Messages.Count == 0
                    ? $"{snapshot.Kind}: {snapshot.Status}"
                    : string.Join(Environment.NewLine, snapshot.Messages);
            });
            var papers = await _api.GetPapersAsync();
            _allPapers.Clear();
            _allPapers.AddRange(papers);
            RaiseScopeCountLabels();
            await RefreshProjectsAsync();
            ApplyFilter(SearchText, SelectedPaper?.Id);
            await LoadDashboardAsync();
        }
        catch (SraJobCancelledException exc)
        {
            DashboardOutput += Environment.NewLine + Environment.NewLine + exc.Message;
            StatusText = exc.Message;
            await LoadDashboardAsync();
        }
        catch (Exception exc)
        {
            DashboardOutput += Environment.NewLine + Environment.NewLine + exc.Message;
        }
        finally
        {
            ActiveJobId = null;
            IsCancellingJob = false;
            IsBusy = false;
        }
    }

    public async Task RunBatchReferencesAsync()
    {
        if (IsBusy) return;
        IsBusy = true;
        DashboardOutput = "正在批量提取参考文献…";
        try
        {
            var result = await _api.ExtractReferencesBatchAsync();
            DashboardOutput = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
            var papers = await _api.GetPapersAsync();
            _allPapers.Clear();
            _allPapers.AddRange(papers);
            RaiseScopeCountLabels();
            await RefreshProjectsAsync();
            ApplyFilter(SearchText, SelectedPaper?.Id);
            await LoadDashboardAsync();
        }
        catch (Exception exc)
        {
            DashboardOutput = exc.Message;
        }
        finally
        {
            IsBusy = false;
        }
    }

    public async Task RunDoctorAsync(bool deep)
    {
        try
        {
            MaintenanceOutput = deep ? "正在执行深度检查…" : "正在检查数据库…";
            var result = await _api.GetDoctorAsync(deep);
            MaintenanceOutput = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            MaintenanceOutput = exc.Message;
        }
    }

    public async Task PreviewPruneCacheAsync()
    {
        try
        {
            var result = await _api.PruneCacheAsync(false, false);
            MaintenanceOutput = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            MaintenanceOutput = exc.Message;
        }
    }

    public async Task ApplyPruneCacheAsync()
    {
        try
        {
            var result = await _api.PruneCacheAsync(true, true);
            MaintenanceOutput = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            MaintenanceOutput = exc.Message;
        }
    }

    public async Task ExportBackupArchiveAsync(bool includePdfs, Stream destination, string fileName)
    {
        try
        {
            var modeLabel = includePdfs ? "完整归档备份（含全部 PDF）" : "轻量备份（数据库 + 笔记 + 审定 + 卡片）";
            StatusText = $"正在生成并流式导出{modeLabel}…";
            MaintenanceOutput = $"正在通过 SQLite Online Backup API 导出{modeLabel}（不含任何 API Key）…";
            var bytes = await _api.DownloadBackupArchiveToStreamAsync(includePdfs, destination);
            var sizeText = bytes < 1024 * 1024
                ? $"{bytes / 1024.0:F1} KB"
                : $"{bytes / 1048576.0:F2} MB";
            MaintenanceOutput =
                $"备份导出成功：{fileName}（{sizeText}）\n"
                + $"备份模式：{modeLabel}\n"
                + "安全声明：不包含任何 API Key、DPAPI/Keychain 密钥或网络凭据。\n"
                + "离线恢复命令（先退出桌面端）：sra restore-backup <备份.zip> --replace";
            StatusText = $"研究库备份已导出：{fileName}（{sizeText}）";
        }
        catch (Exception exc)
        {
            MaintenanceOutput = "导出研究库备份失败：" + exc.Message;
            StatusText = "导出备份失败：" + exc.Message;
        }
    }

    public async Task VerifyBackupArchiveAsync(Stream zipStream, string fileName)
    {
        try
        {
            StatusText = $"正在只读校验备份包 {fileName}…";
            MaintenanceOutput = $"正在校验 {fileName} 的清单 SHA-256 与 SQLite integrity_check…";
            var report = await _api.VerifyBackupArchiveAsync(zipStream, fileName);
            var valid = report.TryGetProperty("valid", out var vProp) && vProp.GetBoolean();
            var formatted = JsonSerializer.Serialize(report, new JsonSerializerOptions
            {
                WriteIndented = true,
                Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            });
            MaintenanceOutput = (valid
                ? $"备份包校验通过：{fileName}\n\n"
                : $"备份包校验未通过：{fileName}\n\n") + formatted;
            StatusText = valid ? $"备份包校验通过：{fileName}" : $"备份包存在异常：{fileName}";
        }
        catch (Exception exc)
        {
            MaintenanceOutput = "校验备份包失败：" + exc.Message;
            StatusText = "校验备份包失败：" + exc.Message;
        }
    }

    public async Task AnalyzeAsync(AnalysisRequest request)
    {
        if (SelectedPaper is null || IsBusy)
        {
            return;
        }
        var paperId = SelectedPaper.Id;
        ResearchContext = request.ResearchContext ?? "";
        ExcludeAfterText = request.ExcludeAfterText ?? "";
        await RunJobAsync(
            "正在启动论文分析…",
            async () => await _api.StartAnalysisAsync(paperId, request),
            paperId,
            "论文分析完成"
        );
    }

    public async Task ReparseAsync()
    {
        if (SelectedPaper is null || IsBusy)
        {
            return;
        }
        var paperId = SelectedPaper.Id;
        await RunJobAsync(
            "正在重建 PDF 解析…",
            async () => await _api.StartReparseAsync(paperId),
            paperId,
            "PDF 解析已重建；旧 Card 与模型缓存已清理"
        );
    }

    public async Task ExtractReferencesAsync()
    {
        if (SelectedPaper is null || IsBusy)
        {
            return;
        }
        IsBusy = true;
        try
        {
            StatusText = "正在提取参考文献…";
            await _api.ExtractReferencesAsync(SelectedPaper.Id);
            await ReloadSelectedAsync();
            StatusText = "参考文献提取完成";
        }
        catch (Exception exc)
        {
            StatusText = $"参考文献提取失败：{exc.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    public async Task DeleteSelectedAsync()
    {
        if (SelectedPaper is null || IsBusy)
        {
            return;
        }
        IsBusy = true;
        var deletingId = SelectedPaper.Id;
        var currentIndex = Papers.IndexOf(SelectedPaper);
        var nextPreferredId = Papers
            .Where(p => p.Id != deletingId)
            .ElementAtOrDefault(Math.Max(0, Math.Min(currentIndex, Papers.Count - 2)))
            ?.Id;
        try
        {
            var result = await _api.DeletePaperAsync(deletingId, true);
            // 立刻从本地列表与搜索命中中剔除已删除论文，避免等待网络刷新期间产生 404 幽灵项
            _allPapers.RemoveAll(p => p.Id == deletingId);
            for (var i = FullTextHits.Count - 1; i >= 0; i--)
            {
                if (FullTextHits[i].PaperId == deletingId)
                {
                    FullTextHits.RemoveAt(i);
                }
            }
            RaiseScopeCountLabels();
            ApplyFilter(SearchText, nextPreferredId);
            if (SelectedPaper is null)
            {
                ClearProjection();
                SelectedDetail = null;
                SelectedTitle = "选择一篇论文";
                SelectedSubtitle = "从左侧论文库开始你的阅读。";
                SelectedParseStatus = "尚未选择文档";
                SelectedAnalysisState = "未选择";
                RawCardJson = "尚未选择论文。";
            }
            StatusText = string.IsNullOrWhiteSpace(result.FileWarning) ? "论文已删除" : result.FileWarning;
            // 不能调用受 IsBusy 守卫短路的 RefreshAsync()，改用内部 RefreshCoreAsync 同步后端全量状态与专题计数
            await RefreshCoreAsync(nextPreferredId, preserveStatusText: true);
        }
        catch (Exception exc)
        {
            StatusText = $"删除失败：{exc.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private static string JsonValue(JsonElement element, string name)
    {
        return element.TryGetProperty(name, out var value) ? value.ToString() : "—";
    }

    private static (string Stage, double Progress, bool Indeterminate) JobVisual(IReadOnlyList<string> messages, string status)
    {
        var last = messages.LastOrDefault() ?? "";
        var all = string.Join("\n", messages);
        if (status == "done") return ("完成", 100, false);
        if (status == "cancelled") return ("已取消", 0, false);
        if (status == "error") return ("发生错误", 100, false);
        if (all.Contains("Saving Paper Card", StringComparison.OrdinalIgnoreCase) || all.Contains("Paper Card saved", StringComparison.OrdinalIgnoreCase)) return ("保存结果", 96, false);
        if (last.Contains("Pro", StringComparison.OrdinalIgnoreCase) || all.Contains("Pro independent review", StringComparison.OrdinalIgnoreCase)) return ("Pro 深度审读", 82, last.Contains("sending request", StringComparison.OrdinalIgnoreCase));
        if (last.Contains("Metadata layout recovery", StringComparison.OrdinalIgnoreCase) || last.Contains("Preparing evidence ledger", StringComparison.OrdinalIgnoreCase)) return ("整理证据与元数据", 52, false);
        if (last.Contains("Lite request", StringComparison.OrdinalIgnoreCase) || all.Contains("Lite request", StringComparison.OrdinalIgnoreCase)) return ("Lite 事实提取", 62, last.Contains("sending request", StringComparison.OrdinalIgnoreCase));
        if (last.Contains("Docling:", StringComparison.OrdinalIgnoreCase)) return ("Docling 文档解析", 28, true);
        if (last.Contains("Rebuild:", StringComparison.OrdinalIgnoreCase)) return ("重建 PDF 解析", 18, true);
        if (last.Contains("Import:", StringComparison.OrdinalIgnoreCase) || last.Contains("Importing", StringComparison.OrdinalIgnoreCase)) return ("导入 PDF", 14, true);
        return ("准备中", 6, true);
    }
}
