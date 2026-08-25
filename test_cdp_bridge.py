import json
from unittest.mock import patch, MagicMock
from urllib.error import URLError

import pytest

from cdp_bridge import find_florr_tab, eval_js, capture_screenshot

# 不能依赖"这台机器9222端口有没有真Chrome在监听"这种外部状态 —— 之前这样写过,
# 开发机上真跑起了CDP-enabled Chrome后这些测试就全炸了(find_florr_tab意外真的
# 找到标签页, eval_js意外真的没报错). 全部mock掉urllib请求, 让测试结果只取决于
# 代码逻辑本身, 不取决于谁凑巧在跑什么.


def test_find_florr_tab_returns_none_when_cdp_port_not_listening():
    with patch("cdp_bridge.urllib.request.urlopen", side_effect=URLError("refused")):
        assert find_florr_tab() is None


def test_find_florr_tab_returns_none_when_no_florr_tab_open():
    other_tabs = json.dumps([{"url": "https://example.com/", "id": "1"}]).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = other_tabs
    with patch("cdp_bridge.urllib.request.urlopen", return_value=mock_resp):
        assert find_florr_tab() is None


def test_find_florr_tab_finds_florr_among_other_tabs():
    tabs = json.dumps([
        {"url": "https://example.com/", "id": "1"},
        {"url": "https://florr.io/", "id": "2", "webSocketDebuggerUrl": "ws://x"},
    ]).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = tabs
    with patch("cdp_bridge.urllib.request.urlopen", return_value=mock_resp):
        tab = find_florr_tab()
        assert tab is not None
        assert tab["id"] == "2"


def test_eval_js_raises_clear_error_when_no_tab_found():
    with patch("cdp_bridge.find_florr_tab", return_value=None):
        with pytest.raises(RuntimeError, match="florr.io标签页"):
            eval_js("1 + 1")


def test_capture_screenshot_raises_clear_error_when_no_tab_found():
    with patch("cdp_bridge.find_florr_tab", return_value=None):
        with pytest.raises(RuntimeError, match="florr.io标签页"):
            capture_screenshot()
