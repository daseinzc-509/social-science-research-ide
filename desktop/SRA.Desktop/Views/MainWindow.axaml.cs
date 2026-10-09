using System.Text;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;
using SRA.Desktop.Models;
using SRA.Desktop.ViewModels;

namespace SRA.Desktop.Views;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        NavigateTo("papers");
    }

    private MainWindowViewModel? ViewModel => DataContext as MainWindowViewModel;


    private void NavigateTo(string page)
    {
        PapersPage.IsVisible = page == "papers";
        BooksPage.IsVisible = page == "books";
        ProjectsPage.IsVisible = page == "projects";
        TasksPage.IsVisible = page == "tasks";
        HealthPage.IsVisible = page == "health";
        SettingsPage.IsVisible = page == "settings";

        SetSelected(PapersNav, page == "papers");
        SetSelected(TasksNav, page == "tasks");
        SetSelected(HealthNav, page == "health");
        SetSelected(SettingsNav, page == "settings");

        bool showLibrary = page == "papers";
        SidebarPane.IsVisible = showLibrary;
        WorkspaceBody.ColumnDefinitions[0].Width = new GridLength(showLibrary ? 280 : 0);
    }

    private static void SetSelected(Button button, bool selected)
    {
        button.Classes.Remove("selected");
        if (selected) button.Classes.Add("selected");
    }

    private void PapersNav_Click(object? sender, RoutedEventArgs e) => NavigateTo("papers");

    private async void TasksNav_Click(object? sender, RoutedEventArgs e)
    {
        NavigateTo("tasks");
        if (ViewModel is not null) await ViewModel.LoadDashboardAsync();
    }

    private async void HealthNav_Click(object? sender, RoutedEventArgs e)
    {
        NavigateTo("health");
        if (ViewModel is not null) await ViewModel.RunDoctorAsync(false);
    }

    private async void SettingsNav_Click(object? sender, RoutedEventArgs e)
    {
        NavigateTo("settings");
        if (ViewModel is not null) await ViewModel.LoadSettingsAsync();
    }

    // Content-first mode. The preference is saved by the view model.
    private void ToggleInspector_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null) ViewModel.ShowInspector = !ViewModel.ShowInspector;
    }

    private void MinimizeWindow_Click(object? sender, RoutedEventArgs e)
        => WindowState = WindowState.Minimized;

    private void ToggleMaximizeWindow_Click(object? sender, RoutedEventArgs e)
        => WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;

    private void CloseWindow_Click(object? sender, RoutedEventArgs e) => Close();

    private async void RefreshDashboard_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null) await ViewModel.LoadDashboardAsync();
    }

    private async void BatchAnalyzePage_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null) await ViewModel.RunBatchAnalysisAsync();
    }

    private async void BatchReferencesPage_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null) await ViewModel.RunBatchReferencesAsync();
    }

    private async void QuickDoctorPage_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null) await ViewModel.RunDoctorAsync(false);
    }

    private async void DeepDoctorPage_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null) await ViewModel.RunDoctorAsync(true);
    }

    private async void PreviewPrunePage_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is not null) await ViewModel.PreviewPruneCacheAsync();
    }

    private async void ApplyPrunePage_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        var confirm = new ConfirmWindow(
            "清理旧模型缓存",
            "只删除旧 prompt 版本的模型缓存，不删除 PDF、Paper Card、source blocks 或 notes。继续？",
            "清理缓存"
        );
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.ApplyPruneCacheAsync();
    }

    private async void SaveEmbeddedSettings_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        await ViewModel.Settings.SaveAsync();
    }

    private async void ImportPdf_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || !StorageProvider.CanOpen) return;
        var files = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
        {
            Title = "导入论文 PDF",
            AllowMultiple = false,
            FileTypeFilter = [FilePickerFileTypes.Pdf],
        });
        if (files.Count == 0) return;
        await using var stream = await files[0].OpenReadAsync();
        await ViewModel.ImportPdfAsync(stream, files[0].Name);
    }

    private async void BatchImport_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || !StorageProvider.CanOpen) return;
        var files = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
        {
            Title = "批量选择论文 PDF",
            AllowMultiple = true,
            FileTypeFilter = [FilePickerFileTypes.Pdf],
        });
        if (files.Count == 0) return;

        var uploads = new List<(Stream Stream, string FileName)>();
        try
        {
            foreach (var file in files.Take(200))
            {
                uploads.Add((await file.OpenReadAsync(), file.Name));
            }
            await ViewModel.ImportBatchAsync(uploads, $"批量导入 {uploads.Count} 个 PDF");
        }
        finally
        {
            foreach (var (stream, _) in uploads) stream.Dispose();
        }
    }

    private async void FolderImport_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || !StorageProvider.CanPickFolder) return;
        var folders = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions
        {
            Title = "选择包含 PDF 的文件夹",
            AllowMultiple = false,
        });
        if (folders.Count == 0) return;
        var localPath = folders[0].TryGetLocalPath();
        if (string.IsNullOrWhiteSpace(localPath))
        {
            ViewModel.StatusText = "当前平台无法获得该文件夹的本地路径。";
            return;
        }

        var paths = Directory.EnumerateFiles(localPath, "*.pdf", SearchOption.AllDirectories).Take(200).ToList();
        if (paths.Count == 0)
        {
            ViewModel.StatusText = "所选文件夹中没有检测到 PDF。";
            return;
        }

        var uploads = new List<(Stream Stream, string FileName)>();
        try
        {
            foreach (var path in paths)
            {
                uploads.Add((File.OpenRead(path), Path.GetFileName(path)));
            }
            await ViewModel.ImportBatchAsync(uploads, $"从文件夹导入 {uploads.Count} 个 PDF");
        }
        finally
        {
            foreach (var (stream, _) in uploads) stream.Dispose();
        }
    }

    private async void Analyze_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || !ViewModel.HasSelection) return;
        var dialog = new AnalyzeWindow(ViewModel.ResearchContext, ViewModel.ExcludeAfterText);
        var result = await dialog.ShowDialog<AnalysisDialogResult?>(this);
        if (result is null) return;
        await ViewModel.AnalyzeAsync(new AnalysisRequest
        {
            ResearchContext = result.ResearchContext,
            ExcludeAfterText = result.ExcludeAfterText,
            Force = result.Force,
        });
    }

    private async void Reparse_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null || ViewModel.SelectedPaper is null) return;
        var confirm = new ConfirmWindow(
            "重建解析",
            "重新调用 PDF 解析器并替换 source blocks，同时清除旧 Paper Card 与模型缓存。原 PDF 和手动元数据修订会保留。继续？",
            "重建解析"
        );
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.ReparseAsync();
    }

    private async void EditMetadata_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is null || ViewModel.SelectedDetail is null) return;
        var window = new MetadataWindow(ViewModel.Api, ViewModel.SelectedPaper.Id, ViewModel.SelectedDetail);
        var changed = await window.ShowDialog<bool>(this);
        if (changed)
        {
            await ViewModel.ReloadSelectedAsync();
            ViewModel.StatusText = "元数据修订已保存。重新分析不会覆盖。";
        }
    }

    private async void ExtractReferences_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        await ViewModel.ExtractReferencesAsync();
    }

    private async void OpenPdf_Click(object? sender, RoutedEventArgs e)
    {
        var uri = ViewModel?.SelectedPdfUri;
        if (uri is null) return;
        await Launcher.LaunchUriAsync(uri);
    }

    private async void DeletePaper_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is null) return;
        var name = ViewModel.SelectedPaper.Title;
        var confirm = new ConfirmWindow(
            "删除论文",
            $"永久删除“{name}”？\n\n将删除数据库记录、source blocks、Paper Card、模型缓存、用户笔记，以及 SRA data/papers 中保存的 PDF。此操作不可撤销。",
            "永久删除"
        );
        if (!await confirm.ShowDialog<bool>(this)) return;
        await ViewModel.DeleteSelectedAsync();
    }

    private async void ExportCard_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is null) return;
        var content = await ViewModel.Api.GetCardJsonAsync(ViewModel.SelectedPaper.Id);
        await SaveTextAsync(SafeName(ViewModel.SelectedPaper.Title) + "-paper-card.json", content);
    }

    private async void ExportReading_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is null) return;
        var content = await ViewModel.Api.GetReadingReportAsync(ViewModel.SelectedPaper.Id);
        await SaveTextAsync(SafeName(ViewModel.SelectedPaper.Title) + "-deep-reading.md", content);
    }

    private async void ExportText_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is null) return;
        var content = await ViewModel.Api.GetParsedTextAsync(ViewModel.SelectedPaper.Id);
        await SaveTextAsync(SafeName(ViewModel.SelectedPaper.Title) + "-parsed.txt", content);
    }

    private async void ExportReferencesJson_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is null) return;
        var content = await ViewModel.Api.GetReferencesJsonAsync(ViewModel.SelectedPaper.Id);
        await SaveTextAsync(SafeName(ViewModel.SelectedPaper.Title) + "-references.json", content);
    }

    private async void ExportBibtex_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is null) return;
        var content = await ViewModel.Api.GetReferencesBibtexAsync(ViewModel.SelectedPaper.Id);
        await SaveTextAsync(SafeName(ViewModel.SelectedPaper.Title) + "-references.bib", content);
    }

    private async Task SaveTextAsync(string suggestedName, string content)
    {
        if (!StorageProvider.CanSave) return;
        var file = await StorageProvider.SaveFilePickerAsync(new FilePickerSaveOptions
        {
            Title = "导出文件",
            SuggestedFileName = suggestedName,
        });
        if (file is null) return;
        await using var stream = await file.OpenWriteAsync();
        stream.SetLength(0);
        await using var writer = new StreamWriter(stream, new UTF8Encoding(false));
        await writer.WriteAsync(content);
        if (ViewModel is not null) ViewModel.StatusText = $"已导出：{file.Name}";
    }

    private async void ModelSettings_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        var settingsViewModel = new SettingsWindowViewModel(ViewModel.Api);
        await settingsViewModel.LoadAsync();
        var window = new SettingsWindow { DataContext = settingsViewModel };
        await window.ShowDialog(this);
    }

    private async void Dashboard_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        var window = new DashboardWindow(ViewModel.Api);
        await window.ShowDialog(this);
        await ViewModel.RefreshAsync();
    }

    private async void Maintenance_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel is null) return;
        var window = new MaintenanceWindow(ViewModel.Api);
        await window.ShowDialog(this);
    }

    private static string SafeName(string value)
    {
        var invalid = Path.GetInvalidFileNameChars();
        var cleaned = new string(value.Select(ch => invalid.Contains(ch) ? '_' : ch).ToArray()).Trim();
        return string.IsNullOrWhiteSpace(cleaned) ? "paper" : cleaned[..Math.Min(cleaned.Length, 80)];
    }
}
