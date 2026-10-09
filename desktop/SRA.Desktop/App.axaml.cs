using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Avalonia.Styling;
using SRA.Desktop.Services;
using SRA.Desktop.ViewModels;
using SRA.Desktop.Views;

namespace SRA.Desktop;

public partial class App : Application
{
    public override void Initialize()
    {
        AvaloniaXamlLoader.Load(this);
    }

    public static void ApplyThemeMode(string? mode)
    {
        if (Current is null) return;
        Current.RequestedThemeVariant = mode switch
        {
            "浅色" => ThemeVariant.Light,
            "深色" => ThemeVariant.Dark,
            _ => ThemeVariant.Default,
        };
    }

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            var preferences = new DesktopPreferencesService();
            var snapshot = preferences.Load();
            ApplyThemeMode(snapshot.ThemeMode);

            var api = new SraApiClient("http://127.0.0.1:8766/");
            desktop.MainWindow = new MainWindow
            {
                DataContext = new MainWindowViewModel(api, preferences, snapshot),
            };
        }

        base.OnFrameworkInitializationCompleted();
    }
}
