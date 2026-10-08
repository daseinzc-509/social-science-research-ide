using System.Text.Json;
using Avalonia.Controls;
using Avalonia.Interactivity;
using SRA.Desktop.Services;

namespace SRA.Desktop.Views;

public partial class DashboardWindow : Window
{
    private readonly SraApiClient? _api;

    public DashboardWindow()
    {
        InitializeComponent();
    }

    public DashboardWindow(SraApiClient api) : this()
    {
        _api = api;
        Opened += async (_, _) => await LoadAsync();
    }

    private async Task LoadAsync()
    {
        if (_api is null) return;
        try
        {
            var data = await _api.GetDashboardAsync();
            PapersText.Text = Value(data, "papers");
            AnalyzedText.Text = Value(data, "analyzed");
            PendingText.Text = Value(data, "pending_analysis");
            ReviewText.Text = Value(data, "needs_review");
            ReferencesText.Text = Value(data, "references");
            LogBox.Text = "批量分析会逐篇执行 Lite → Pro；单篇失败不会中断整个队列。";
        }
        catch (Exception exc)
        {
            LogBox.Text = exc.Message;
        }
    }

    private async void BatchAnalyze_Click(object? sender, RoutedEventArgs e)
    {
        if (_api is null) return;
        try
        {
            LogBox.Text = "正在启动批量分析…";
            var accepted = await _api.StartBatchAnalysisAsync();
            await _api.WaitForJobAsync(accepted.JobId, snapshot =>
            {
                LogBox.Text = snapshot.Messages.Count == 0
                    ? $"{snapshot.Kind}: {snapshot.Status}"
                    : string.Join(Environment.NewLine, snapshot.Messages);
            });
            await LoadAsync();
        }
        catch (Exception exc)
        {
            LogBox.Text += Environment.NewLine + Environment.NewLine + exc.Message;
        }
    }

    private async void BatchReferences_Click(object? sender, RoutedEventArgs e)
    {
        if (_api is null) return;
        try
        {
            LogBox.Text = "正在批量提取参考文献…";
            var result = await _api.ExtractReferencesBatchAsync();
            LogBox.Text = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
            await LoadAsync();
        }
        catch (Exception exc)
        {
            LogBox.Text = exc.Message;
        }
    }

    private async void Refresh_Click(object? sender, RoutedEventArgs e) => await LoadAsync();

    private static string Value(JsonElement element, string name)
    {
        if (!element.TryGetProperty(name, out var value)) return "—";
        return value.ToString();
    }
}
