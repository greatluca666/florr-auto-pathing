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


# ── ensure_zoom_for_rarity (开刷前滚轮拉近相机, 让 sample_rarity 读得出稀有度) ──

def _stub_zoom_env(monkeypatch, thick_seq):
    """thick_seq: list of lists — successive scan_bar_thickness() return values
    (last entry repeats once exhausted). Returns a dict recording calls."""
    import types
    calls = {"scan": 0, "scroll": [], "sleep": 0.0, "moveto": []}
    seq = list(thick_seq)

    def fake_scan(**k):
        i = min(calls["scan"], len(seq) - 1)
        calls["scan"] += 1
        return list(seq[i])

    monkeypatch.setattr(main.enemy_detect, "scan_bar_thickness", fake_scan)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: calls["moveto"].append(a))
    # zoom 滚轮走 CDP (main.cdp_bridge.scroll_wheel), 不是 pyautogui.scroll
    monkeypatch.setattr(main.cdp_bridge, "scroll_wheel",
                        lambda amt, *a, **k: calls["scroll"].append(amt))
    clock = {"t": 0.0}
    monkeypatch.setattr(main.time, "time", lambda: clock["t"])

    def fake_sleep(s):
        clock["t"] += s
        calls["sleep"] += s

    monkeypatch.setattr(main.time, "sleep", fake_sleep)
    return calls


def test_ensure_zoom_disabled_returns_immediately(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[9, 9]])
    assert main.ensure_zoom_for_rarity(False) is False
    assert calls["scan"] == 0
    assert calls["scroll"] == []


def test_ensure_zoom_reaches_target(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[2, 2], [3, 3], [4, 4]])
    assert main.ensure_zoom_for_rarity(True) is True
    assert len(calls["scroll"]) == 2          # 2 scrolls, 3rd scan median hits 4


def test_ensure_zoom_already_ok_no_scroll(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[5, 6]])   # median 5.5 >= 4 first scan
    assert main.ensure_zoom_for_rarity(True) is True
    assert calls["scroll"] == []


def test_ensure_zoom_scroll_cap(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[2, 2]])   # never improves
    assert main.ensure_zoom_for_rarity(True) is False
    # FIX 1: cap give-up now restores the zoom, so it's ZOOM_MAX_SCROLLS
    # forward scrolls + one restore scroll.
    assert len(calls["scroll"]) == main.ZOOM_MAX_SCROLLS + 1


def test_ensure_zoom_waits_for_mobs_then_succeeds(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[], [], [4, 4]])
    assert main.ensure_zoom_for_rarity(True) is True
    assert calls["scroll"] == []              # never scrolled during empty rounds
    assert calls["sleep"] >= 4.0


def test_ensure_zoom_wait_cap(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[]])       # always empty
    assert main.ensure_zoom_for_rarity(True) is False
    assert calls["scroll"] == []
    assert calls["sleep"] >= main.ZOOM_WAIT_CAP


def test_ensure_zoom_flips_scroll_direction(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[3, 3], [2, 2], [4, 4]])
    assert main.ensure_zoom_for_rarity(True) is True
    assert calls["scroll"][0] == main.ZOOM_SCROLL_AMOUNT
    assert calls["scroll"][1] == -main.ZOOM_SCROLL_AMOUNT


def test_ensure_zoom_bails_and_restores_when_both_directions_regress(monkeypatch):
    # regress -> flip -> STILL regress: give up, and undo every scroll applied.
    calls = _stub_zoom_env(monkeypatch, [[3, 3], [2, 2], [1, 1]])
    assert main.ensure_zoom_for_rarity(True) is False
    assert sum(calls["scroll"]) == 0                       # net zoom restored
    assert calls["scroll"][0] == main.ZOOM_SCROLL_AMOUNT   # forward...
    assert -main.ZOOM_SCROLL_AMOUNT in calls["scroll"]     # ...then a flip


def test_ensure_zoom_restores_on_scroll_cap(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[2, 2]])          # never improves
    assert main.ensure_zoom_for_rarity(True) is False
    assert len(calls["scroll"]) == main.ZOOM_MAX_SCROLLS + 1   # + restore
    assert sum(calls["scroll"]) == 0                           # net zoom restored


def test_ensure_zoom_wait_cap_bounds_afk(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[]])
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: True)
    assert main.ensure_zoom_for_rarity(True) is False
    assert calls["sleep"] >= main.ZOOM_WAIT_CAP     # top-of-loop cap fires
    assert calls["scroll"] == []


