import inspect
import io

import pytest

import main


def test_apply_worker_config_reads_active_slice(monkeypatch):
    applied = {}
    monkeypatch.setattr(main, "apply_map", lambda name: applied.setdefault("map", name))
    cfg = {"version": 2, "active": {
        "map": "ocean", "location": [11, 22], "farming_area": [[1, 2], [3, 4]],
        "farming_duration": 120, "consecutive_short_round_limit": 5,
        "enemy_ai_enabled": False, "auto_switch_server": False,
    }}
    w = main._apply_worker_config(cfg)
    assert applied["map"] == "ocean"
    assert w["location"] == (11, 22)
    assert w["farming_area"] == [(1, 2), (3, 4)]
    assert w["farming_duration"] == 120
    assert w["short_round_limit"] == 5
    assert w["enemy_ai_enabled"] is False
    assert w["auto_switch_server"] is False


def test_apply_worker_config_falls_back_to_flat_when_no_active(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    cfg = {"map": "anthell", "location": [1, 1], "farming_area": [[0, 0], [2, 2]],
           "farming_duration": 99, "consecutive_short_round_limit": 4,
           "enemy_ai_enabled": True, "auto_switch_server": True}
    w = main._apply_worker_config(cfg)
    assert w["farming_duration"] == 99
    assert w["short_round_limit"] == 4


def test_apply_worker_config_fills_missing_from_defaults(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    w = main._apply_worker_config({"version": 2, "active": {"map": "desert"}})
    import app_config
    assert w["farming_duration"] == app_config.DEFAULTS["farming_duration"]
    assert w["location"] == tuple(app_config.DEFAULTS["location"])


def test_maybe_scan_enemies_disabled_never_touches_enemy_detect(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(AssertionError("不该扫描")))
    decision, dets, last, scanned = main._maybe_scan_enemies(
        False, 1000.0, 0.0, ("chase", "x"), ["old"])
    assert decision == ("wander", None)
    assert dets == []
    assert last == 0.0
    assert scanned is False


def test_maybe_scan_enemies_throttled_returns_prev(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(AssertionError("还没到扫描间隔")))
    prev, prev_dets = ("flee", [(1, 2)]), ["d1", "d2"]
    decision, dets, last, scanned = main._maybe_scan_enemies(True, 0.1, 0.0, prev, prev_dets)
    assert decision is prev
    assert dets is prev_dets
    assert last == 0.0
    assert scanned is False


def test_maybe_scan_enemies_scans_when_due(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies", lambda **k: ["det"])
    monkeypatch.setattr(main.enemy_detect, "select_action",
                        lambda dets, **k: ("chase", "target", 250, []))
    now = main.ENEMY_SCAN_INTERVAL + 1.0
    decision, dets, last, scanned = main._maybe_scan_enemies(
        True, now, 0.0, ("wander", None), [])
    assert decision == ("chase", "target", 250, [])
    assert dets == ["det"]
    assert last == now
    assert scanned is True


def test_maybe_scan_enemies_scan_error_degrades_to_wander(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("model missing")))
    now = main.ENEMY_SCAN_INTERVAL + 1.0
    decision, dets, last, scanned = main._maybe_scan_enemies(
        True, now, 0.0, ("wander", None), ["old"])
    assert decision == ("wander", None)
    assert dets == []
    assert last == now
    assert scanned is True   # 尝试过一次观测 —— 算一次 miss


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


def test_main_exposes_mythic_wiring():
    assert hasattr(main, "_drive_and_check_stall")
    assert isinstance(main.MYTHIC_LATCH_ENABLED, bool)
    for name in ("MYTHIC_ENGAGE_PX", "MYTHIC_RELEASE_PX", "MYTHIC_RELEASE_MISSES",
                 "MYTHIC_STRAFE_RADIUS", "MYTHIC_CACTUS_HOLD_PX",
                 "MYTHIC_STRAFE_K_RADIAL"):
        assert isinstance(getattr(main, name), (int, float))


def test_mythic_miss_counter_only_advances_on_fresh_scan():
    """节流 tick (scanned=False) 不能推进 miss 计数 —— 循环里 mythic 分支每 tick
    都跑, 但只有真扫描过的 tick 才是一次新观测. 少了这道门, 3-miss 释放在快机器上
    会缩成 ~2 (节流 tick 拿同一份缓存检测重复扣数)."""
    latched, misses = True, 0

    def tick(scanned, has_target):
        nonlocal latched, misses
        if scanned:
            latched, misses = main._update_mythic_latch(latched, misses, has_target, 3)

    tick(scanned=True, has_target=False)      # 真扫描 miss 1
    assert (latched, misses) == (True, 1)
    tick(scanned=False, has_target=False)     # 节流 tick —— 不推进
    assert (latched, misses) == (True, 1)
    tick(scanned=True, has_target=False)      # 真扫描 miss 2
    assert (latched, misses) == (True, 2)
    tick(scanned=False, has_target=False)     # 节流 tick —— 不推进
    assert (latched, misses) == (True, 2)
    tick(scanned=True, has_target=False)      # 真扫描 miss 3 —— 解锁
    assert (latched, misses) == (False, 0)


# ── move_to_position 的 on_tick 钩子 (wander 腿途中让外层索敌) ──────────────

def _stub_move_env(monkeypatch, pos=(10, 10), dead=False, menu=False):
    """把 move_to_position 的所有实机依赖打桩掉, 只留纯逻辑."""
    import types
    monkeypatch.setattr(main, "get_player_position", lambda *a, **k: pos, raising=False)
    monkeypatch.setattr(main, "on_death_screen", lambda: dead, raising=False)
    monkeypatch.setattr(main, "on_start_screen", lambda: menu, raising=False)
    monkeypatch.setattr(main, "reset_keyboard", lambda: None, raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: None)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)


def test_move_to_position_on_tick_aborts_leg_with_its_signal(monkeypatch):
    # 玩家位置恒定 (永远到不了目标), on_tick 第 3 次返回 "enemy" —— 应在那一 tick
    # 立刻收手, 返回该信号, 早于 max_attempts 和 stall 判定.
    _stub_move_env(monkeypatch, pos=(10, 10))
    calls = []

    def on_tick(pos):
        calls.append(pos)
        return "enemy" if len(calls) >= 3 else None

    result = main.move_to_position((10, 10), (999, 999), max_attempts=50, on_tick=on_tick)
    assert result == "enemy"
    assert len(calls) == 3


def test_move_to_position_on_tick_falsy_does_not_abort(monkeypatch):
    # on_tick 从不返回信号 —— 腿正常按老逻辑走完 (位置恒定 → stall → "stuck"),
    # 钩子每 tick 都被调到.
    _stub_move_env(monkeypatch, pos=(10, 10))
    ticks = []
    result = main.move_to_position((10, 10), (999, 999), max_attempts=50,
                                   on_tick=lambda p: ticks.append(p))
    assert result == "stuck"
    assert len(ticks) >= 5


def test_move_to_position_without_on_tick_unchanged(monkeypatch):
    # 不传 on_tick (默认 None) —— 行为跟以前完全一样: 已在 5px 内 → 立刻到达.
    _stub_move_env(monkeypatch, pos=(500, 500))
    assert main.move_to_position((500, 500), (502, 501), max_attempts=5) is True


def test_run_worker_calls_ensure_invert_attack_once(monkeypatch):
    monkeypatch.setattr(main.cdp_bridge, "is_dedicated_chrome_ready", lambda: True)
    monkeypatch.setattr(main, "create_overlay", lambda *a, **k: _StubOverlay())
    monkeypatch.setattr(main, "overlay", None, raising=False)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
    })
    calls = []
    monkeypatch.setattr(main.florr_settings, "ensure_invert_attack_on",
                        lambda ej, *a, **k: calls.append(ej) or ("turned_on", ""))
    monkeypatch.setattr(main, "on_death_screen",
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert len(calls) == 1
    assert calls[0] is main.cdp_bridge.eval_js


def test_run_worker_survives_invert_attack_failure(monkeypatch):
    """ensure_invert_attack_on 返回 failed 时 worker 照常进主循环, 不 SystemExit."""
    monkeypatch.setattr(main.cdp_bridge, "is_dedicated_chrome_ready", lambda: True)
    ov = _StubOverlay()
    warned = []
    ov.update = lambda **kw: warned.append(kw.get("message"))
    monkeypatch.setattr(main, "create_overlay", lambda *a, **k: ov)
    monkeypatch.setattr(main, "overlay", None, raising=False)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
    })
    monkeypatch.setattr(main.florr_settings, "ensure_invert_attack_on",
                        lambda ej, *a, **k: ("failed", "addr-out-of-range"))
    monkeypatch.setattr(main, "on_death_screen",
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):   # 到了主循环 = 没被 failed 掐死
        main.run_worker({})
    assert any(m and "反转攻击键" in m for m in warned)
