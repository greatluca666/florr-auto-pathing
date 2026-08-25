import pytest

from cdp_bridge import find_florr_tab, eval_js

# 这些测试跑在没有真实Chrome CDP端口监听的环境里(CI/开发机大部分时候都是) ——
# 只验证"连不上/找不到标签页"这条路径优雅退出, 不验证真实JS执行(那个需要
# 真Chrome, 属于debug_switch_server.py管的实机验证范畴).


def test_find_florr_tab_returns_none_when_cdp_port_not_listening():
    # 9222没人监听(没用--remote-debugging-port启动Chrome)时, 不该抛异常,
    # 该老老实实返回None, 让调用方决定怎么办.
    assert find_florr_tab() is None


def test_eval_js_raises_clear_error_when_no_tab_found():
    with pytest.raises(RuntimeError, match="florr.io标签页"):
        eval_js("1 + 1")
