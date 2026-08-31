import pytest

import gui_chrome_flow as flow


@pytest.fixture(autouse=True)
def stub_cdp(monkeypatch):
    state = {"ready": False, "launched": 0, "tab_results": [], "ready_calls": 0, "wait_timeouts": []}

    def stub_ready():
        state["ready_calls"] += 1
        return state["ready"]

    def stub_wait(timeout, *a, **k):
        state["wait_timeouts"].append(timeout)
        return state["tab_results"].pop(0)

    monkeypatch.setattr(flow.cdp_bridge, "is_dedicated_chrome_ready", stub_ready)
    monkeypatch.setattr(flow.cdp_bridge, "quit_and_launch_chrome",
                        lambda: state.__setitem__("launched", state["launched"] + 1))
    monkeypatch.setattr(flow.cdp_bridge, "wait_for_florr_tab", stub_wait)
    return state


def test_returns_immediately_when_already_ready(stub_cdp):
    stub_cdp["ready"] = True
    flow.ensure_chrome_ready(None, confirm=lambda p: pytest.fail("不该问确认"),
                             prompt_retry=lambda p: pytest.fail("不该问重试"))
    assert stub_cdp["launched"] == 0


def test_cancel_at_confirm_raises(stub_cdp):
    with pytest.raises(flow.ChromeSetupCancelled):
        flow.ensure_chrome_ready(None, confirm=lambda p: False,
                                 prompt_retry=lambda p: True)
    assert stub_cdp["launched"] == 0


def test_confirm_then_tab_found_succeeds(stub_cdp):
    stub_cdp["tab_results"] = [{"url": "https://florr.io/"}]
    flow.ensure_chrome_ready(None, confirm=lambda p: True,
                             prompt_retry=lambda p: pytest.fail("不该问重试"))
    assert stub_cdp["launched"] == 1
    # 确保 wait_for_florr_tab 被调用时指定了 15 秒超时
    assert stub_cdp["wait_timeouts"] == [15]


def test_retry_once_then_found(stub_cdp):
    stub_cdp["tab_results"] = [None, {"url": "https://florr.io/"}]
    retries = []
    flow.ensure_chrome_ready(None, confirm=lambda p: True,
                             prompt_retry=lambda p: retries.append(1) or True)
    assert len(retries) == 1
    assert stub_cdp["launched"] == 1
    # 确保就绪状态仅在初始检查时被查询一次，重试循环中不会重新轮询
    assert stub_cdp["ready_calls"] == 1


def test_retry_declined_raises(stub_cdp):
    stub_cdp["tab_results"] = [None]
    with pytest.raises(flow.ChromeSetupCancelled):
        flow.ensure_chrome_ready(None, confirm=lambda p: True,
                                 prompt_retry=lambda p: False)
