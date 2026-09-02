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


def test_apply_worker_config_maps_config_map_to_biome_key(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    w = main._apply_worker_config({"version": 2, "active": {"map": "anthell"}})
    assert w["biome"] == "ant_hell"        # config anthell -> index key ant_hell
    w2 = main._apply_worker_config({"version": 2, "active": {"map": "ocean"}})
    assert w2["biome"] == "ocean"
    w3 = main._apply_worker_config({"version": 2, "active": {"map": "desert"}})
    assert w3["biome"] == "desert"


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


def test_lock_biome_success_first_try(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "switch_server", lambda b: seen.append(b) or "srv-1")
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    assert main._lock_biome("ocean") is True
    assert seen == ["ocean"]


def test_lock_biome_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("cdp boom")
        return "srv-9"

    monkeypatch.setattr(main, "switch_server", flaky)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    assert main._lock_biome("desert") is True
    assert calls["n"] == 3


def test_lock_biome_all_attempts_fail_is_warn_only(monkeypatch):
    calls = {"n": 0}

    def always_fail(b):
        calls["n"] += 1
        raise RuntimeError("network down")

    monkeypatch.setattr(main, "switch_server", always_fail)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    assert main._lock_biome("desert") is False        # no raise
    assert calls["n"] == main._BIOME_LOCK_RETRIES


def test_lock_biome_constants_are_numbers():
    for name in ("_BIOME_LOCK_RETRIES", "_BIOME_LOCK_RETRY_SLEEP", "_BIOME_RECONNECT_SLEEP"):
        assert isinstance(getattr(main, name), (int, float))


def test_wait_for_start_menu_returns_true_when_menu_present(monkeypatch):
    monkeypatch.setattr(main, "on_start_screen", lambda: True)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    assert main._wait_for_start_menu(timeout=5) is True


def test_wait_for_start_menu_polls_until_menu_appears(monkeypatch):
    seq = iter([False, False, True])
    monkeypatch.setattr(main, "on_start_screen", lambda: next(seq, True))
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    assert main._wait_for_start_menu(timeout=5) is True


def test_wait_for_start_menu_times_out(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(main.time, "time", lambda: clock[0])
    monkeypatch.setattr(main.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(main, "on_start_screen", lambda: False)
    assert main._wait_for_start_menu(timeout=3, interval=0.5) is False
    assert clock[0] >= 3


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
        "biome": "desert",
    })
    monkeypatch.setattr(main, "_lock_biome", lambda b: True)   # 本测跟锁生态区无关
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


def _stub_run_worker_env(monkeypatch, overlay=None):
    monkeypatch.setattr(main.cdp_bridge, "is_dedicated_chrome_ready", lambda: True)
    monkeypatch.setattr(main, "create_overlay",
                        lambda *a, **k: overlay or _StubOverlay())
    monkeypatch.setattr(main, "overlay", None, raising=False)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
        "biome": "desert",
        "enter_game_swap": {"enabled": False, "mod": "none", "digit": "1"},
        "reach_area_swap": {"enabled": False, "mod": "none", "digit": "1"},
    })
    monkeypatch.setattr(main, "switch_server", lambda *a, **k: "stub-srv")
    monkeypatch.setattr(main, "on_death_screen", lambda: False)
    monkeypatch.setattr(main, "on_start_screen", lambda: False)
    monkeypatch.setattr(main, "on_guest_screen", lambda: False)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)


def test_run_worker_reasserts_florr_toggles_at_startup_and_each_round(monkeypatch):
    _stub_run_worker_env(monkeypatch)
    calls = []
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: calls.append((addr, want)) or ("unchanged", ""))
    # 掐在寻路 —— 它在"每轮重写"之后, 所以第 1 轮那次也算进去
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    # 启动 1 次 + 第 1 轮进游戏后 1 次 = _reassert_florr_toggles 调 2 次
    # 每次内部对 attack + defense 各调一次 ensure_flag = 4 次
    assert len(calls) == 4
    A, D = main.florr_settings.INVERT_ATTACK_ADDR, main.florr_settings.INVERT_DEFENSE_ADDR
    # 默认 cfg={} → invert_attack 默认 True → want 1; invert_defense 默认 False → want 0
    assert calls == [(A, 1), (D, 0), (A, 1), (D, 0)]


def test_run_worker_toggle_wants_follow_cfg(monkeypatch):
    _stub_run_worker_env(monkeypatch)
    calls = []
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: calls.append((addr, want)) or ("unchanged", ""))
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({"invert_attack": False, "invert_defense": True})
    A, D = main.florr_settings.INVERT_ATTACK_ADDR, main.florr_settings.INVERT_DEFENSE_ADDR
    assert calls == [(A, 0), (D, 1), (A, 0), (D, 1)]


