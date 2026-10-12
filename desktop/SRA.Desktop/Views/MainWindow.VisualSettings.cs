using Avalonia.Controls;
using Avalonia.Interactivity;
using SRA.Desktop.Models;
using SRA.Desktop.ViewModels;

namespace SRA.Desktop.Views;

/// <summary>Code-behind for explicit vision analysis and preferences.</summary>
public partial class MainWindow
{
    private async void SaveVisualSettings_Click(object? sender, RoutedEventArgs e)
    {
        if (DataContext is MainWindowViewModel viewModel)
            await viewModel.Settings.SaveVisualAsync();
    }

    private async void AnalyzeFigure_Click(object? sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not VisualSourceItem figure ||
            DataContext is not MainWindowViewModel viewModel)
            return;
        // User explicitly clicked a button that says it sends cropped image
        // data to a billable remote model. No auto-run during document import.
        button.IsEnabled = false;
        try
        {
            await viewModel.AnalyzeFigureAsync(figure);
        }
        finally
        {
            button.IsEnabled = true;
        }
    }
}
