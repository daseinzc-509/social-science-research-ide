using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using Avalonia.Media;
using SRA.Desktop.Models;

namespace SRA.Desktop.Controls;

/// <summary>
/// Native table renderer using Docling cell coordinates. A merged cell is
/// one Border with matching Grid.RowSpan / Grid.ColumnSpan; no repeated OCR
/// headers are guessed or combined. Source raw text is displayed separately.
/// </summary>
public sealed class StructuredTableView : UserControl
{
    public static readonly StyledProperty<TableItem?> TableProperty =
        AvaloniaProperty.Register<StructuredTableView, TableItem?>(nameof(Table));

    private const int CellWidth = 168;
    private const int MaxVisualCells = 8000;
    private readonly ScrollViewer _scrollViewer = new()
    {
        HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
        VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
    };

    public TableItem? Table
    {
        get => GetValue(TableProperty);
        set => SetValue(TableProperty, value);
    }

    public StructuredTableView()
    {
        Content = _scrollViewer;
    }

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == TableProperty)
            Rebuild();
    }

    private void Rebuild()
    {
        var table = Table;
        if (table is null || !table.HasStructuredRows)
        {
            _scrollViewer.Content = null;
            return;
        }

        // Avoid freezing the UI when importing a very large appendix. The
        // complete unmodified source remains in the raw-text Expander.
        if ((long)table.RowCount * table.ColumnCount > MaxVisualCells)
        {
            _scrollViewer.Content = new TextBlock
            {
                Text = $"表格包含 {table.RowCount}×{table.ColumnCount} 个网格位置；" +
                       "为防止界面卡顿，暂不展开完整网格。可在下方查看原始提取文本。",
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(12),
            };
            return;
        }

        var grid = new Grid();
        for (var col = 0; col < table.ColumnCount; col++)
            grid.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = new GridLength(CellWidth),
            });
        for (var row = 0; row < table.RowCount; row++)
            grid.RowDefinitions.Add(new RowDefinition
            {
                Height = GridLength.Auto,
            });

        foreach (var cell in table.SpanCells)
        {
            // Treat coordinates as untrusted data from a persisted Paper Card.
            if (cell.Row < 0 || cell.Column < 0 ||
                cell.RowSpan < 1 || cell.ColumnSpan < 1 ||
                cell.Row + cell.RowSpan > table.RowCount ||
                cell.Column + cell.ColumnSpan > table.ColumnCount)
                continue;

            var text = new TextBlock
            {
                Text = cell.Text,
                TextWrapping = TextWrapping.Wrap,
                FontSize = 11,
                FontWeight = cell.IsHeader ? FontWeight.SemiBold : FontWeight.Normal,
                VerticalAlignment = VerticalAlignment.Center,
            };
            var border = new Border
            {
                BorderThickness = new Thickness(0.5),
                BorderBrush = Brushes.Gray,
                Padding = new Thickness(9, 8),
                MinHeight = 40,
                Child = text,
            };
            Grid.SetRow(border, cell.Row);
            Grid.SetColumn(border, cell.Column);
            Grid.SetRowSpan(border, cell.RowSpan);
            Grid.SetColumnSpan(border, cell.ColumnSpan);
            grid.Children.Add(border);
        }

        _scrollViewer.Content = grid;
    }
}
