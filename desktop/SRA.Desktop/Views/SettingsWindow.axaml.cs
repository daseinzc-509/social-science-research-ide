using Avalonia.Controls;
using Avalonia.Interactivity;
using SRA.Desktop.ViewModels;

namespace SRA.Desktop.Views;

public partial class SettingsWindow : Window
{
    public SettingsWindow()
    {
        InitializeComponent();
    }

    private void Cancel_Click(object? sender, RoutedEventArgs e) => Close(false);

    private async void Save_Click(object? sender, RoutedEventArgs e)
    {
        if (DataContext is SettingsWindowViewModel viewModel && await viewModel.SaveAsync())
        {
            Close(true);
        }
    }
}