def test_run_worker_survives_toggle_failure(monkeypatch):
    """ensure_flag 返回 failed 时 worker 照常进主循环, 不 SystemExit, 悬浮窗警告."""
    ov = _StubOverlay()
    warned = []
    ov.update = lambda **kw: warned.append(kw.get("message"))
    _stub_run_worker_env(monkeypatch, overlay=ov)
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: ("failed", "not-bool:9"))
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):   # 到了主循环 = 没被 failed 掐死
        main.run_worker({})
    assert any(m and "反转" in m for m in warned)


def test_reassert_florr_toggles_returns_per_flag_status(monkeypatch):
    seen = []

    def fake(ej, addr, want):
        seen.append((addr, want))
        return ("changed", "") if addr == main.florr_settings.INVERT_ATTACK_ADDR else ("unchanged", "")

    monkeypatch.setattr(main.florr_settings, "ensure_flag", fake)
    out = main._reassert_florr_toggles(True, False)
    assert out == {"attack": "changed", "defense": "unchanged"}
    A, D = main.florr_settings.INVERT_ATTACK_ADDR, main.florr_settings.INVERT_DEFENSE_ADDR
    assert seen == [(A, 1), (D, 0)]


def test_run_worker_locks_biome_on_title_before_clicking_start(monkeypatch):
    """锁生态区必须在标题页、click_start_game() 之前 —— 反过来(进局后 forceServerID)
    会把人踢回标题页, 形成死循环. 顺序: _lock_biome -> _wait_for_start_menu ->
    click_start_game."""
    _stub_run_worker_env(monkeypatch)
    events = []
    monkeypatch.setattr(main, "_lock_biome", lambda b: events.append(("lock", b)) or True)
    monkeypatch.setattr(main, "_wait_for_start_menu",
                        lambda *a, **k: events.append("wait") or True)
    monkeypatch.setattr(main, "on_start_screen", lambda: True)
    monkeypatch.setattr(main, "click_start_game", lambda: events.append("click") or True)
    monkeypatch.setattr(main, "_reassert_florr_toggles",
                        lambda *a, **k: {"attack": "unchanged", "defense": "unchanged"})
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert events == [("lock", "desert"), "wait", "click"]


def test_run_worker_does_not_lock_biome_when_not_on_start_screen(monkeypatch):
    _stub_run_worker_env(monkeypatch)   # on_start_screen 恒 False
    locks = []
    monkeypatch.setattr(main, "_lock_biome", lambda b: locks.append(b) or True)
    monkeypatch.setattr(main, "_reassert_florr_toggles",
                        lambda *a, **k: {"attack": "unchanged", "defense": "unchanged"})
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert locks == []      # 锁只发生在标题页(点开始前), 不在开局菜单就一次都不锁


def test_run_worker_switch_server_uses_configured_biome(monkeypatch):
    _stub_run_worker_env(monkeypatch)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 9999,
        "short_round_limit": 1, "enemy_ai_enabled": False, "auto_switch_server": True,
        "biome": "ocean",
        "enter_game_swap": "none", "reach_area_swap": "none",
    })
    monkeypatch.setattr(main, "_lock_biome", lambda b: True)
    monkeypatch.setattr(main, "_reassert_florr_toggles",
                        lambda *a, **k: {"attack": "unchanged", "defense": "unchanged"})
    monkeypatch.setattr(main, "lazy_theta_pathing", lambda *a, **k: False)  # 没到区 -> 短局
    sw = []

    def rec(*a, **k):
        sw.append(a)
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "switch_server", rec)
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert sw == [("ocean",)]      # switch_server 收到配置里的 biome, 不是空参


# ── 未登录标题页: run_worker 自动点「以游客身份游玩」──────────────────────

def test_run_worker_clicks_play_as_guest_when_on_guest_screen(monkeypatch):
    """停在未登录登录选择页时, 启动阶段 + 第 1 轮各点一次「以游客身份游玩」."""
    _stub_run_worker_env(monkeypatch)
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: ("unchanged", ""))
    monkeypatch.setattr(main, "on_guest_screen", lambda: True)
    clicks = []
    monkeypatch.setattr(main, "click_play_as_guest", lambda: clicks.append(1))
    # 掐在寻路 —— 启动那次 + 第 1 轮那次都已经跑过了
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert len(clicks) == 2                       # 启动前 1 次 + 第 1 轮顶部 1 次


def test_run_worker_never_clicks_guest_when_not_on_guest_screen(monkeypatch):
    """登录过的 profile 的常态: on_guest_screen 恒 False, 一次都不点."""
    _stub_run_worker_env(monkeypatch)
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: ("unchanged", ""))
    monkeypatch.setattr(main, "on_guest_screen", lambda: False)
    monkeypatch.setattr(main, "click_play_as_guest",
                        lambda: pytest.fail("不在游客页不该点「以游客身份游玩」"))
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})


