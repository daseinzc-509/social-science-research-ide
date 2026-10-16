"""Static wiring checks for v5 native Avalonia + API client.

These tests do not replace Windows `dotnet build` or UI interaction tests.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
WINDOW=ROOT/"desktop/SRA.Desktop/Views/RegionReviewWindow.cs"
COMPARE=ROOT/"desktop/SRA.Desktop/Views/ReviewedComparisonWindow.cs"
CLIENT=ROOT/"desktop/SRA.Desktop/Services/SraApiClient.cs"
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
    # Check the integrated C# client, not a deleted one-off migration script.
    client=CLIENT.read_text(encoding="utf-8")
    route=ROUTE.read_text(encoding="utf-8")
    start=client.index("public Task<JsonElement> PreviewReviewedComparisonAsync")
    end=client.index("public Task<JsonElement> GetVisualSettingsAsync", start)
    methods=client[start:end]
    for method in ("PreviewReviewedComparisonAsync", "RunReviewedComparisonAsync",
                   "ListReviewedComparisonsAsync", "GetReviewedComparisonAsync"):
        assert re.search(r"public\s+(?:async\s+)?Task<JsonElement>\s+"+method+r"\s*\(", methods)
    for endpoint in ("/reanalysis/preview", "/reanalysis/run", "/reanalysis/reports"):
        assert endpoint in route
        assert endpoint in methods
    assert 'expected_snapshot_digest = snapshotDigest' in methods
    assert 'approve_external_send = externalConsent' in methods
    assert 'acknowledge_api_cost = costConsent' in methods
    assert 'ReadJsonAsync<JsonElement>(response, cancellationToken)' in methods
    assert 'GetJsonAsync<JsonElement>(' in methods
    assert 'Uri.EscapeDataString(paperId)' in methods


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
