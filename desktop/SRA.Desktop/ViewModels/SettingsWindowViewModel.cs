using CommunityToolkit.Mvvm.ComponentModel;
using SRA.Desktop.Models;
using SRA.Desktop.Services;

namespace SRA.Desktop.ViewModels;

public partial class SettingsWindowViewModel : ObservableObject
{
    private readonly SraApiClient _api;

    public SettingsWindowViewModel(SraApiClient api)
    {
        _api = api;
    }

    [ObservableProperty]
    private string liteApiKey = "";

    [ObservableProperty]
    private string liteKeyHint = "留空表示保持现有 Key";

    [ObservableProperty]
    private string liteApiBaseUrl = "";

    [ObservableProperty]
    private string liteModel = "";

    [ObservableProperty]
    private string proApiKey = "";

    [ObservableProperty]
    private string proKeyHint = "留空表示保持现有 Key";

    [ObservableProperty]
    private string proApiBaseUrl = "";

    [ObservableProperty]
    private string proModel = "";

    [ObservableProperty]
    private bool useLiteConnection;

    [ObservableProperty]
    private string statusText = "";

    [ObservableProperty]
    private bool isBusy;

    public bool IsProConnectionEditable => !UseLiteConnection;

    partial void OnUseLiteConnectionChanged(bool value)
    {
        OnPropertyChanged(nameof(IsProConnectionEditable));
    }

    public async Task LoadAsync()
    {
        IsBusy = true;
        try
        {
            var settings = await _api.GetModelSettingsAsync();
            LiteApiBaseUrl = settings.LiteApiBaseUrl;
            LiteModel = settings.LiteModel;
            ProApiBaseUrl = settings.ProApiBaseUrl;
            ProModel = settings.ProModel;
            UseLiteConnection = settings.SameConnection;
            LiteKeyHint = settings.HasLiteApiKey
                ? $"已设置：{settings.LiteApiKeyMasked}；留空保持不变"
                : "尚未设置 Lite API Key";
            ProKeyHint = settings.HasProApiKey
                ? $"已设置：{settings.ProApiKeyMasked}；留空保持不变"
                : "尚未设置 Pro API Key";
            StatusText = "";
        }
        catch (Exception exc)
        {
            StatusText = exc.Message;
        }
        finally
        {
            IsBusy = false;
        }
    }

    public async Task<bool> SaveAsync()
    {
        IsBusy = true;
        StatusText = "正在保存…";
        try
        {
            await _api.UpdateModelSettingsAsync(
                new ModelSettingsUpdate
                {
                    LiteApiKey = string.IsNullOrWhiteSpace(LiteApiKey) ? null : LiteApiKey.Trim(),
                    LiteApiBaseUrl = LiteApiBaseUrl.Trim(),
                    LiteModel = LiteModel.Trim(),
                    ProApiKey = string.IsNullOrWhiteSpace(ProApiKey) ? null : ProApiKey.Trim(),
                    ProApiBaseUrl = ProApiBaseUrl.Trim(),
                    ProModel = ProModel.Trim(),
                    ProUseLiteConnection = UseLiteConnection,
                }
            );
            StatusText = "模型设置已保存。";
            return true;
        }
        catch (Exception exc)
        {
            StatusText = exc.Message;
            return false;
        }
        finally
        {
            IsBusy = false;
        }
    }
}
