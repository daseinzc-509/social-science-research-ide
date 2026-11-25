"""桌面端 Material Design 3 主题、拖放导入、快捷键与证据跳页闭环的静态契约测试。"""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "SRA.Desktop"
STYLES = DESKTOP / "Styles"


def test_all_desktop_axaml_styles_and_controls_are_well_formed():
    targets = [
        DESKTOP / "App.axaml",
        DESKTOP / "Views" / "MainWindow.axaml",
        DESKTOP / "Views" / "AnalyzeWindow.axaml",
        DESKTOP / "Views" / "MetadataWindow.axaml",
        DESKTOP / "Views" / "ConfirmWindow.axaml",
        DESKTOP / "Controls" / "ClaimCard.axaml",
        STYLES / "ControlStyles.axaml",
        STYLES / "ShellStyles.axaml",
        STYLES / "LibraryStyles.axaml",
        STYLES / "ReadingStyles.axaml",
        STYLES / "InspectorStyles.axaml",
    ]
    for path in targets:
        root = ET.parse(path).getroot()
        assert root is not None, f"无法解析 XAML：{path}"


def test_md3_tonal_surface_tokens_and_pill_button_hierarchy():
    app_xaml = (DESKTOP / "App.axaml").read_text(encoding="utf-8")
    for token in (
        "SurfaceContainerLowestBrush",
        "SurfaceContainerLowBrush",
        "SurfaceContainerBrush",
        "SurfaceContainerHighBrush",
        "SurfaceContainerHighestBrush",
        "TonalFillBrush",
        "OnTonalFillBrush",
        "OutlineSubtleBrush",
    ):
        assert app_xaml.count(f'x:Key="{token}"') == 2, f"明暗双主题必须同时定义 {token}"

    controls = (STYLES / "ControlStyles.axaml").read_text(encoding="utf-8")
    assert 'Selector="Button.primary"' in controls
    assert 'Selector="Button.tonal"' in controls
    assert 'Selector="Button.secondary"' in controls
    assert 'Selector="Button.ghost"' in controls
    assert 'Selector="Button.danger"' in controls
    assert 'Selector="Border.drop-overlay-card"' in controls


def test_main_window_drag_drop_and_shortcuts_wired():
    xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")
    code = (DESKTOP / "Views" / "MainWindow.axaml.cs").read_text(encoding="utf-8")

    assert 'DragDrop.AllowDrop="True"' in xaml
    assert 'x:Name="DropOverlay"' in xaml
    assert 'x:Name="LibrarySearchBox"' in xaml

    assert "AttachDragAndDrop();" in code
    assert "AttachEvidenceNavigation();" in code
    assert "KeyDown += MainWindow_KeyDown;" in code
    assert "e.DataTransfer.TryGetFiles()" in code
    assert "Key.F" in code
    assert "Key.OemQuestion" in code
    assert "Key.O" in code
    assert "Key.Escape" in code


def test_claim_evidence_jumps_to_pdf_page_with_highlight():
    card_xaml = (DESKTOP / "Controls" / "ClaimCard.axaml").read_text(encoding="utf-8")
    card_cs = (DESKTOP / "Controls" / "ClaimCard.axaml.cs").read_text(encoding="utf-8")
    bridge = (DESKTOP / "Views" / "MainWindow.RegionReview.cs").read_text(encoding="utf-8")
    review = (DESKTOP / "Views" / "RegionReviewWindow.cs").read_text(encoding="utf-8")

    assert 'Click="EvidenceJump_Click"' in card_xaml
    assert "EvidenceJumpRequestedEvent" in card_cs
    assert "RoutingStrategies.Bubble" in card_cs
    assert "ClaimCard.EvidenceJumpRequestedEvent" in bridge
    assert "initialPage: page" in bridge
    assert "highlightSourceBlockId: blockId" in bridge

    assert "int initialPage = 1" in review
    assert "string? highlightSourceBlockId = null" in review
    assert "_highlightShape" in review
    assert "TryApplyEvidenceHighlight" in review
    assert "LoadPageAsync(_initialPage)" in review


def test_dialogs_use_md3_cards_and_three_tab_region_review():
    review = (DESKTOP / "Views" / "RegionReviewWindow.cs").read_text(encoding="utf-8")
    model_center = (DESKTOP / "Views" / "ModelCenterWindow.cs").read_text(encoding="utf-8")

    assert "① 框选校订" in review
    assert "② 版本历史" in review
    assert "③ 独立复评 (Pro)" in review
    assert '"paper-tabs"' in review

    assert '"paper-tabs"' in model_center
    assert '"tonal-card"' in model_center
    assert '"workspace-surface"' in model_center
