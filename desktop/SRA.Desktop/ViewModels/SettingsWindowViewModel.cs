using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using SRA.Desktop.Models;
using SRA.Desktop.Services;

namespace SRA.Desktop.ViewModels;

/// <summary>Lite/Pro model settings plus explicitly optional Docling and Vision settings.</summary>
public partial class SettingsWindowViewModel : ObservableObject
{
    private readonly SraApiClient _api;

    public SettingsWindowViewModel(SraApiClient api)
    {
        _api = api;
    }

    [ObservableProperty] private string liteApiKey = "";
    [ObservableProperty] private string liteKeyHint = "留空表示保持现有 Key";
    [ObservableProperty] private string liteApiBaseUrl = "";
    [ObservableProperty] private string liteModel = "";
    [ObservableProperty] private string proApiKey = "";
    [ObservableProperty] private string proKeyHint = "留空表示保持现有 Key";
    [ObservableProperty] private string proApiBaseUrl = "";
    [ObservableProperty] private string proModel = "";
    [ObservableProperty] private bool useLiteConnection;
    [ObservableProperty] private string statusText = "";
    [ObservableProperty] private bool isBusy;

    // Formula recognition is Docling's local model enrichment; Vision is a
    // separately configured, opt-in image-capable remote model.
    [ObservableProperty] private bool formulaEnabled;
    [ObservableProperty] private bool visionEnabled;
    [ObservableProperty] private bool visionUseProConnection = true;
    [ObservableProperty] private string visionApiKey = "";
    [ObservableProperty] private string visionKeyHint = "留空表示保持现有密钥";
    [ObservableProperty] private string visionApiBaseUrl = "";
    [ObservableProperty] private string visionModel = "";
    [ObservableProperty] private string visualStatusText = "";
    [ObservableProperty] private bool visualBusy;

    public bool IsProConnectionEditable => !UseLiteConnection;
    public bool IsVisionConnectionEditable => !VisionUseProConnection;

    partial void OnUseLiteConnectionChanged(bool value) =>
        OnPropertyChanged(nameof(IsProConnectionEditable));

    partial void OnVisionUseProConnectionChanged(bool value) =>
        OnPropertyChanged(nameof(IsVisionConnectionEditable));

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
            LiteApiKey = "";
            ProApiKey = "";
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
        await LoadVisualAsync();
    }

    public async Task LoadVisualAsync()
    {
        VisualBusy = true;
        try
        {
            var config = await _api.GetVisualSettingsAsync();
            FormulaEnabled = GetBool(config, "formula_enabled");
            VisionEnabled = GetBool(config, "vision_enabled");
            VisionUseProConnection = GetBool(config, "use_pro_connection");
            VisionApiBaseUrl = GetText(config, "vision_api_base_url");
            VisionModel = GetText(config, "vision_model");
            VisionKeyHint = GetBool(config, "has_vision_api_key")
                ? $"已设置：{GetText(config, "vision_api_key_masked")}；留空保持不变"
                : "尚未设置独立 Vision API Key";
            VisionApiKey = "";
            VisualStatusText = "公式默认本地执行；视觉分析只在点击具体图片后向模型发起请求。";
        }
        catch (Exception exc)
        {
            VisualStatusText = "读取公式/图片设置失败：" + exc.Message;
        }
        finally
        {
            VisualBusy = false;
        }
    }

    private static bool GetBool(JsonElement obj, string name) =>
        obj.ValueKind == JsonValueKind.Object
        && obj.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.True;

    private static string GetText(JsonElement obj, string name) =>
        obj.ValueKind == JsonValueKind.Object
        && obj.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? ""
            : "";

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
            StatusText = "Lite / Pro 模型设置已保存。";
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

    public async Task<bool> SaveVisualAsync()
    {
        VisualBusy = true;
        VisualStatusText = "正在保存公式和图像模型配置…";
        try
        {
            await _api.SaveVisualSettingsAsync(
                FormulaEnabled, VisionEnabled, VisionUseProConnection,
                VisionApiKey, VisionApiBaseUrl, VisionModel);
            VisionApiKey = "";
            VisualStatusText = FormulaEnabled
                ? "已保存。需要对论文执行【重新解析】和【重新分析】，公式列表才会刷新。"
                : "已保存。关闭公式增强不影响已存历史 Card；重新解析后生效。";
            return true;
        }
        catch (Exception exc)
        {
            VisualStatusText = "无法保存公式/图片设置：" + exc.Message;
            return false;
        }
        finally
        {
            VisualBusy = false;
        }
    }
}
