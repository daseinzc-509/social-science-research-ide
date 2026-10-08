using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
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

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            var api = new SraApiClient("http://127.0.0.1:8766/");
            desktop.MainWindow = new MainWindow
            {
                DataContext = new MainWindowViewModel(api),
            };
        }

        base.OnFrameworkInitializationCompleted();
    }
}
