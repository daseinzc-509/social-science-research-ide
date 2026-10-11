"""V5 consent-gated comparative Pro review regression suite.

Runs offline with mock model responses. Existing v4 tests and a real source/
Paper Card SQLite fixture provide the baseline. These tests never call an
external API and do not claim that a real Pro model has been validated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from test_region_review_versioning import environment, _base, _confirm
from sociology_research.api.routes import region_review as routes
from sociology_research.services import region_review_reanalysis as service
from sociology_research.models import (
    PaperCard, ParsedDocument, ParseStatus, SourceBlock,
)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        routes, "_configured_pro",
        lambda: ("TEST_ONLY_KEY", "https://pro.example.com/v1", "GLM-simulated"),
    )
    return []


def _mock_comparison(plan):
    impacts = []
    for item in plan["human_reviews"]:
        impacts.append(service.ImpactItem(
            review_id=item["review_id"],
            status="QUALIFIES",
            baseline_claim_ids=[],
            source_block_ids=[
                source["source_block_id"]
                for source in item["linked_original_source"]
            ],
            updated_interpretation="仅按人工核实版本重新评价统计数字，不修改原作者引文。",
            reasoning="Docling 原始识别和研究者确认文字存在潜在差异，需要 PDF 核对。",
        ))
    return service.ReviewedComparison(
        summary="人工修订可能影响统计量的解释，尚不能直接归为作者原文。",
        impacts=impacts,
        limitations=["实际 PDF 图片内容需研究者再次核实"],
    )


def _prepare(client, monkeypatch):
    _confirm(client, monkeypatch)
    result=client.get(_base()+"/reanalysis/preview")
    assert result.status_code==200,result.text
    return result.json()


def _run(client, digest, **patch):
    body = {
        "expected_snapshot_digest":digest,
        "approve_external_send":True,
        "acknowledge_api_cost":True,
    }
    body.update(patch)
    return client.post(_base()+"/reanalysis/run",json=body)


def test_preview_never_calls_remote_api_and_does_not_disclose_key(environment, configured, monkeypatch):
    client, repo, _, _=environment
    called=[]
    monkeypatch.setattr(routes,"run_pro_comparison",lambda *a,**kw:called.append(True))
    preview=_prepare(client,monkeypatch)
    assert not called
    assert preview["will_send_to_external_api"] is True
    assert preview["requires_explicit_consent"] is True
    assert preview["only_one_pro_request"] is True
    assert preview["runs_lite"] is False
    assert preview["modifies_original_paper_card"] is False
    assert preview["review_count"]==1
    assert preview["provider"]=="https://pro.example.com"
    assert preview["pro_model"]=="GLM-simulated"
    assert len(preview["snapshot_digest"])==64
    assert "TEST_ONLY_KEY" not in json.dumps(preview)
    assert "original-A" not in json.dumps(preview.get("baseline_claims",[]))
    assert "Reviewed: 42.7 percent" in preview["outbound_payload"]
    assert "The original article reports 42.7 percent." in preview["outbound_payload"]
    assert "TEST_ONLY_KEY" not in preview["outbound_payload"]


def test_explicit_model_cost_and_external_send_consent_required(environment,configured,monkeypatch):
    client,_,_,_=environment
    preview=_prepare(client,monkeypatch)
    digest=preview["snapshot_digest"]
    assert _run(client,digest,approve_external_send=False).status_code==422
    assert _run(client,digest,acknowledge_api_cost=False).status_code==422
    assert _run(client,digest,approve_external_send="yes").status_code==422
    assert _run(client,digest,acknowledge_api_cost=None).status_code==422
    assert client.get(_base()+"/reanalysis/reports").json()["reports"]==[]


def test_valid_comparison_creates_separate_immutable_report(environment,configured,monkeypatch):
    client, repo, _, source=environment
    plan=_prepare(client,monkeypatch)
    old_card=repo.get_card("paper1").model_dump_json()
    old_cache=repo.get_model_run("paper1","lite","mock","v1","hash1")
    calls=[]
    def fake(plan,**kw):
        calls.append((plan,kw))
        return _mock_comparison(plan)
    monkeypatch.setattr(routes,"run_pro_comparison",fake)
    response=_run(client,plan["snapshot_digest"])
    assert response.status_code==200,response.text
    result=response.json()
    assert len(calls)==1
    assert calls[0][1]["api_key"]=="TEST_ONLY_KEY"
    assert result["original_card_unchanged"] is True
    assert result["applied_to_evidence"] is False
    assert result["lite_or_pro_cache_modified"] is False
    assert result["impact_count"]==1
    assert "AI_INFERRED" in result["markdown"]
    assert source.text==repo.get_source_blocks("paper1")[0].text
    assert repo.get_card("paper1").model_dump_json()==old_card
    assert repo.get_model_run("paper1","lite","mock","v1","hash1")==old_cache
    archived=client.get(_base()+"/reanalysis/reports").json()["reports"]
    assert len(archived)==1
    detail=client.get(_base()+f"/reanalysis/reports/{result['id']}")
    assert detail.status_code==200
    assert detail.headers["cache-control"]=="no-store, private"
    assert detail.json()["historical_snapshot"] is True
    assert detail.json()["original_card_sha256"]==plan["original_card_sha256"]
    assert detail.json()["input_snapshot"]["human_reviews"][0]["corrected_text"]=="Reviewed: 42.7 percent"
    assert detail.json()["comparison"]["impacts"][0]["status"]=="QUALIFIES"


def test_wrong_source_or_baseline_claim_id_is_rejected():
    plan={
        "human_reviews":[{
            "review_id":"review-123",
            "linked_original_source":[{"source_block_id":"s-1"}],
        }],
        "baseline_claims":[{"claim_id":"claim-1"}],
    }
    for claim_ids, source_ids in [(["fabricated"],["s-1"]),(["claim-1"],["fake"])]:
        reply=service.ReviewedComparison(summary="x",impacts=[
            service.ImpactItem(review_id="review-123",status="UNCERTAIN",
                               baseline_claim_ids=claim_ids,
                               source_block_ids=source_ids,
                               updated_interpretation="review text",reasoning="limitations")
        ])
        with pytest.raises(service.ReanalysisError):
            service.validate_comparison(reply,plan)
    with pytest.raises(service.ReanalysisError,match="exactly one"):
        service.validate_comparison(
            service.ReviewedComparison(summary="x",impacts=[
                service.ImpactItem(
                    review_id="fake-id",status="UNCERTAIN",
                    updated_interpretation="x",reasoning="y",
                )
            ]),plan
        )


def test_json_output_contract_with_fake_pro_client(environment,configured,monkeypatch):
    client,repo,_,_=environment
    preview=_prepare(client,monkeypatch)
    record=repo.get_paper("paper1")
    reviews=service.VersionedRegionReviewStore(repo.database_path)
    plan=service.build_plan(repo,"paper1","GLM-simulated",review_store=reviews)
    sends=[]
    class FakeClient:
        def __init__(self,**kw):
            assert kw["api_key"]=="TEST_ONLY_KEY"
        def complete_json(self,**kw):
            sends.append(kw)
            result=_mock_comparison(plan)
            return result.model_dump_json()
    result=service.run_pro_comparison(
        plan,api_key="TEST_ONLY_KEY",
        base_url="https://pro.example.com/v1",
        client_factory=FakeClient,
    )
    assert result.impacts[0].status=="QUALIFIES"
    assert len(sends)==1
    assert "corrected_text" in sends[0]["user_prompt"]
    assert "HUMAN" in sends[0]["system_prompt"] or "人工修订" in sends[0]["system_prompt"]
    assert sends[0]["model"]=="GLM-simulated"
    assert sends[0]["max_tokens"]==service.MAX_MODEL_TOKENS


def test_model_error_never_saves_incomplete_comparison(environment,configured,monkeypatch):
    client,_,_,_=environment
    plan=_prepare(client,monkeypatch)
    def fake(*a,**kw):
        raise service.ReanalysisError("Model returned wrong ids")
    monkeypatch.setattr(routes,"run_pro_comparison",fake)
    failed=_run(client,plan["snapshot_digest"])
    assert failed.status_code==502
    assert "may already" in failed.text
    assert client.get(_base()+"/reanalysis/reports").json()["reports"]==[]


def test_stale_plan_after_researcher_revision_rejected_before_call(environment,configured,monkeypatch):
    client,_,_,_=environment
    preview=_prepare(client,monkeypatch)
    reviews=client.get(_base()+"/reviews").json()["reviews"]
    previous=reviews[0]
    result=client.post(_base()+f"/reviews/{previous['id']}/revise",json={
        "expected_revision":1,
        "corrected_text":"changed after preview",
        "researcher_note":"new verification"
    })
    assert result.status_code==200
    calls=[]
    monkeypatch.setattr(routes,"run_pro_comparison",lambda *a,**kw:calls.append(1))
    stale=_run(client,preview["snapshot_digest"])
    assert stale.status_code==409
    assert not calls


def test_stale_source_after_model_request_discarded_without_write(environment,configured,monkeypatch):
    client,repo,_,source=environment
    plan=_prepare(client,monkeypatch)
    card_before=repo.get_card("paper1").model_dump_json()
    def changes_source(plan,**kw):
        current=repo.get_paper("paper1")
        refreshed=ParsedDocument(
            paper_id="paper1",sha256=current.sha256,
            source_path=current.stored_path,page_count=1,
            status=ParseStatus.SUCCESS,parser_version=current.parser_version,
            blocks=[SourceBlock(id=source.id,page_number=1,
                                text="OCR content changed during remote API call",
                                bbox=source.bbox,role=source.role)],
        )
        repo.refresh_parsed_document("paper1",refreshed)
        return _mock_comparison(plan)
    monkeypatch.setattr(routes,"run_pro_comparison",changes_source)
    stale=_run(client,plan["snapshot_digest"])
    assert stale.status_code==409
    assert "may already" in stale.text
    assert client.get(_base()+"/reanalysis/reports").json()["reports"]==[]
    assert repo.get_card("paper1").model_dump_json()==card_before


def test_card_changed_during_remote_api_call_refuses_save(environment,configured,monkeypatch):
    client,repo,_,_=environment
    plan=_prepare(client,monkeypatch)
    def changes_card(plan,**kw):
        original=repo.get_card("paper1")
        replacement=original.model_copy(update={"warnings":["Updated while model was running"]})
        repo.save_card(replacement)
        return _mock_comparison(plan)
    monkeypatch.setattr(routes,"run_pro_comparison",changes_card)
    response=_run(client,plan["snapshot_digest"])
    assert response.status_code==409
    assert client.get(_base()+"/reanalysis/reports").json()["reports"]==[]


def test_conflicting_human_corrections_block_the_entire_review(environment,configured,monkeypatch):
    client,_,_,_=environment
    _prepare(client,monkeypatch)
    _confirm(client,monkeypatch,text="Contradictory OCR number")
    blocked=client.get(_base()+"/reanalysis/preview")
    assert blocked.status_code==409
    assert "overlapping" in blocked.text


def test_page_only_human_correction_cannot_enter_v5_comparison(environment,configured,monkeypatch):
    client,_,_,_=environment
    _confirm(client,monkeypatch,bounds={"x0":0.75,"y0":0.55,"x1":0.95,"y1":0.9})
    blocked=client.get(_base()+"/reanalysis/preview")
    assert blocked.status_code==409


def test_new_report_endpoints_require_known_paper_and_scope(environment,configured,monkeypatch):
    client,_,_,_=environment
    assert client.get("/api/v1/papers/nonexistent/region-review/reanalysis/preview").status_code==404
    assert client.get(_base()+"/reanalysis/reports/wrong-id").status_code==404
    assert client.post(_base()+"/reanalysis/run",json={
        "expected_snapshot_digest":"not-hex",
        "approve_external_send":True,
        "acknowledge_api_cost":True
    }).status_code==422



def test_changing_pro_provider_after_preview_requires_new_consent(environment,configured,monkeypatch):
    client,_,_,_=environment
    preview=_prepare(client,monkeypatch)
    # Identical model name at a different provider URL MUST invalidate consent.
    monkeypatch.setattr(
        routes,"_configured_pro",
        lambda:("SECOND_PROVIDER_KEY","https://another.example/v2","GLM-simulated"),
    )
    invoked=[]
    monkeypatch.setattr(routes,"run_pro_comparison",lambda *a,**kw:invoked.append(True))
    response=_run(client,preview["snapshot_digest"])
    assert response.status_code==409
    assert not invoked


def test_long_human_review_text_must_not_be_silently_cut_off(environment,configured,monkeypatch):
    client,repo,_,_=environment
    _confirm(client,monkeypatch,text="扩展修正说明"*600)
    response=client.get(_base()+"/reanalysis/preview")
    assert response.status_code==409
    assert "too long" in response.text.lower()
