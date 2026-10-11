"""Static wiring checks for v5 native Avalonia + API client.

These tests do not replace Windows `dotnet build` or UI interaction tests.
"""
from __future__ import annotations

from pathlib import Path
import ast

ROOT=Path(__file__).resolve().parents[1]
WINDOW=ROOT/"desktop/SRA.Desktop/Views/RegionReviewWindow.cs"
COMPARE=ROOT/"desktop/SRA.Desktop/Views/ReviewedComparisonWindow.cs"
INSTALLER=ROOT/"apply_region_review_v5.py"
ROUTE=ROOT/"src/sociology_research/api/routes/region_review.py"


def test_explicit_consent_and_outbound_preview_ui():
    window=WINDOW.read_text(encoding="utf-8")
    assert 'IsChecked = false' in window
    assert '_externalConsent.IsChecked != true' in window
    assert 'outbound_payload' in window
    assert '查看即将发送给 Pro API 的完整文字' in window
    assert 'PrepareReviewedComparisonAsync' in window
    assert 'RunReviewedComparisonAsync' in window
    assert 'ResetReviewRunPlan' in window


def test_historical_two_column_comparison_ui_and_source_labels():
    compare=COMPARE.read_text(encoding="utf-8")
    assert 'ColumnDefinitions("*,*")' in compare
    assert 'AUTHOR_STATED / 原始 Paper Card' in compare
    assert 'HUMAN_REVIEWED + AI_INFERRED' in compare
    assert 'original_text' in compare
    assert 'updated_interpretation' in compare
    assert 'original_card_sha256' in compare
    assert 'Clipboard' in compare


def test_api_client_methods_injected_with_matching_route_paths():
    module=ast.parse(INSTALLER.read_text(encoding="utf-8"))
    assignment=next(node for node in module.body if isinstance(node,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=="CLIENT_METHODS" for t in node.targets))
    methods=ast.literal_eval(assignment.value)
    route=ROUTE.read_text(encoding="utf-8")
    for method in ("PreviewReviewedComparisonAsync","RunReviewedComparisonAsync",
                   "ListReviewedComparisonsAsync","GetReviewedComparisonAsync"):
        assert method in methods
    assert "/reanalysis/preview" in route
    assert "/reanalysis/run" in route
    assert "/reanalysis/reports" in route
    assert 'approve_external_send' in methods
    assert 'acknowledge_api_cost' in methods
    assert 'ReadJsonAsync<JsonElement>' in methods


def test_fail_closed_code_still_preserves_baseline():
    route=ROUTE.read_text(encoding="utf-8")
    service=(ROOT/"src/sociology_research/services/region_review_reanalysis.py").read_text(encoding="utf-8")
    assert 'approve_external_send is not True' in route
    assert 'snapshot_digest"] != payload.expected_snapshot_digest' in route
    assert 'plan["provider_digest"]' in service
    assert 'BEGIN IMMEDIATE' in service
    assert 'ReviewReanalysisStore' in service
    assert "repository.save_card(" not in service
    assert "repository.save_model_run(" not in service
    assert "pipeline.analyze(" not in service