def test_apply_worker_config_reads_swap_keys(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    e = {"enabled": True, "mod": "k", "digit": "3"}
    r = {"enabled": True, "mod": "none", "digit": "7"}
    cfg = {"version": 2, "active": {"map": "desert",
                                    "enter_game_swap": e, "reach_area_swap": r}}
    w = main._apply_worker_config(cfg)
    assert w["enter_game_swap"] == e
    assert w["reach_area_swap"] == r


def test_apply_worker_config_swap_keys_default_disabled(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    off = {"enabled": False, "mod": "none", "digit": "1"}
    w = main._apply_worker_config({"version": 2, "active": {"map": "desert"}})
    assert w["enter_game_swap"] == off
    assert w["reach_area_swap"] == off


def _swap_env(monkeypatch, *, enter="k", reach="l"):
    """_stub_run_worker_env + 记录 press_swap 调用 + _apply_worker_config 带 swap 键.

    enter/reach 是不透明哨兵 —— main.loadout_swap.press_swap 被 stub 成"记下参数",
    真按键逻辑(和弦 / 对象形状)由 test_loadout_swap.py 覆盖. 这里只关心"哪个字段的
    值被传给了 press_swap、顺序、以及 swap_this_round 门控".
    """
    _stub_run_worker_env(monkeypatch)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
        "biome": "desert",
        "enter_game_swap": enter, "reach_area_swap": reach,
    })
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: ("unchanged", ""))
    seen = []
    monkeypatch.setattr(main.loadout_swap, "press_swap", lambda spec: seen.append(spec))
    return seen


def test_run_worker_presses_enter_swap_on_entry(monkeypatch):
    # 第 1 轮总会切 (round_count == 1); 这里第 1 轮就在寻路处掐断, 只证明
    # "进了游戏 → 按 enter swap", 没到区域 → reach 不按.
    seen = _swap_env(monkeypatch, enter="k", reach="l")
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert seen == ["k"]                 # 进游戏切换按了; 没到区域, reach 没按


def test_run_worker_presses_reach_swap_on_arrival(monkeypatch):
    seen = _swap_env(monkeypatch, enter="k", reach="l")
    calls = {"n": 0}

    def fake_path(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "lazy_theta_pathing", fake_path)
    monkeypatch.setattr(main, "auto_farming",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert seen == ["k", "l"]            # enter 先, 到区域后 reach


def test_run_worker_skips_reach_swap_when_pathing_fails(monkeypatch):
    seen = _swap_env(monkeypatch, enter="k", reach="l")
    calls = {"n": 0}

    def fake_path(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "lazy_theta_pathing", fake_path)
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert "l" not in seen               # 没到区域 → reach 永不触发
    assert seen == ["k"]                 # 第 1 轮进游戏切一次; 第 2 轮没重进游戏
                                         # (_stub 里 on_start/on_death 恒 False) → 不切


def test_run_worker_skips_swaps_on_survived_continuation_round(monkeypatch):
    # 一命跑满 farming_duration 没死: 下一轮不过 on_death/on_start 分支, florr 没
    # 重置 loadout, 不该再切. auto_farming 第 1 次正常返回, 第 2 次掐断.
    seen = _swap_env(monkeypatch, enter="k", reach="l")
    monkeypatch.setattr(main, "lazy_theta_pathing", lambda *a, **k: True)
    calls = {"n": 0}

    def fake_farm(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "auto_farming", fake_farm)
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert seen == ["k", "l"]            # 只有第 1 轮切; 第 2 轮存活续命轮不切


def test_run_worker_swaps_again_after_real_respawn(monkeypatch):
    # 每轮都真的过 on_start_screen 分支 (= 真重进游戏) → 每轮都该切 enter swap,
    # 证明 gate 是"这轮真进了游戏"而不是"仅第 1 轮".
    seen = _swap_env(monkeypatch, enter="k", reach="l")
    monkeypatch.setattr(main, "click_start_game", lambda: None)
    # 锁生态区那段会额外反复轮询 on_start_screen —— stub 掉, 让计数器只被主循环
    # 体每轮那一次 on_start_screen() 推进.
    monkeypatch.setattr(main, "_lock_biome", lambda *a, **k: True)
    monkeypatch.setattr(main, "_wait_for_start_menu", lambda *a, **k: True)
    starts = {"n": 0}

    def fake_start_screen():
        starts["n"] += 1
        return starts["n"] <= 2          # 第 1、2 轮都在开局菜单

    monkeypatch.setattr(main, "on_start_screen", fake_start_screen)
    calls = {"n": 0}

    def fake_path(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "lazy_theta_pathing", fake_path)
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert seen == ["k", "k"]            # 两轮都真重进了游戏 → 两轮都切 enter