def test_ensure_zoom_recenters_mouse_on_entry(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[]])       # no mob -> wait-cap out, no scroll
    assert main.ensure_zoom_for_rarity(True) is False
    assert calls["scroll"] == []
    assert (main.SCREEN_WIDTH // 2, main.SCREEN_HEIGHT // 2) in calls["moveto"]


def test_ensure_zoom_success_does_not_restore(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[2, 2], [4, 4]])
    assert main.ensure_zoom_for_rarity(True) is True
    assert sum(calls["scroll"]) == main.ZOOM_SCROLL_AMOUNT   # one forward, kept


def test_move_gain_for_zoom_none_and_nonpositive_give_base():
    assert main._move_gain_for_zoom(None) == main.MOVE_EXTEND_GAIN
    assert main._move_gain_for_zoom(0) == main.MOVE_EXTEND_GAIN
    assert main._move_gain_for_zoom(-3) == main.MOVE_EXTEND_GAIN


def test_move_gain_for_zoom_scales_inversely_with_thickness():
    # thickness == baseline -> scale 1
    assert main._move_gain_for_zoom(main.ZOOM_BASELINE_THICK) == main.MOVE_EXTEND_GAIN
    # thickness 2x baseline -> half gain
    assert main._move_gain_for_zoom(2 * main.ZOOM_BASELINE_THICK) == main.MOVE_EXTEND_GAIN * 0.5


def test_move_gain_for_zoom_has_a_floor():
    # a huge thickness reading must not shrink the gain to ~nothing
    g = main._move_gain_for_zoom(1000)
    assert g == main.MOVE_EXTEND_GAIN * 0.3


def test_leaving_area():
    area = [(7, 3), (52, 63)]
    assert main._leaving_area((25, 20), area) is False
    assert main._leaving_area((25, 120), area) is True
    assert main._leaving_area((60, 20), area) is True


def test_move_to_position_extend_gain_scales_mouse_offset(monkeypatch):
    # same start/target, different extend_gain -> mouse offset from centre scales
    # linearly (pick dist small enough that dist*gain stays under the 500 clamp).
    import types
    monkeypatch.setattr(main, "get_player_position", lambda *a, **k: (0, 0), raising=False)
    monkeypatch.setattr(main, "on_death_screen", lambda: False, raising=False)
    monkeypatch.setattr(main, "on_start_screen", lambda: False, raising=False)
    monkeypatch.setattr(main, "reset_keyboard", lambda: None, raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    seen = []
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: seen.append(a))

    cx = main.SCREEN_WIDTH // 2
    # target 10 units to the +x of the player at (0,0); dist = 10.
    # run one tick each: on the first tick last_dist is None so no arrival/stall
    # short-circuit; on_tick returns a sentinel to stop after that tick.
    def one_tick(_pos):
        return "stop"

    seen.clear()
    main.move_to_position((0, 0), (10, 0), max_attempts=5, on_tick=one_tick, extend_gain=10)
    off_a = seen[0][0] - cx        # x offset from centre, gain 10 -> dist*gain = 100

    seen.clear()
    main.move_to_position((0, 0), (10, 0), max_attempts=5, on_tick=one_tick, extend_gain=40)
    off_b = seen[0][0] - cx        # gain 40 -> dist*gain = 400

    assert off_b == pytest.approx(off_a * 4)


def test_move_to_position_extend_gain_none_matches_base_constant(monkeypatch):
    import types
    for stub in ("get_player_position", "on_death_screen", "on_start_screen", "reset_keyboard"):
        pass
    monkeypatch.setattr(main, "get_player_position", lambda *a, **k: (0, 0), raising=False)
    monkeypatch.setattr(main, "on_death_screen", lambda: False, raising=False)
    monkeypatch.setattr(main, "on_start_screen", lambda: False, raising=False)
    monkeypatch.setattr(main, "reset_keyboard", lambda: None, raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    seen = []
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: seen.append(a))

    def one_tick(_pos):
        return "stop"

    seen.clear()
    main.move_to_position((0, 0), (10, 0), max_attempts=5, on_tick=one_tick, extend_gain=None)
    off_none = seen[0][0]
    seen.clear()
    main.move_to_position((0, 0), (10, 0), max_attempts=5, on_tick=one_tick,
                          extend_gain=main.MOVE_EXTEND_GAIN)
    off_const = seen[0][0]
    assert off_none == off_const
