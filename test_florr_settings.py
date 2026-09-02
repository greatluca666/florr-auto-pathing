import json

import pytest

import florr_settings as fs


def _resp(payload):
    """把一个 dict 包成 cdp_bridge.eval_js 那种返回结构(payload 会被 JSON.stringify)."""
    return {"result": {"result": {"type": "string", "value": json.dumps(payload)}}}


def test_addr_not_calibrated_does_not_call_eval(monkeypatch):
    monkeypatch.setattr(fs, "INVERT_ATTACK_ADDR", None)
    calls = []
    status, detail = fs.ensure_invert_attack_on(lambda e: calls.append(e))
    assert (status, detail) == ("failed", "addr-not-calibrated")
    assert calls == []


def test_turned_on_when_before_zero():
    resp = _resp({"ok": True, "before": 0, "after": 1})
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=0xAD1234) == ("turned_on", "")


def test_on_already_when_before_one():
    resp = _resp({"ok": True, "before": 1, "after": 1})
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=0xAD1234) == ("on_already", "")


@pytest.mark.parametrize("reason", ["no-wasm-memory", "addr-out-of-range", "not-bool:7"])
def test_failed_passes_through_js_reason(reason):
    resp = _resp({"ok": False, "reason": reason})
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=1) == ("failed", reason)


def test_failed_on_eval_exception():
    def boom(_e):
        raise RuntimeError("no florr tab")
    assert fs.ensure_invert_attack_on(boom, addr=1) == ("failed", "cdp-error:no florr tab")


def test_failed_when_no_value_in_response():
    # 表达式在页面里抛异常时 Runtime.evaluate 不带 result.result.value
    resp = {"result": {"result": {"type": "object", "className": "Object"}}}
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=1) == ("failed", "no-value")


def test_failed_on_bad_json():
    resp = {"result": {"result": {"type": "string", "value": "not json{"}}}
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=1) == ("failed", "bad-json")


def test_failed_on_none_response():
    assert fs.ensure_invert_attack_on(lambda e: None, addr=1) == ("failed", "no-value")


def test_js_has_decimal_addr_and_is_stringify_iife():
    js = fs._js(0x1234)
    assert "const A = 4660;" in js
    assert js.startswith("JSON.stringify((() => {")
    assert js.rstrip().endswith("})())")
