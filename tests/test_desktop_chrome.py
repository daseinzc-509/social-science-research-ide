"""Small desktop XAML contract checks; does not require a graphical session."""
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT = Path(__file__).resolve().parents[1] / "desktop" / "SRA.Desktop"
MAIN = PROJECT / "Views" / "MainWindow.axaml"
NS = {"a": "https://github.com/avaloniaui"}


def test_main_window_xaml_is_well_formed():
    root = ET.parse(MAIN).getroot()
    assert root.tag == "{https://github.com/avaloniaui}Window"


def test_minimize_icon_has_nonzero_dimensions():
    root = ET.parse(MAIN).getroot()
    matches = [node for node in root.findall(".//a:Button", NS)
               if node.get("Click") == "MinimizeWindow_Click"]
    assert len(matches) == 1
    bars = matches[0].findall("a:Border", NS)
    assert len(bars) == 1
    assert float(bars[0].get("Width", "0")) >= 12
    assert float(bars[0].get("Height", "0")) >= 1
    assert "InkBrush" in bars[0].get("Background", "")


def test_api_status_does_not_occupy_top_navigation():
    root = ET.parse(MAIN).getroot()
    assert not any((n.get("Text") or "") == "本地 API"
                   for n in root.findall(".//a:TextBlock", NS))
    assert any(node.get("Click") == "ImportPdf_Click"
               for node in root.findall(".//a:MenuItem", NS))


def test_window_commands_still_exist():
    root = ET.parse(MAIN).getroot()
    commands = {node.get("Click") for node in root.findall(".//a:Button", NS)}
    assert {"MinimizeWindow_Click", "ToggleMaximizeWindow_Click", "CloseWindow_Click"} <= commands
