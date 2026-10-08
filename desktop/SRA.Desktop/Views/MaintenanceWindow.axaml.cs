using System.Text.Json;
using Avalonia.Controls;
using Avalonia.Interactivity;
using SRA.Desktop.Services;

namespace SRA.Desktop.Views;

public partial class MaintenanceWindow : Window
{
    private readonly SraApiClient? _api;

    public MaintenanceWindow()
    {
        InitializeComponent();
    }

    public MaintenanceWindow(SraApiClient api) : this()
    {
        _api = api;
        Opened += async (_, _) => await CheckAsync(false);
    }

    private async Task CheckAsync(bool deep)
    {
        if (_api is null) return;
        try
        {
            OutputBox.Text = deep ? "正在执行深度检查…" : "正在检查…";
            var result = await _api.GetDoctorAsync(deep);
            OutputBox.Text = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            OutputBox.Text = exc.Message;
        }
    }

    private async void QuickCheck_Click(object? sender, RoutedEventArgs e) => await CheckAsync(false);
    private async void DeepCheck_Click(object? sender, RoutedEventArgs e) => await CheckAsync(true);

    private async void PreviewPrune_Click(object? sender, RoutedEventArgs e)
    {
        if (_api is null) return;
        try
        {
            var result = await _api.PruneCacheAsync(false, false);
            OutputBox.Text = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            OutputBox.Text = exc.Message;
        }
    }

    private async void ApplyPrune_Click(object? sender, RoutedEventArgs e)
    {
        if (_api is null) return;
        var confirm = new ConfirmWindow(
            "清理旧模型缓存",
            "只删除旧 prompt 版本的模型缓存，不删除 PDF、Paper Card、source blocks 或 notes。继续？",
            "清理缓存"
        );
        if (!await confirm.ShowDialog<bool>(this))
        {
            return;
        }
        try
        {
            var result = await _api.PruneCacheAsync(true, true);
            OutputBox.Text = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (Exception exc)
        {
            OutputBox.Text = exc.Message;
        }
    }
}
