using Avalonia.Controls;
using Avalonia.Interactivity;
using SRA.Desktop.Models;

namespace SRA.Desktop.Views;

public partial class AnalyzeWindow : Window
{
    public AnalyzeWindow()
    {
        InitializeComponent();
    }

    public AnalyzeWindow(string researchContext, string excludeAfterText) : this()
    {
        ResearchContextBox.Text = researchContext;
        ExcludeAfterBox.Text = excludeAfterText;
    }

    private void Run_Click(object? sender, RoutedEventArgs e)
    {
        Close(new AnalysisDialogResult(
            string.IsNullOrWhiteSpace(ResearchContextBox.Text) ? null : ResearchContextBox.Text.Trim(),
            string.IsNullOrWhiteSpace(ExcludeAfterBox.Text) ? null : ExcludeAfterBox.Text.Trim(),
            ForceCheckBox.IsChecked == true
        ));
    }

    private void Cancel_Click(object? sender, RoutedEventArgs e) => Close(null);
}
