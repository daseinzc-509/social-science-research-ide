using Avalonia.Interactivity;

namespace SRA.Desktop.Views;

public partial class MainWindow
{
    private async void RegionReview_Click(object? sender, RoutedEventArgs e)
    {
        if (ViewModel?.SelectedPaper is not { } paper) return;
        var window = new RegionReviewWindow(ViewModel.Api, paper.Id, paper.Title);
        await window.ShowDialog(this);
    }
}
