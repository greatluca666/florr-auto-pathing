import biome_lock


def _cdp_str(value):
    """伪造 cdp_bridge.eval_js 的返回: Runtime.evaluate 不带 returnByValue 时
    字符串直接在 result.result.value."""
    return {"result": {"result": {"type": "string", "value": value}}}


def test_current_server_code_extracts_ws_subdomain():
    calls = []

    def fake_eval(expr):
        calls.append(expr)
        return _cdp_str("wss://25jn.s.m28n.net/")

    assert biome_lock.current_server_code(fake_eval) == "25jn"
    # 每次调用都(幂等)带上 WS url 钩子的安装代码
    assert "__florrWsHook" in calls[0]
    assert "WebSocket" in calls[0]


def test_current_server_code_empty_when_not_connected():
    assert biome_lock.current_server_code(lambda e: _cdp_str("")) == ""


def test_current_server_code_empty_for_non_florr_url():
    assert biome_lock.current_server_code(lambda e: _cdp_str("wss://foo.example.net/")) == ""


def test_current_server_code_empty_when_eval_returns_junk():
    assert biome_lock.current_server_code(lambda e: {"result": {}}) == ""
    assert biome_lock.current_server_code(lambda e: None) == ""


def test_current_server_code_swallows_eval_error():
    def boom(expr):
        raise RuntimeError("CDP 连不上")

    assert biome_lock.current_server_code(boom) == ""


def test_on_biome_true_when_code_in_pool():
    ev = lambda e: _cdp_str("wss://25jo.s.m28n.net/")
    assert biome_lock.on_biome(ev, ["25jn", "25jo", "25jp"]) is True


def test_on_biome_false_when_code_not_in_pool():
    ev = lambda e: _cdp_str("wss://25jk.s.m28n.net/")   # garden 服务器
    assert biome_lock.on_biome(ev, ["25jn", "25jo", "25jp"]) is False


def test_on_biome_false_when_no_current_code():
    assert biome_lock.on_biome(lambda e: _cdp_str(""), ["25jn"]) is False
