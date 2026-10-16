using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using SRA.Desktop.Services;

namespace SRA.Desktop.Views;

/// <summary>
/// An intentionally native Avalonia window using the already authenticated
/// SraApiClient. Secret keys are write-only and never read back into a control.
/// </summary>
public sealed class ModelCenterWindow : Window
{
    private readonly SraApiClient _api;
    private readonly ComboBox _downloadMode = Picker("系统代理", "直连", "自定义 HTTP 代理");
    private readonly ComboBox _aiMode = Picker("系统代理", "直连", "自定义 HTTP 代理");
    private readonly TextBox _downloadProxy = Edit("http://127.0.0.1:7890");
    private readonly TextBox _aiProxy = Edit("http://127.0.0.1:7890");
    private readonly TextBlock _modelState = new() { TextWrapping = Avalonia.Media.TextWrapping.Wrap };
    private readonly CheckBox _includeFormula = new() { Content = "包含公式增强权重（需要额外下载）" };
    private readonly CheckBox _useManagedModels = new() { Content = "使用已离线验证的本地模型（取消勾选可恢复旧版缓存方式）" };
    private readonly ComboBox _profiles = new() { MinWidth = 250 };
    private readonly ComboBox _stage = Picker("Lite", "Pro", "Vision");
    private readonly ComboBox _remoteModels = new() { MinWidth = 260 };
    private readonly TextBox _name = Edit("配置名称，例如：火山方舟 GLM");
    private readonly TextBox _url = Edit("https://.../v1 或服务商指定 Base URL");
    private readonly TextBox _model = Edit("模型 ID，例如 kimi-k3");
    private readonly TextBox _key = Edit("API Key（留空表示保留已有密钥）");
    private readonly TextBox _tokens = Edit("8192");
    private readonly TextBlock _status = new() { TextWrapping = Avalonia.Media.TextWrapping.Wrap };
    private List<ProfileEntry> _profileEntries = new();
    private bool _loading;

    private sealed class ProfileEntry
    {
        public string Id { get; init; } = "";
        public string Name { get; init; } = "";
        public string BaseUrl { get; init; } = "";
        public string Model { get; init; } = "";
        public int MaxTokens { get; init; } = 8192;
        public bool HasApiKey { get; init; }
        public override string ToString() => $"{Name} · {Model}";
    }

    private static ComboBox Picker(params string[] choices) => new()
    {
        ItemsSource = choices,
        SelectedIndex = 0,
        HorizontalAlignment = HorizontalAlignment.Stretch,
    };

    private static TextBox Edit(string placeholder) => new()
    {
        PlaceholderText = placeholder,
        MinWidth = 260,
    };

    private static Button Action(string text, Action callback)
    {
        var button = new Button { Content = text, Padding = new Thickness(14, 9) };
        button.Click += (_, _) => callback();
        return button;
    }

    private static StackPanel Section(string title, string description)
    {
        var section = new StackPanel { Spacing = 10, Margin = new Thickness(0, 0, 0, 18) };
        section.Children.Add(new TextBlock { Text = title, FontSize = 17, FontWeight = Avalonia.Media.FontWeight.SemiBold });
        section.Children.Add(new TextBlock { Text = description, TextWrapping = Avalonia.Media.TextWrapping.Wrap, Opacity = 0.73 });
        return section;
    }

