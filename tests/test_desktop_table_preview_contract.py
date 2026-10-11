"""Static contracts for the Avalonia PDF table preview.

This intentionally does not claim to compile Avalonia or validate OCR quality.
CI's dotnet build must verify XAML compiled bindings on the target SDK.
"""
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "desktop/SRA.Desktop/Models/UiModels.cs"
VIEWMODEL = ROOT / "desktop/SRA.Desktop/ViewModels/MainWindowViewModel.cs"
VIEW = ROOT / "desktop/SRA.Desktop/Views/MainWindow.axaml"


def test_main_window_xaml_is_well_formed():
    root = ET.parse(VIEW).getroot()
    assert root.tag.endswith("Window")


def test_table_preview_keeps_json_source_and_row_structure():
    source = MODEL.read_text(encoding="utf-8")
    assert 'node.TryGetProperty("table_rows"' in source
    assert 'JsonRead.Text(node, "text")' in source
    assert 'string.IsNullOrWhiteSpace(originalText) ? flatText : originalText' in source
    assert "FirstRowCells" in source
    assert "RemainingRows" in source
    assert "RowCount" in source
    assert "ColumnCount" in source
    assert "SourceBlockId = JsonRead.Text(node, \"id\")" in source
    assert "result.Add(new TableCellItem" in source
    # Explicitly prevent silent correction/deduplication in presentation code.
    assert "Distinct()" not in source[source.index("public sealed class TableItem"):source.index("public static class FieldLabels")]


def test_preview_and_raw_audit_are_both_available():
    root = ET.parse(VIEW).getroot()
    tabs = [node for node in root.iter() if node.tag.endswith("TabItem")]
    table_tabs = [tab for tab in tabs if tab.attrib.get("Header") == "表格"]
    assert len(table_tabs) == 1
    elements = list(table_tabs[0].iter())
    assert any(
        element.tag.endswith("ScrollViewer")
        and element.attrib.get("HorizontalScrollBarVisibility") == "Auto"
        for element in elements
    )
    assert any(
        element.tag.endswith("ItemsControl")
        and element.attrib.get("ItemsSource") == "{Binding FirstRowCells}"
        for element in elements
    )
    assert any(
        element.tag.endswith("ItemsControl")
        and element.attrib.get("ItemsSource") == "{Binding RemainingRows}"
        for element in elements
    )
    assert any(
        element.tag.endswith("TextBox")
        and element.attrib.get("Text") == "{Binding DisplayText}"
        for element in elements
    )
    assert any(element.tag.endswith("Expander") for element in elements)


def test_warnings_are_collapsed_without_removing_raw_text():
    view = VIEW.read_text(encoding="utf-8")
    model = VIEWMODEL.read_text(encoding="utf-8")
    assert 'Text="{Binding WarningsSummary}"' in view
    assert 'Text="{Binding WarningsText}"' in view
    assert '<Expander IsExpanded="False">' in view
    assert 'WarningsSummary = warnings.Count == 0' in model
    assert 'string.Join(Environment.NewLine, warnings)' in model
    assert 'OnPropertyChanged(nameof(HasNoTables))' in model
    assert 'public bool HasNoTables => Tables.Count == 0;' in model
