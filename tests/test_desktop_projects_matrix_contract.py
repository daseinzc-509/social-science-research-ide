"""桌面端去杂物抽屉、原生右键菜单、专题目录树与 Evidence Matrix 静态契约测试。"""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "SRA.Desktop"


def test_kitchen_sink_three_dots_menu_is_removed_and_actions_relocated():
    xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")
    root = ET.fromstring(xaml)
    assert root is not None

    # 严守 desktop/SRA.Desktop/AGENTS.md：彻底移除右上角大杂烩 ••• 菜单
    assert 'Content="•••"' not in xaml
    # 独立意图清晰的导出按钮
    assert 'Content="导出 ▾"' in xaml
    # 概览书目卡片就地编辑题录 + 底部独立文献维护卡片
    assert 'Content="编辑题录"' in xaml
    assert 'Text="文献维护"' in xaml
    # 右侧研究画像就地展示所属专题芯片与归入入口
    assert 'Text="所属研究专题"' in xaml
    assert 'Click="AssignSelectedPaperProjects_Click"' in xaml


def test_native_context_menus_exist_for_papers_and_projects():
    xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")

    # 论文列表项原生右键菜单
    for handler in (
        "ContextOpenPaperPdf_Click",
        "ContextAnalyzePaper_Click",
        "ContextRegionReviewPaper_Click",
        "ContextAssignPaperProjects_Click",
        "ContextEditPaperMetadata_Click",
        "ContextExtractPaperReferences_Click",
        "ContextExportReading_Click",
        "ContextExportBibtex_Click",
        "ContextReparsePaper_Click",
        "ContextDeletePaper_Click",
    ):
        assert f'Click="{handler}"' in xaml, f"缺少论文右键菜单事件：{handler}"

    # 研究专题文件夹原生右键菜单
    for handler in (
        "ContextOpenProjectMatrix_Click",
        "ContextAddPapersToProject_Click",
        "ContextEditProject_Click",
        "ContextExportProjectMatrix_Click",
        "ContextDeleteProject_Click",
    ):
        assert f'Click="{handler}"' in xaml, f"缺少专题右键菜单事件：{handler}"


def test_topic_tree_and_deterministic_evidence_matrix_contracts():
    xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")
    projects_vm = (DESKTOP / "ViewModels" / "MainWindowViewModel.Projects.cs").read_text(encoding="utf-8")
    projects_cs = (DESKTOP / "Views" / "MainWindow.Projects.cs").read_text(encoding="utf-8")
    client_cs = (DESKTOP / "Services" / "SraApiClient.cs").read_text(encoding="utf-8")

    # 左侧目录树筛选与专题切换
    assert 'Click="FilterAll_Click"' in xaml
    assert 'Click="FilterAnalyzed_Click"' in xaml
    assert 'Click="FilterPending_Click"' in xaml
    assert 'Click="CreateProject_Click"' in xaml
    assert 'Click="SwitchToSingleView_Click"' in xaml
    assert 'Click="SwitchToMatrixView_Click"' in xaml

    # Evidence Matrix 严格来源分离与零 LLM 确定性声明、原文跳页
    assert "EVIDENCE MATRIX" in xaml
    assert 'Click="MatrixCitationJump_Click"' in xaml
    assert 'Click="MatrixOpenPaper_Click"' in xaml
    assert 'Text="AUTHOR"' in xaml
    assert 'Text="AI REVIEW"' in xaml
    assert "零模型调用" in projects_vm
    assert "BuildActiveMatrixMarkdown" in projects_vm

    # 删除专题或移出文献必须明确提示绝不删除原论文
    assert "绝不会删除文献库中的任何 PDF" in projects_cs
    assert "不会删除文献库中的 PDF" in projects_cs
    assert "highlightSourceBlockId: blockId" in projects_cs

    # SraApiClient 完整项目 CRUD 与关联方法
    for method in (
        "GetProjectsAsync",
        "CreateProjectAsync",
        "GetProjectDetailAsync",
        "UpdateProjectAsync",
        "DeleteProjectAsync",
        "UpsertProjectPaperAsync",
        "RemoveProjectPaperAsync",
        "GetProjectEvidenceMatrixAsync",
    ):
        assert f"{method}(" in client_cs, f"SraApiClient 缺少 {method}"


def test_desktop_ux_agents_rule_exists():
    rule = DESKTOP / "AGENTS.md"
    assert rule.is_file()
    text = rule.read_text(encoding="utf-8")
    assert "禁止使用大杂烩" in text
    assert "ContextMenu" in text
    assert "专题文件夹" in text