    private static StackPanel Buttons(params Control[] children)
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 9 };
        foreach (var child in children) panel.Children.Add(child);
        return panel;
    }

    public ModelCenterWindow(SraApiClient api)
    {
        _api = api;
        Title = "SRA · 模型、网络与 API 配置中心";
        Width = 840;
        Height = 820;
        MinWidth = 700;
        MinHeight = 570;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        _key.PasswordChar = '●';
        _profiles.SelectionChanged += (_, _) => ShowSelectedProfile();
        var page = new StackPanel { Spacing = 12, Margin = new Thickness(28) };
        page.Children.Add(new TextBlock { Text = "模型配置中心", FontSize = 24, FontWeight = Avalonia.Media.FontWeight.Bold });
        page.Children.Add(new TextBlock
        {
            Text = "模型下载代理与 AI 请求代理分别配置。密钥使用原有 Windows DPAPI / macOS Keychain 加密保存。",
            TextWrapping = Avalonia.Media.TextWrapping.Wrap,
        });

        var network = Section("01  网络与代理", "支持 HTTP(S) 代理，例如 Clash 的 HTTP/Mixed 端口。请不要填写 SOCKS-only 端口。");
        network.Children.Add(new TextBlock { Text = "Docling / Hugging Face 下载代理" });
        network.Children.Add(_downloadMode);
        network.Children.Add(_downloadProxy);
        network.Children.Add(new TextBlock { Text = "AI API 请求代理（国内 API 可以单独选择直连）" });
        network.Children.Add(_aiMode);
        network.Children.Add(_aiProxy);
        network.Children.Add(Buttons(Action("保存网络设置", () => _ = RunAsync(SaveNetworkAsync))));
        page.Children.Add(network);

        var weights = Section("02  Docling 离线模型", "下载布局、表格与中文 RapidOCR 模型，并在断网子进程里运行一次真实 PDF 转换才标记可用。");
        weights.Children.Add(_modelState);
        weights.Children.Add(_includeFormula);
        weights.Children.Add(_useManagedModels);
        weights.Children.Add(Buttons(Action("保存模型使用模式", () => _ = RunAsync(SetModelModeAsync))));
        weights.Children.Add(Buttons(
            Action("下载并离线验证", () => _ = RunAsync(() => StartModelOperationAsync(true))),
            Action("仅验证现有资源", () => _ = RunAsync(() => StartModelOperationAsync(false))),
            Action("刷新状态", () => _ = RunAsync(LoadAsync))));
        page.Children.Add(weights);

        var accounts = Section("03  AI API 多配置", "创建或编辑服务商连接，然后将选中配置分配给 Lite、Pro 或 Vision。只有点击连接测试时才发送一条可能计费的短请求。");
        accounts.Children.Add(new TextBlock { Text = "已保存配置" });
        accounts.Children.Add(_profiles);
        accounts.Children.Add(Buttons(
            Action("新建配置", ClearEditor),
            Action("导入当前 Lite / Pro / Vision", () => _ = RunAsync(ImportExistingAsync)),
            Action("删除选中配置", () => _ = RunAsync(DeleteProfileAsync))));
        accounts.Children.Add(new TextBlock { Text = "配置名称" });
        accounts.Children.Add(_name);
        accounts.Children.Add(new TextBlock { Text = "Base URL" });
        accounts.Children.Add(_url);
        accounts.Children.Add(new TextBlock { Text = "模型 ID" });
        accounts.Children.Add(_model);
        accounts.Children.Add(Buttons(
            Action("尝试从服务端读取模型", () => _ = RunAsync(GetRemoteModelsAsync))));
        accounts.Children.Add(_remoteModels);
        _remoteModels.SelectionChanged += (_, _) =>
        {
            if (_remoteModels.SelectedItem is string id) _model.Text = id;
        };
        accounts.Children.Add(new TextBlock { Text = "API Key（安全保存，不会回显）" });
        accounts.Children.Add(_key);
        accounts.Children.Add(new TextBlock { Text = "单次最大输出 Token（Lite/Pro 生效；Kimi K3 请按额度选择）" });
        accounts.Children.Add(_tokens);
        accounts.Children.Add(Buttons(
            Action("保存配置", () => _ = RunAsync(SaveProfileAsync)),
            Action("测试连接（可能计费）", () => _ = RunAsync(TestProfileAsync))));
        accounts.Children.Add(new TextBlock { Text = "将选中配置用于" });
        accounts.Children.Add(_stage);
        accounts.Children.Add(Buttons(Action("设为此任务阶段当前配置", () => _ = RunAsync(ActivateProfileAsync))));
        page.Children.Add(accounts);

        _status.Text = "加载中...";
        page.Children.Add(new TextBlock { Text = "运行状态", FontSize = 16, FontWeight = Avalonia.Media.FontWeight.SemiBold });
        page.Children.Add(_status);
        Content = new ScrollViewer { Content = page, VerticalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Auto };
        Opened += async (_, _) => await RunAsync(LoadAsync);
    }

    private static string Value(JsonElement element, string name, string fallback = "")
    {
        if (!element.TryGetProperty(name, out var p)) return fallback;
        return p.ValueKind == JsonValueKind.String ? p.GetString() ?? fallback : fallback;
    }

    private static int Choice(string mode) => mode switch { "direct" => 1, "custom" => 2, _ => 0 };
    private static string SelectedMode(ComboBox combo) => combo.SelectedIndex switch { 1 => "direct", 2 => "custom", _ => "system" };

    private async Task RunAsync(Func<Task> work)
    {
        try { await work(); }
        catch (Exception ex) { _status.Text = "操作失败：" + ex.Message; }
    }

    private async Task LoadAsync()
    {
        var root = await _api.GetModelCenterAsync();
        var net = root.GetProperty("network");
        _downloadMode.SelectedIndex = Choice(Value(net, "download_mode"));
        _downloadProxy.Text = Value(net, "download_proxy");
        _aiMode.SelectedIndex = Choice(Value(net, "ai_mode"));
        _aiProxy.Text = Value(net, "ai_proxy");
        var modelStatus = root.GetProperty("models");
        var enabled = modelStatus.GetProperty("managed_enabled").GetBoolean();
        var verified = modelStatus.GetProperty("verified").GetBoolean();
        _useManagedModels.IsChecked = enabled;
        _modelState.Text = $"托管模型：{(enabled ? "已启用" : "未启用（保留旧版缓存机制）")} · "
            + $"{(verified ? "离线验证通过" : "尚未通过离线验证")}\n"
            + "路径：" + Value(modelStatus, "model_directory") + "\n"
            + "Docling：" + Value(modelStatus, "docling_version");
        var profiles = root.GetProperty("profiles");
        var selectedId = (_profiles.SelectedItem as ProfileEntry)?.Id;
        _profileEntries = profiles.GetProperty("profiles").EnumerateArray().Select(p => new ProfileEntry
        {
            Id = Value(p, "id"), Name = Value(p, "name"), BaseUrl = Value(p, "base_url"),
            Model = Value(p, "model"),
            MaxTokens = p.TryGetProperty("max_tokens", out var limit) ? limit.GetInt32() : 8192,
            HasApiKey = p.TryGetProperty("has_api_key", out var has) && has.GetBoolean(),
        }).ToList();
        _loading = true;
        _profiles.ItemsSource = _profileEntries;
        _profiles.SelectedItem = _profileEntries.FirstOrDefault(p => p.Id == selectedId) ?? _profileEntries.FirstOrDefault();
        _loading = false;
        ShowSelectedProfile();
        _status.Text = "设置已同步。模型下载需要联网；离线验证不发送论文内容。";
    }

    private void ShowSelectedProfile()
    {
        if (_loading) return;
        if (_profiles.SelectedItem is not ProfileEntry p) return;
        _name.Text = p.Name;
        _url.Text = p.BaseUrl;
        _model.Text = p.Model;
        _tokens.Text = p.MaxTokens.ToString(CultureInfo.InvariantCulture);
        _key.Text = "";
        _remoteModels.ItemsSource = null;
        _key.PlaceholderText = p.HasApiKey ? "已安全保存，留空不更改" : "输入 API Key";
    }

    private void ClearEditor()
    {
        _profiles.SelectedItem = null;
        _name.Text = ""; _url.Text = ""; _model.Text = ""; _key.Text = "";
        _tokens.Text = "8192";
        _status.Text = "新配置：填写名称、Base URL、模型 ID 与 API Key，然后保存。";
    }

    private async Task SaveNetworkAsync()
    {
        await _api.SaveModelCenterNetworkAsync(new
        {
            download_mode = SelectedMode(_downloadMode), download_proxy = _downloadProxy.Text ?? "",
            ai_mode = SelectedMode(_aiMode), ai_proxy = _aiProxy.Text ?? "",
        });
        _status.Text = "网络设置已保存。之后的新下载和新 AI 请求使用更新后的代理。";
    }

    private async Task SaveProfileAsync()
    {
        if (!int.TryParse(_tokens.Text, NumberStyles.None, CultureInfo.InvariantCulture, out var tokens))
            throw new InvalidOperationException("Max Tokens 必须是整数");
        var id = (_profiles.SelectedItem as ProfileEntry)?.Id;
        var result = await _api.SaveNamedModelProfileAsync(new
        {
            id, name = _name.Text?.Trim(), base_url = _url.Text?.Trim(),
            model = _model.Text?.Trim(), api_key = _key.Text?.Trim(), max_tokens = tokens,
        });
        var savedId = Value(result, "id");
        _key.Text = "";
        await LoadAsync();
        _profiles.SelectedItem = _profileEntries.FirstOrDefault(p => p.Id == savedId);
        _status.Text = "配置已安全保存；选择阶段并点击‘设为当前配置’后才用于新任务。";
    }

    private async Task ImportExistingAsync()
    {
        var result = await _api.ImportExistingNamedModelProfilesAsync();
        var count = result.GetProperty("imported").GetArrayLength();
        await LoadAsync();
        _status.Text = $"已导入 {count} 套现有配置。不会改变当前分析设置。";
    }

    private async Task DeleteProfileAsync()
    {
        if (_profiles.SelectedItem is not ProfileEntry p) return;
        await _api.DeleteNamedModelProfileAsync(p.Id);
        await LoadAsync();
        _status.Text = "配置已删除；正在使用的配置不能删除，以防影响分析任务。";
    }

    private async Task ActivateProfileAsync()
    {
        if (_profiles.SelectedItem is not ProfileEntry p) throw new InvalidOperationException("请先选择配置");
        var slot = _stage.SelectedIndex switch { 1 => "pro", 2 => "vision", _ => "lite" };
        await _api.ActivateNamedModelProfileAsync(slot, p.Id);
        _status.Text = $"已将 {p.Name} 分配给 {slot.ToUpperInvariant()}。新任务将使用此配置，已运行的任务不变。";
    }

    private async Task GetRemoteModelsAsync()
    {
        if (_profiles.SelectedItem is not ProfileEntry p) throw new InvalidOperationException("请先保存配置");
        var models = await _api.GetNamedModelCatalogAsync(p.Id);
        var values = models.GetProperty("models").EnumerateArray()
            .Select(element => element.GetString() ?? "").Where(id => id.Length > 0).ToArray();
        _remoteModels.ItemsSource = values;
        _status.Text = values.Length > 0
            ? "已读取服务商模型列表，选择一个 ID 可填入模型框。"
            : "服务商未返回模型 ID，请手动填写。";
    }

    private async Task TestProfileAsync()
    {
        if (_profiles.SelectedItem is not ProfileEntry p) throw new InvalidOperationException("请先保存并选择配置");
        _status.Text = "正在发送一次短 JSON 测试请求（可能产生服务商费用，不会发送论文）。";
        await _api.TestNamedModelProfileAsync(p.Id);
        _status.Text = "连接测试通过：模型接受了 Chat Completions JSON 请求。";
    }

    private async Task SetModelModeAsync()
    {
        await _api.SetManagedDoclingModelModeAsync(_useManagedModels.IsChecked == true);
        await LoadAsync();
        _status.Text = _useManagedModels.IsChecked == true
            ? "已切换到离线验证的托管模型；新解析任务使用这些模型。"
            : "已恢复旧版缓存解析方式；原有模型文件不会被删除。";
    }

    private async Task StartModelOperationAsync(bool download)
    {
        _status.Text = download ? "开始下载模型..." : "正在验证本地模型...";
        var accepted = await _api.StartDoclingModelOperationAsync(download, _includeFormula.IsChecked == true);
        for (var i = 0; i < 3600; i++)
        {
            if (!IsVisible) return; // The server job continues; no hidden background UI work.
            await Task.Delay(TimeSpan.FromSeconds(2));
            var job = await _api.GetJobAsync(accepted.JobId);
            _status.Text = job.Messages.LastOrDefault() ?? "模型任务正在运行...";
            if (job.Status == "done")
            {
                await LoadAsync();
                _status.Text = "模型下载与离线 PDF/OCR 验证成功。";
                return;
            }
            if (job.Status == "error")
                throw new InvalidOperationException(job.Error ?? "模型下载/验证失败");
        }
        _status.Text = "模型任务运行时间较长，可关闭窗口后稍后重新查看状态。";
    }
}
