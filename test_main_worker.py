import inspect
import io

import pytest

import main


def test_apply_worker_config_maps_keys(monkeypatch):
    applied = {}
    monkeypatch.setattr(main, "apply_map", lambda name: applied.setdefault("map", name))
    cfg = {
        "map": "ocean",
        "location": [11, 22],
        "farming_area": [[1, 2], [3, 4]],
        "farming_duration": 120,
        "consecutive_short_round_limit": 5,
        "enemy_ai_enabled": False,
        "auto_switch_server": False,
        "afk_enabled": True,
    }
    w = main._apply_worker_config(cfg)
    assert applied["map"] == "ocean"
    assert w["location"] == (11, 22)
    assert w["farming_area"] == [(1, 2), (3, 4)]
    assert w["farming_duration"] == 120
    assert w["short_round_limit"] == 5
    assert w["enemy_ai_enabled"] is False
    assert w["auto_switch_server"] is False


def test_maybe_scan_enemies_disabled_never_touches_enemy_detect(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(AssertionError("不该扫描")))
    decision, dets, last = main._maybe_scan_enemies(False, 1000.0, 0.0, ("chase", "x"), ["old"])
    assert decision == ("wander", None)
    assert dets == []
    assert last == 0.0


def test_maybe_scan_enemies_throttled_returns_prev(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(AssertionError("还没到扫描间隔")))
    prev, prev_dets = ("flee", [(1, 2)]), ["d1", "d2"]
    decision, dets, last = main._maybe_scan_enemies(True, 0.1, 0.0, prev, prev_dets)
    assert decision is prev
    assert dets is prev_dets
    assert last == 0.0


def test_maybe_scan_enemies_scans_when_due(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies", lambda **k: ["det"])
    monkeypatch.setattr(main.enemy_detect, "select_action",
                        lambda dets, **k: ("chase", "target", 250, []))
    now = main.ENEMY_SCAN_INTERVAL + 1.0
    decision, dets, last = main._maybe_scan_enemies(True, now, 0.0, ("wander", None), [])
    assert decision == ("chase", "target", 250, [])
    assert dets == ["det"]
    assert last == now


def test_maybe_scan_enemies_scan_error_degrades_to_wander(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("model missing")))
    now = main.ENEMY_SCAN_INTERVAL + 1.0
    decision, dets, last = main._maybe_scan_enemies(True, now, 0.0, ("wander", None), ["old"])
    assert decision == ("wander", None)
    assert dets == []
    assert last == now


def test_auto_farming_accepts_enemy_ai_enabled_kwarg():
    sig = inspect.signature(main.auto_farming)
    assert "enemy_ai_enabled" in sig.parameters
    assert sig.parameters["enemy_ai_enabled"].kind == inspect.Parameter.KEYWORD_ONLY


def test_worker_graceful_exit_resets_keyboard_then_exits(monkeypatch):
    called = []
    monkeypatch.setattr(main, "reset_keyboard", lambda: called.append("reset"))
    with pytest.raises(SystemExit):
        main._worker_graceful_exit(15, None)
    assert called == ["reset"]


class _StubOverlay:
    def update(self, **kw):
        pass

    def show_warning(self, *a, **k):
        pass

    def hide_warning(self):
        pass


def test_run_worker_does_not_start_florr_auto_afk(monkeypatch):
    """florr-auto-afk 的生命周期归 GUI. worker 一旦自己调
    ensure_florr_auto_afk_running(), 在 exe 缺失时它会走到 input() —— 而
    console=False 打包出来的 worker stdin 是死的, 那一下直接把 worker 撂倒
    (RuntimeError: lost sys.stdin), 第一轮都跑不到. 而且用户刚在界面上关掉
    AFK 开关, worker 又会把它拉回来.
    """
    monkeypatch.setattr(main.cdp_bridge, "is_dedicated_chrome_ready", lambda: True)
    monkeypatch.setattr(
        main.afk_watch, "ensure_florr_auto_afk_running",
        lambda *a, **k: pytest.fail("worker 不该自己去拉起 florr-auto-afk"))
    monkeypatch.setattr(main, "create_overlay", lambda *a, **k: _StubOverlay())
    monkeypatch.setattr(main, "overlay", None, raising=False)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2),
        "farming_area": [(0, 0), (9, 9)],
        "farming_duration": 300,
        "short_round_limit": 2,
        "enemy_ai_enabled": False,
        "auto_switch_server": False,
    })
    # 主循环体的第一个调用 —— 在这里掐断, 前面的 setup 已经全跑完了.
    monkeypatch.setattr(main, "on_death_screen",
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})


def test_worker_stdin_watcher_resets_keyboard_on_eof(monkeypatch):
    """GUI 关掉 worker 的 stdin 管道 = 停止请求. 打包成 console=False 之后
    CTRL_BREAK / SIGTERM 都不一定送得到, 这条 EOF 路径是唯一保证"停止"时
    space+WASD 会被松开的机制 —— 它坏了, 每次停止都把角色卡在按住状态.
    """
    order = []
    monkeypatch.setattr(main, "reset_keyboard", lambda: order.append("reset"))
    monkeypatch.setattr(main.os, "_exit",
                        lambda code: (order.append(("exit", code)),
                                      (_ for _ in ()).throw(SystemExit(code))))
    monkeypatch.setattr(main.sys, "stdin", io.StringIO(""))   # 立刻 EOF

    with pytest.raises(SystemExit):
        main._worker_stdin_watch()

    assert order == ["reset", ("exit", 0)]


def test_install_worker_stdin_watcher_noop_without_stdin(monkeypatch):
    """打包后的 GUI 直接双击 exe 跑 worker 调试时 sys.stdin 可能是 None ——
    那种情况下别起线程(读 None 会直接抛)."""
    started = []

    class _FakeThreading:
        @staticmethod
        def Thread(*a, **k):
            started.append(1)
            raise AssertionError("stdin 是 None 时不该起看门线程")

    monkeypatch.setattr(main.sys, "stdin", None)
    monkeypatch.setattr(main, "threading", _FakeThreading)
    main._install_worker_stdin_watcher()
    assert started == []


def test_update_mythic_latch_locks_on_target():
    assert main._update_mythic_latch(False, 0, True, 3) == (True, 0)
    assert main._update_mythic_latch(True, 2, True, 3) == (True, 0)   # miss counter resets


def test_update_mythic_latch_stays_off_without_target():
    assert main._update_mythic_latch(False, 0, False, 3) == (False, 0)


def test_update_mythic_latch_counts_misses_then_releases():
    latched, misses = True, 0
    latched, misses = main._update_mythic_latch(latched, misses, False, 3)
    assert (latched, misses) == (True, 1)
    latched, misses = main._update_mythic_latch(latched, misses, False, 3)
    assert (latched, misses) == (True, 2)
    latched, misses = main._update_mythic_latch(latched, misses, False, 3)
    assert (latched, misses) == (False, 0)
