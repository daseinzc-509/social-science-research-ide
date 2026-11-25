using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using SRA.Desktop.Services;

namespace SRA.Desktop.Views;

/// <summary>
/// 统一模型配置中心：管理 Lite / Pro / Vision 三阶段连接、网络代理以及 Docling 离线模型与增强开关。
/// 所有配置直接写入 SRA 后端统一加密存储，不生成桌面端独立凭据缓存。
/// </summary>
public sealed class ModelCenterWindow : Window
{
    private readonly SraApiClient _api;
    private readonly ComboBox _downloadMode = Picker("系统代理", "直连", "自定义 HTTP 代理");
    private readonly ComboBox _aiMode = Picker("系统代理", "直连", "自定义 HTTP 代理");
    private readonly TextBox _downloadProxy = Edit("http://127.0.0.1:7890");
    private readonly TextBox _aiProxy = Edit("http://127.0.0.1:7890");
    private readonly TextBlock _modelState = new()
    {
        TextWrapping = TextWrapping.Wrap,
        FontSize = 12.5,
        LineHeight = 20,
    };
    private readonly CheckBox _includeFormula = new() { Content = "下载时包含公式模型权重" };
    private readonly CheckBox _useManagedModels = new() { Content = "使用已离线验证的托管 Docling 模型" };
    private readonly CheckBox _formulaEnabled = new() { Content = "启用本地公式识别（不调用付费 AI）" };
    private readonly CheckBox _visionEnabled = new() { Content = "允许按需分析单张图片（调用 Vision API 可能计费）" };
    private readonly TextBlock _effectiveConnections = new()
    {
        TextWrapping = TextWrapping.Wrap,
        FontSize = 12.5,
        LineHeight = 22,
    };
    private readonly ComboBox _profiles = new()
    {
        MinWidth = 260,
        HorizontalAlignment = HorizontalAlignment.Stretch,
    };
    private readonly ComboBox _stage = Picker("Lite · 事实索引", "Pro · 深度审读", "Vision · 图片分析");
    private readonly ComboBox _remoteModels = new()
    {
        MinWidth = 260,
        HorizontalAlignment = HorizontalAlignment.Stretch,
    };
    private readonly TextBox _name = Edit("如：火山方舟 GLM");
    private readonly TextBox _url = Edit("如：https://.../v1 或服务商指定 Base URL");
    private readonly TextBox _model = Edit("如：kimi-k3");
    private readonly TextBox _key = Edit("API Key（更新时留空表示保留原密钥）");
    private readonly TextBox _tokens = Edit("8192");
    private readonly TextBlock _status = new()
    {
        TextWrapping = TextWrapping.Wrap,
        FontSize = 12,
        LineHeight = 19,
    };
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
        MinWidth = 200,
    };

    private static Button Action(string text, Action callback, string styleClass = "secondary", string? extraClass = null)
    {
        var button = new Button { Content = text };
        button.Classes.Add(styleClass);
        if (!string.IsNullOrEmpty(extraClass)) button.Classes.Add(extraClass);
        button.Click += (_, _) => callback();
        return button;
    }

    private static TextBlock FieldLabel(string text) => new()
    {
        Text = text,
        FontSize = 12,
        FontWeight = FontWeight.SemiBold,
        Classes = { "muted" },
    };

    private static TextBlock Hint(string text) => new()
    {
        Text = text,
        TextWrapping = TextWrapping.Wrap,
        FontSize = 11.5,
        LineHeight = 18,
        Classes = { "muted" },
    };

    private static Border Card(string styleClass, string title, string? description, params Control[] children)
    {
        var stack = new StackPanel { Spacing = 11 };
        stack.Children.Add(new TextBlock
        {
            Text = title,
            FontSize = 15.5,
            FontWeight = FontWeight.SemiBold,
        });
        if (!string.IsNullOrWhiteSpace(description))
        {
            stack.Children.Add(Hint(description));
        }
        foreach (var child in children) stack.Children.Add(child);
        var border = new Border
        {
            Padding = new Thickness(20, 18),
            Child = stack,
        };
        border.Classes.Add(styleClass);
        return border;
    }

    private static StackPanel FieldGroup(string label, Control input) => new()
    {
        Spacing = 5,
        Children = { FieldLabel(label), input },
    };

    private static WrapPanel Buttons(params Control[] children)
    {
        var panel = new WrapPanel { ItemSpacing = 9, LineSpacing = 8 };
        foreach (var child in children) panel.Children.Add(child);
        return panel;
    }

    private static ScrollViewer TabPage(params Control[] cards)
    {
        var content = new StackPanel
        {
            Spacing = 14,
            Margin = new Thickness(4, 14, 12, 22),
        };
        foreach (var card in cards) content.Children.Add(card);
        return new ScrollViewer
        {
            Content = content,
            VerticalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Auto,
        };
    }

    public ModelCenterWindow(SraApiClient api)
    {
        _api = api;
        Title = "SRA · 模型配置中心";
        Width = 940;
        Height = 820;
        MinWidth = 720;
        MinHeight = 580;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        _key.PasswordChar = '●';
        _profiles.SelectionChanged += (_, _) => ShowSelectedProfile();
        _remoteModels.SelectionChanged += (_, _) =>
        {
            if (_remoteModels.SelectedItem is string modelId) _model.Text = modelId;
        };

        // Tab 1: AI 服务商（三张清晰 MD3 卡片）
        var effectiveCard = Card(
            "tonal-card",
            "当前实际生效的模型",
            "展示分析器当前实际使用的 Lite、Pro、Vision 连接，而非仅显示配置名称。旧设置仍然有效，可一键导入。",
            _effectiveConnections);

        var nameTokenRow = new Grid
        {
            ColumnDefinitions = new ColumnDefinitions("2*,*"),
            ColumnSpacing = 12,
        };
        var nameField = FieldGroup("配置名称", _name);
        var tokensField = FieldGroup("单次最大输出 Token（Lite/Pro）", _tokens);
        Grid.SetColumn(nameField, 0);
        Grid.SetColumn(tokensField, 1);
        nameTokenRow.Children.Add(nameField);
        nameTokenRow.Children.Add(tokensField);

        var profileEditorCard = Card(
            "card",
            "API 服务商与命名配置",
            "编辑参数后可先「仅保存」或直接应用到指定阶段；API Key 加密存储，不会明文回显。",
            FieldGroup("已保存的 API 服务商 / 模型配置", _profiles),
            Buttons(
                Action("新建配置", ClearEditor, "secondary", "compact"),
                Action("导入原有 Lite / Pro / Vision", () => _ = RunAsync(ImportExistingAsync), "tonal", "compact"),
                Action("删除选中", () => _ = RunAsync(DeleteProfileAsync), "danger", "compact")),
            nameTokenRow,
            FieldGroup("Base URL", _url),
            FieldGroup("Model ID（以服务商实际 ID 为准）", _model),
            Buttons(Action("从服务商读取模型列表", () => _ = RunAsync(GetRemoteModelsAsync), "tonal", "compact")),
            _remoteModels,
            FieldGroup("API Key（加密保存，不会回显）", _key),
            Buttons(
                Action("仅保存到配置列表", () => _ = RunAsync(SaveProfileAsync), "secondary"),
                Action("测试已保存配置（可能计费）", () => _ = RunAsync(TestProfileAsync), "tonal")));

        var activationCard = Card(
            "soft-card",
            "应用到分析阶段",
            "“仅保存”不影响分析；“保存并应用”立即更新下一次分析的真实连接。已经运行的分析不切换。Vision 仍需在图片操作时明确确认外发。",
            FieldGroup("为哪个分析阶段应用此配置？", _stage),
            Buttons(Action("保存并应用到选定阶段", () => _ = RunAsync(SaveAndActivateAsync), "primary")));

        // Tab 2: 网络代理
        var downloadProxyCard = Card(
            "card",
            "Docling / Hugging Face 下载代理",
            "仅用于本地解析模型权重下载。自定义代理填写 HTTP/Mixed 端口，不要填 SOCKS-only 端口。",
            FieldGroup("下载代理模式", _downloadMode),
            FieldGroup("自定义代理地址", _downloadProxy));

        var aiProxyCard = Card(
            "card",
            "AI API 请求代理",
            "与模型下载代理完全独立；使用国内 API 服务商时可单独设为直连。",
            FieldGroup("AI API 代理模式", _aiMode),
            FieldGroup("自定义代理地址", _aiProxy),
            Buttons(Action("保存网络设置", () => _ = RunAsync(SaveNetworkAsync), "primary")));

        // Tab 3: Docling / 增强
        var doclingStateBox = new Border
        {
            Padding = new Thickness(14, 12),
            Child = _modelState,
        };
        doclingStateBox.Classes.Add("tonal-card");

        var doclingCard = Card(
            "card",
            "Docling 离线解析权重",
            "权重下载结束后会在独立离线进程中运行一次 PDF/OCR 转换，验证通过才标记为可用。",
            doclingStateBox,
            _includeFormula,
            _useManagedModels,
            Buttons(
                Action("下载并验证", () => _ = RunAsync(() => StartModelOperationAsync(true)), "primary"),
                Action("验证已有模型", () => _ = RunAsync(() => StartModelOperationAsync(false)), "tonal"),
                Action("保存本地模型使用模式", () => _ = RunAsync(SetModelModeAsync), "secondary"),
                Action("刷新状态", () => _ = RunAsync(LoadAsync), "ghost")));

        var enhancementsCard = Card(
            "card",
            "本地公式与图片分析开关",
            "公式开关在新解析任务中生效；Vision 模型和密钥统一在“AI 服务商”页指定，这里只控制是否允许按需使用。",
            _formulaEnabled,
            Hint("如果需要额外公式权重，请先在上方勾选下载选项并完成离线验证。"),
            _visionEnabled,
            Hint("开启后不会自动外发图片，也不会覆盖已经选择的 Vision 配置；每张图片分析均需研究者明确确认。"),
            Buttons(Action("保存公式 / 图片开关", () => _ = RunAsync(SaveEnhancementsAsync), "primary")));

        var header = new StackPanel { Spacing = 5, Margin = new Thickness(0, 0, 0, 14) };
        header.Children.Add(new TextBlock
        {
            Text = "MODEL CENTER",
            Classes = { "eyebrow" },
        });
        header.Children.Add(new TextBlock
        {
            Text = "统一模型配置中心",
            Classes = { "h1" },
        });
        header.Children.Add(Hint("一个入口管理三类 AI 任务、网络代理与 Docling；配置同步到 SRA 原有加密存储，不生成第二份生效设置。"));

        var tabs = new TabControl
        {
            Classes = { "paper-tabs" },
            ItemsSource = new[]
            {
                new TabItem { Header = "AI 服务商", Content = TabPage(effectiveCard, profileEditorCard, activationCard) },
                new TabItem { Header = "网络代理", Content = TabPage(downloadProxyCard, aiProxyCard) },
                new TabItem { Header = "Docling / 增强", Content = TabPage(doclingCard, enhancementsCard) },
            },
            SelectedIndex = 0,
        };

        var tabsSurface = new Border
        {
            Padding = new Thickness(12, 4, 8, 8),
            ClipToBounds = true,
            Child = tabs,
        };
        tabsSurface.Classes.Add("workspace-surface");

        _status.Text = "正在加载配置…";
        var footer = new Border
        {
            Margin = new Thickness(0, 12, 0, 0),
            Padding = new Thickness(16, 11),
            Child = new StackPanel
            {
                Spacing = 3,
                Children =
                {
                    new TextBlock { Text = "运行状态", FontSize = 11.5, FontWeight = FontWeight.SemiBold, Classes = { "muted" } },
                    _status,
                },
            },
        };
        footer.Classes.Add("soft-card");

        var shell = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto"), Margin = new Thickness(24, 20, 24, 20) };
        Grid.SetRow(header, 0);
        Grid.SetRow(tabsSurface, 1);
        Grid.SetRow(footer, 2);
        shell.Children.Add(header);
        shell.Children.Add(tabsSurface);
        shell.Children.Add(footer);
        Content = shell;
        Opened += async (_, _) => await RunAsync(LoadAsync);
    }

    private static string Value(JsonElement element, string name, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(name, out var property))
            return fallback;
        return property.ValueKind == JsonValueKind.String ? property.GetString() ?? fallback : fallback;
    }

    private static int Choice(string mode) => mode switch { "direct" => 1, "custom" => 2, _ => 0 };
    private static string SelectedMode(ComboBox combo) => combo.SelectedIndex switch { 1 => "direct", 2 => "custom", _ => "system" };
    private string SelectedStage() => _stage.SelectedIndex switch { 1 => "pro", 2 => "vision", _ => "lite" };

    private async Task RunAsync(Func<Task> work)
    {
        try { await work(); }
        catch (Exception ex) { _status.Text = "操作失败：" + ex.Message; }
    }

    private static string StageDescription(JsonElement stages, string slot, string label)
    {
        if (!stages.TryGetProperty(slot, out var current)) return label + "：状态未知";
        var model = Value(current, "model");
        var origin = Value(current, "origin");
        var source = origin switch
        {
            "profile" => "命名配置 · " + Value(current, "profile_name"),
            "legacy" => "现有设置（可导入）",
            _ => "尚未配置",
        };
        var enabled = current.TryGetProperty("enabled", out var enabledFlag) && enabledFlag.GetBoolean();
        var extra = slot == "vision" && !enabled ? " · 使用开关关闭" : "";
        var url = Value(current, "base_url");
        var host = Uri.TryCreate(url, UriKind.Absolute, out var parsed) ? " · " + parsed.Host : "";
        return $"{label}：{(string.IsNullOrEmpty(model) ? "未选择" : model)}  |  {source}{host}{extra}";
    }

    private async Task LoadAsync()
    {
        var root = await _api.GetModelCenterAsync();
        var network = root.GetProperty("network");
        _downloadMode.SelectedIndex = Choice(Value(network, "download_mode"));
        _downloadProxy.Text = Value(network, "download_proxy");
        _aiMode.SelectedIndex = Choice(Value(network, "ai_mode"));
        _aiProxy.Text = Value(network, "ai_proxy");

        var models = root.GetProperty("models");
        var enabled = models.GetProperty("managed_enabled").GetBoolean();
        var verified = models.GetProperty("verified").GetBoolean();
        _useManagedModels.IsChecked = enabled;
        _modelState.Text = $"托管模式：{(enabled ? "已启用" : "关闭（兼容旧模型缓存）")} · "
            + $"{(verified ? "已通过离线验证" : "尚未通过离线验证")}\n"
            + "模型路径：" + Value(models, "model_directory") + "\n"
            + "Docling 版本：" + Value(models, "docling_version");

        if (root.TryGetProperty("enhancements", out var prefs))
        {
            _formulaEnabled.IsChecked = prefs.GetProperty("formula_enabled").GetBoolean();
            _visionEnabled.IsChecked = prefs.GetProperty("vision_enabled").GetBoolean();
            _includeFormula.IsChecked = _formulaEnabled.IsChecked;
        }
        if (root.TryGetProperty("effective", out var effective))
        {
            _effectiveConnections.Text = string.Join("\n", new[]
            {
                StageDescription(effective, "lite", "Lite"),
                StageDescription(effective, "pro", "Pro"),
                StageDescription(effective, "vision", "Vision"),
            });
        }
        else
        {
            _effectiveConnections.Text = "当前模型信息暂不可用，请检查 Python 后端版本。";
        }

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
        if (_profiles.SelectedItem is ProfileEntry) ShowSelectedProfile();
        else ClearEditor();
        _status.Text = "配置已同步。仅保存配置不会切换分析模型；请使用“保存并应用到选定阶段”。";
    }

    private void ShowSelectedProfile()
    {
        if (_loading || _profiles.SelectedItem is not ProfileEntry p) return;
        _name.Text = p.Name;
        _url.Text = p.BaseUrl;
        _model.Text = p.Model;
        _tokens.Text = p.MaxTokens.ToString(CultureInfo.InvariantCulture);
        _key.Text = "";
        _remoteModels.ItemsSource = null;
        _key.PlaceholderText = p.HasApiKey ? "密钥已安全保存，留空不更改" : "请输入 API Key";
    }

    private void ClearEditor()
    {
        _profiles.SelectedItem = null;
        _name.Text = "";
        _url.Text = "";
        _model.Text = "";
        _key.Text = "";
        _tokens.Text = "8192";
        _remoteModels.ItemsSource = null;
        _key.PlaceholderText = "请输入 API Key";
        _status.Text = "新建配置：填写名称、Base URL、模型 ID 和 API Key。";
    }

    private void SelectProfile(string id)
    {
        _profiles.SelectedItem = _profileEntries.FirstOrDefault(p => p.Id == id);
    }

    private async Task<string> SaveProfileCoreAsync()
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
        if (string.IsNullOrWhiteSpace(savedId)) throw new InvalidOperationException("服务端未返回配置 ID");
        _key.Text = "";
        await LoadAsync();
        SelectProfile(savedId);
        return savedId;
    }

    private async Task SaveProfileAsync()
    {
        await SaveProfileCoreAsync();
        _status.Text = "配置已保存。如果这套配置原本在使用，修改已同步到下一次分析；否则需点击“保存并应用”。";
    }

    private async Task SaveAndActivateAsync()
    {
        var id = await SaveProfileCoreAsync();
        var slot = SelectedStage();
        await _api.ActivateNamedModelProfileAsync(slot, id);
        await LoadAsync();
        SelectProfile(id);
        _status.Text = slot == "vision"
            ? "已保存并应用 Vision 配置，同时启用按需图片分析；实际发送图片仍需要研究者明确确认。"
            : $"已保存并应用到 {slot.ToUpperInvariant()}。新分析任务使用更新后的连接，已开始的任务不会切换。";
    }

    private async Task SaveNetworkAsync()
    {
        await _api.SaveModelCenterNetworkAsync(new
        {
            download_mode = SelectedMode(_downloadMode), download_proxy = _downloadProxy.Text ?? "",
            ai_mode = SelectedMode(_aiMode), ai_proxy = _aiProxy.Text ?? "",
        });
        _status.Text = "网络设置已保存。新下载与新 AI 请求使用相应的代理配置。";
    }

    private async Task SaveEnhancementsAsync()
    {
        await _api.SaveModelCenterEnhancementsAsync(_formulaEnabled.IsChecked == true, _visionEnabled.IsChecked == true);
        await LoadAsync();
        _status.Text = "公式 / 图片开关已保存；不会覆盖 Vision 的 API Key、Base URL 或模型 ID。";
    }

    private async Task ImportExistingAsync()
    {
        var result = await _api.ImportExistingNamedModelProfilesAsync();
        var count = result.GetProperty("imported").GetArrayLength();
        await LoadAsync();
        _status.Text = count > 0
            ? $"已从原有加密配置导入 {count} 套连接。原来生效的模型保持不变，可以逐个分配 Lite / Pro / Vision。"
            : "现有连接均已存在于配置列表中，未重复导入。";
    }

    private async Task DeleteProfileAsync()
    {
        if (_profiles.SelectedItem is not ProfileEntry p) return;
        await _api.DeleteNamedModelProfileAsync(p.Id);
        await LoadAsync();
        _status.Text = "已删除未使用的配置。当前生效的配置不能删除；先切换至其他配置。";
    }

    private async Task GetRemoteModelsAsync()
    {
        if (_profiles.SelectedItem is not ProfileEntry p) throw new InvalidOperationException("请先保存配置");
        var models = await _api.GetNamedModelCatalogAsync(p.Id);
        var values = models.GetProperty("models").EnumerateArray()
            .Select(element => element.GetString() ?? "").Where(id => id.Length > 0).ToArray();
        _remoteModels.ItemsSource = values;
        _status.Text = values.Length > 0
            ? "已读取服务商模型列表，选择一个 Model ID 再保存。"
            : "服务商没有返回模型列表，请手动填写 Model ID。";
    }

    private async Task TestProfileAsync()
    {
        if (_profiles.SelectedItem is not ProfileEntry p) throw new InvalidOperationException("请先保存并选择配置");
        _status.Text = "正在发送一次短 JSON 测试请求（可能计费，不会发送论文内容）…";
        await _api.TestNamedModelProfileAsync(p.Id);
        _status.Text = "连接测试通过：模型接受了 Chat Completions JSON 请求。";
    }

    private async Task SetModelModeAsync()
    {
        await _api.SetManagedDoclingModelModeAsync(_useManagedModels.IsChecked == true);
        await LoadAsync();
        _status.Text = _useManagedModels.IsChecked == true
            ? "已开启已验证的托管模型；新解析任务使用该模型。"
            : "已恢复旧版缓存机制，不删除现有权重。";
    }

    private async Task StartModelOperationAsync(bool download)
    {
        _status.Text = download ? "正在请求下载并离线验证…" : "正在验证已有模型…";
        var accepted = await _api.StartDoclingModelOperationAsync(download, _includeFormula.IsChecked == true);
        for (var i = 0; i < 3600; i++)
        {
            if (!IsVisible) return;
            await Task.Delay(TimeSpan.FromSeconds(2));
            var job = await _api.GetJobAsync(accepted.JobId);
            _status.Text = job.Messages.LastOrDefault() ?? "模型任务正在运行…";
            if (job.Status == "done")
            {
                await LoadAsync();
                _status.Text = "模型下载与离线 PDF/OCR 验证成功。";
                return;
            }
            if (job.Status == "error")
                throw new InvalidOperationException(job.Error ?? "模型下载或离线验证失败");
        }
        _status.Text = "模型任务运行时间较长，可关闭窗口后重新查看状态。";
    }
}
