"""Local, no-network regression cases for SRA image-analysis HTTP 502 handling.

These tests use mock Chat Completions provider JSON. No user image, model key,
PDF, external URL or LLM API is contacted.
"""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException

from sociology_research.api.routes import visual


def _choice(content, *, reason="stop", thinking=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if thinking is not None:
        msg["reasoning_content"] = thinking
    return {
        "choices": [{"index": 0, "message": msg, "finish_reason": reason}],
        "usage": usage or {},
    }


def _detail(value):
    with pytest.raises(HTTPException) as caught:
        visual._extract_vision_response(value, model="glm-5.3-flash")
    assert caught.value.status_code == 502
    return caught.value.detail


@pytest.mark.parametrize("body,expected", [
    (_choice("看到两个变量之间的正相关关系。"), "看到两个变量之间的正相关关系。"),
    (_choice([{"type": "text", "text": "横轴年份"},
              {"type": "text", "text": {"value": "纵轴比例"}}]),
     "横轴年份\n纵轴比例"),
    (_choice([{"type": "output_text", "text": "文本块结果"},
              {"type": "image", "url": "https://example.invalid/p.png"}]),
     "文本块结果"),
])
def test_normal_content_and_multipart_text(body, expected):
    assert visual._extract_vision_response(body, model="doubao-seed-2.1-pro") == expected


def test_reasoning_limit_reports_actual_cause_and_safe_usage():
    data = _choice(
        None, reason="length", thinking="PRIVATE internal reason that must not leak",
        usage={
            "completion_tokens": 1100,
            "completion_tokens_details": {"reasoning_tokens": 1100},
        },
    )
    message = _detail(data)
    assert "生成上限" in message
    assert "finish_reason=length" in message
    assert "思考=1100tokens" in message
    assert "PRIVATE" not in message
    assert "不支持图片" not in message


def test_no_final_message_but_reasoning_does_not_leak():
    message = _detail(_choice(
        None, thinking="hidden reasoning PRIVATE_EXCERPT"
    ))
    assert "思考内容" in message
    assert "PRIVATE_EXCERPT" not in message


def test_empty_answer_is_not_automatically_called_unsupported_image():
    message = _detail(_choice(""))
    assert "空的最终回答" in message
    assert "image_url" in message


def test_missing_choices_distinguished_from_reasoning_limit():
    message = _detail({"result": "not a chat completion"})
    assert "缺少 choices" in message
    assert "生成上限" not in message


def test_supported_responses_style_output_text():
    assert visual._extract_vision_response(
        {"output_text": "检测到一张折线图。"}, model="m"
    ) == "检测到一张折线图。"


def test_supported_responses_style_message_parts_without_reasoning():
    data = {"output":[
        {"type":"reasoning","summary":[{"text":"PRIVATE_DO_NOT_SHOW"}]},
        {"type":"message","content":[{"type":"output_text","text":"最终可核验图像描述。"}]}
    ]}
    text = visual._extract_vision_response(data, model="m")
    assert text == "最终可核验图像描述。"
    assert "PRIVATE" not in text


def test_provider_error_code_only_not_message():
    message = _detail({"error":{
        "code":"UnsupportedImageFormat", "message":"PRIVATE_SECRET API_KEY"
    }})
    assert "UnsupportedImageFormat" in message
    assert "PRIVATE_SECRET" not in message


def test_content_filter_explained():
    message = _detail(_choice(None, reason="content_filter"))
    assert "审核" in message


def test_safe_request_id_not_rogue_header():
    assert visual._safe_request_id({
        "x-request-id":"req-abc_987"
    }) == "req-abc_987"
    assert visual._safe_request_id({
        "x-request-id":"api-key secret/private data"
    }) == ""


class FakeResponse:
    def __init__(self, reply, headers=None):
        if isinstance(reply, bytes):
            self.data=reply
        else:
            self.data=json.dumps(reply,ensure_ascii=False).encode()
        self.headers=headers or {}

    def read(self, size=-1):
        return self.data[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_api_request_sends_one_cropped_image_and_4096_budget(monkeypatch):
    sent=[]
    def pretend_request(request, timeout):
        sent.append((request, timeout))
        return FakeResponse(_choice("图1：一张柱状图。"))
    monkeypatch.setattr(visual, "urlopen", pretend_request)
    outcome=visual._request_vision(
        "secret-key", "https://ark.cn-beijing.volces.com/api/plan/v3",
        "glm-5.3-flash", b"\xff\xd8\xffimage"
    )
    assert outcome == "图1：一张柱状图。"
    assert len(sent)==1
    request,timeout=sent[0]
    assert timeout==180
    body=json.loads(request.data)
    assert body["max_completion_tokens"]==4096
    assert "max_tokens" not in body
    assert body["stream"] is False
    assert len(body["messages"][1]["content"])==2
    image_url=body["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    assert "secret-key" not in body["messages"][1]["content"][0]["text"]


def test_one_request_only_when_thinking_exhausts_budget(monkeypatch):
    count=[]
    def pretend(request, timeout):
        count.append(1)
        return FakeResponse(
            _choice(None, reason="length", thinking="unsafe reasoning"),
            {"x-request-id":"req-1234"},
        )
    monkeypatch.setattr(visual,"urlopen",pretend)
    with pytest.raises(HTTPException) as err:
        visual._request_vision(
            "key", "https://mock.example/v1", "model-x", b"jpegbytes"
        )
    assert err.value.status_code == 502
    assert "请求ID=req-1234" in err.value.detail
    assert len(count)==1


def test_429_error_is_distinguished_from_empty_content(monkeypatch):
    count=[]
    def pretend(request, timeout):
        count.append(1)
        raise HTTPError(
            request.full_url, 429, "rate limited",
            {"Retry-After":"35","x-request-id":"req-429"},
            io.BytesIO(json.dumps({
                "error":{"code":"rate_limit_exceeded","message":"PRIVATE"}
            }).encode()),
        )
    monkeypatch.setattr(visual,"urlopen",pretend)
    with pytest.raises(HTTPException) as err:
        visual._request_vision(
            "key", "https://mock.example/v1", "vision-model", b"jpegbytes"
        )
    assert err.value.status_code == 502
    assert "429" in err.value.detail
    assert "req-429" in err.value.detail
    assert "PRIVATE" not in err.value.detail
    assert len(count)==1


def test_malformed_json_reports_protocol_not_image_compatibility(monkeypatch):
    count=[]
    def pretend(request, timeout):
        count.append(1)
        return FakeResponse(b'{"choices": [')
    monkeypatch.setattr(visual,"urlopen",pretend)
    with pytest.raises(HTTPException) as err:
        visual._request_vision("key","https://mock.example/v1","model",b"bytes")
    assert "完整、可解析的 JSON" in err.value.detail
    assert len(count)==1


def test_oversize_response_rejected_without_false_vision_diagnosis(monkeypatch):
    def pretend(request, timeout):
        return FakeResponse(b"x" * (visual.VISION_RESPONSE_LIMIT_BYTES + 2))
    monkeypatch.setattr(visual,"urlopen",pretend)
    with pytest.raises(HTTPException) as err:
        visual._request_vision("key","https://mock.example/v1","model",b"bytes")
    assert "响应过大" in err.value.detail


def test_full_route_keeps_original_pdf_provenance_and_no_card_write():
    from pathlib import Path
    import inspect
    source=inspect.getsource(visual.analyze_figure)
    assert "_verify_pdf" in source
    assert "_render_figure" in source
    assert "approve_external_send" in source
    assert "saved_to_card" in source
    assert "AI_INFERRED_UNVERIFIED" in source
    assert ".save_card(" not in source



def test_full_api_crop_request_requires_explicit_consent(monkeypatch, tmp_path):
    import hashlib
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    pdf=tmp_path/"article.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    record=SimpleNamespace(
        id="paper-1", sha256=hashlib.sha256(pdf.read_bytes()).hexdigest()
    )
    figure=SimpleNamespace(
        id="img-7", page_number=1, bbox=(10.,20.,120.,200.)
    )
    fake_repo=SimpleNamespace(
        get_paper=lambda pid:record if pid == "paper-1" else None,
        get_figures=lambda pid:[figure],
    )
    config=SimpleNamespace(
        vision_analysis_enabled=True,
        require_vision_configuration=lambda:
            ("SECRET","https://mock.example/v1","glm-5.3-flash"),
    )
    monkeypatch.setattr(
        visual.Settings,"from_environment",classmethod(lambda cls:config)
    )
    calls=[]
    monkeypatch.setattr(
        visual,"_render_figure",
        lambda pdf_path,page_no,bounds:b"\xff\xd8\xfffakejpeg"
    )
    monkeypatch.setattr(
        visual,"_request_vision",
        lambda key,url,model,image:calls.append((key,url,model,image)) or "结果"
    )
    app=FastAPI()
    app.include_router(visual.figure_router,prefix="/api/v1")
    app.dependency_overrides[visual.get_services]=lambda:SimpleNamespace(
        repository=fake_repo,papers=SimpleNamespace(pdf_path=lambda paper_id:pdf)
    )
    with TestClient(app) as client:
        endpoint="/api/v1/papers/paper-1/figures/img-7/analyze"
        assert client.post(endpoint,json={"approve_external_send":False}).status_code==422
        assert calls==[]
        response=client.post(endpoint,json={"approve_external_send":True})
        assert response.status_code==200, response.text
        payload=response.json()
        assert payload["description"]=="结果"
        assert payload["provenance"]=="AI_INFERRED_UNVERIFIED"
        assert payload["saved_to_card"] is False
        assert payload["requires_pdf_verification"] is True
        assert calls[0][2]=="glm-5.3-flash"
        assert len(calls)==1


def test_api_does_not_repeat_call_if_model_finishes_with_length(monkeypatch, tmp_path):
    import hashlib
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    pdf=tmp_path/"paper.pdf"
    pdf.write_bytes(b"%PDF-mock")
    record=SimpleNamespace(sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())
    image=SimpleNamespace(id="fig",page_number=1,bbox=(10.,10.,60.,60.))
    config=SimpleNamespace(
        vision_analysis_enabled=True,
        require_vision_configuration=lambda:("SECRET","https://mock.example/v1","vision")
    )
    monkeypatch.setattr(
        visual.Settings,"from_environment",classmethod(lambda cls:config)
    )
    monkeypatch.setattr(visual,"_render_figure",lambda *a:b"image bytes")
    requests=[]
    def network(req,timeout):
        requests.append(req)
        return FakeResponse(_choice(None,reason="length",thinking="not a result"))
    monkeypatch.setattr(visual,"urlopen",network)

    app=FastAPI()
    app.include_router(visual.figure_router,prefix="/api/v1")
    app.dependency_overrides[visual.get_services]=lambda:SimpleNamespace(
        repository=SimpleNamespace(
            get_paper=lambda _:record,get_figures=lambda _:[image]
        ),
        papers=SimpleNamespace(pdf_path=lambda _:pdf)
    )
    with TestClient(app) as client:
        response=client.post(
            "/api/v1/papers/paper-1/figures/fig/analyze",
            json={"approve_external_send":True}
        )
        assert response.status_code==502
        assert "finish_reason=length" in response.json()["detail"]
        assert len(requests)==1
