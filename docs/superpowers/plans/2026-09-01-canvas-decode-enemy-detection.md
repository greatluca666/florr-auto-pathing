# Canvas-decode Enemy Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `enemy_detect.scan_enemies`'s YOLO+`sample_rarity` data source with canvas draw-call decoding (vendored from `/Users/macmima1234/florragent`) — exact mob screen position / name / rarity, zoom-independent, no model — and delete the now-dead zoom gate.

**Architecture:** New `canvas_decode.py` + `canvas_hook.js` vendored from florragent (pure, no CDP). Two new `cdp_bridge` funcs inject the hook and drain its log over raw CDP. `scan_enemies` decodes the newest complete frame into the existing detection-dict shape. `select_action` / Mythic latch / chase-gate / flee are untouched. The zoom gate (`ensure_zoom_for_rarity`, `scroll_wheel`, `ZOOM_*`) and `debug_enemy_detect.py` are removed.

**Tech Stack:** Python 3.11, pytest. Drops `ultralytics` / `cv2` / `numpy` from `enemy_detect.py` (still used elsewhere in the repo — do not uninstall). Uses `cdp_bridge`'s existing websocket CDP transport.

## Global Constraints

- Tests run with `./venv/bin/python -m pytest` (`venv/`, NOT `.venv/`).
- Vendored source lives at `/Users/macmima1234/florragent/scripts/canvas_hook.js`, `/Users/macmima1234/florragent/scripts/canvas_decode.py`, `/Users/macmima1234/florragent/tests/conftest.py`, `/Users/macmima1234/florragent/tests/test_canvas_decode.py`. Copy verbatim where the plan says "verbatim"; do not paraphrase.
- The repo has NO `tests/` dir and NO `conftest.py` — test files are flat `test_*.py` in the repo root.
- Detection-dict shape consumed by `select_action` / Mythic / `_maybe_scan_enemies`: `{"species": str, "rarity": str, "screen_pos": (x, y), "bbox": (x1,y1,x2,y2), "confidence": float}`.
- `enemy_detect.RARITY_ORDER = ["Common","Unusual","Rare","Epic","Legendary","Mythic","Ultra","Super","Eternal","Unique"]` (index = rank). Keep it.
- Rarity colour → rank (from florragent `obs_encoder.RARITY_RANK_BY_COLOR`, hex values identical to `enemy_detect.RARITY_COLORS`):
  `#7EEF6D`→0 `#FFE65D`→1 `#4D52E3`→2 `#861FDE`→3 `#DE1F1F`→4 `#1FDBDE`→5 `#FF2B75`→6 `#2BFFA3`→7 `#555555`→9.
- Desert species slugs (unchanged): `scorpion`, `beetle`, `cactus`, `sandstorm`, `sand_centipede`, `soldier_fire_ant`.
- florr client language is **English** — mob `name` text is English Title Case.
- Chinese comments / print strings matching surrounding style. English commit messages, Conventional Commits, trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- `cdp_bridge._send_cdp_command(method, params=None, timeout=5)` returns the raw CDP response dict `{"id": N, "result": {...}}`. For `Runtime.evaluate` with `returnByValue: True` the value is at `resp["result"]["result"]["value"]` (double-nested — `scroll_wheel` documents this).

---

## Task 1: Vendor `canvas_hook.js` + `canvas_decode.py` + fixtures + `test_canvas_decode.py`

**Files:**
- Create: `canvas_hook.js` (repo root)
- Create: `canvas_decode.py` (repo root)
- Create: `canvas_frame_fixtures.py` (repo root — NOT `test_`-prefixed, so pytest doesn't collect it)
- Create: `test_canvas_decode.py`

**Interfaces produced:**
- `canvas_decode.group_by_frame(records) -> dict[int, list[dict]]`
- `canvas_decode.camera_from_frame(records) -> {"zoom": float, "player_world": (x,y), "player_screen": (x,y)}` — raises `ValueError` if any field can't be read.
- `canvas_decode.mobs_from_frame(records, camera) -> list[{"name","rarity","rarity_color","hp","sx","sy","x","y"}]`
- `canvas_decode.screen_to_world(x, y, camera) -> (wx, wy)`
- `canvas_frame_fixtures`: `ZOOM`, `arc_rec`, `minimap_rec`, `healthbar_recs`, `text_rec`, `nameplate`, `player_recs`, `gameplay_frame`.

- [ ] **Step 1: Vendor `canvas_hook.js`**

`cp /Users/macmima1234/florragent/scripts/canvas_hook.js ./canvas_hook.js` — byte-identical, no edits.

- [ ] **Step 2: Vendor `canvas_decode.py` (trimmed)**

Copy `/Users/macmima1234/florragent/scripts/canvas_decode.py` to `./canvas_decode.py`, then **delete** these functions and their docstrings (keep everything else verbatim): `other_players_from_frame`, `player_from_frame`, `entities_from_frame`, `inventory_slots_from_frame`, `death_panel_continue_button`, `start_menu_start_button`, `decode_frame`. Keep: the module docstring, `import math / re / json`, `from pathlib import Path`, all module constants (`MINIMAP_MAX_SCALE`, `HEALTHBAR_BG/DAMAGE/SECONDARY`, `PLAYER_BODY_COLOR`, `ABSORB_FRACTION`, `PLAYER_RARITY_PATTERN`, `INVENTORY_UI_SCALE_TOL`, `SELF_DISAMBIGUATION_RADIUS`), `_is_player_block`, `load_ndjson`, `group_by_frame`, `_scale`, `_anchor`, `_is_minimap`, `_is_rotated`, `camera_from_frame`, `screen_to_world`, `_bar_width`, `_bar_blocks`, `_is_player_anchor`, `mobs_from_frame`.

Prepend this line right under the module docstring:

```python
# VENDORED from /Users/macmima1234/florragent/scripts/canvas_decode.py (2026-09-01). Trimmed to
# the camera + mob subset florr-auto-pathing's enemy_detect needs. Keep in sync with upstream.
```

If `flake8`/`pyflakes` in the repo flags an unused import after trimming (e.g. `ABSORB_FRACTION` / `INVENTORY_UI_SCALE_TOL` are only referenced by deleted funcs), delete those specific unused constants too. Otherwise leave them.

- [ ] **Step 3: Vendor the synthetic-frame builders**

Create `canvas_frame_fixtures.py`:

```python
"""Synthetic canvas draw-call records for tests — VENDORED from
/Users/macmima1234/florragent/tests/conftest.py (2026-09-01). Deterministic, no browser."""
import json

ZOOM = 0.7558333


def arc_rec(frame, x, y, r, color, anchor=None, scale=ZOOM):
    ax, ay = anchor if anchor else (x, y)
    return {"frame": frame, "op": "fill", "x": x, "y": y, "r": r,
            "bbox": [x - r, y - r, x + r, y + r], "n": 1, "fill": color,
            "stroke": None, "lw": None, "alpha": 1, "m": [scale, 0, 0, scale, ax, ay]}


def minimap_rec(frame, world_x, world_y, color="#FFE763", scale=0.0084):
    ox, oy = 1085.65, 0.74
    return {"frame": frame, "op": "fill", "x": ox + world_x * scale, "y": oy + world_y * scale,
            "r": 2.5, "bbox": None, "n": 1, "fill": color, "stroke": None, "lw": None,
            "alpha": 1, "m": [scale, 0, 0, scale, ox, oy]}


def healthbar_recs(frame, ax, ay, hp=1.0, width=60.0):
    out = []
    for color, lw, w in (("#222222", 10, width), ("#DD3434", 6, width), ("#75DD34", 7, width * hp)):
        out.append({"frame": frame, "op": "stroke", "x": ax, "y": ay, "r": None,
                    "bbox": [ax - width / 2, ay, ax - width / 2 + w, ay], "n": 2,
                    "fill": "#FFFFFF", "stroke": color, "lw": lw, "alpha": 1,
                    "m": [ZOOM, 0, 0, ZOOM, ax, ay]})
    return out


def text_rec(frame, ax, ay, text, color="#FFFFFF", scale=ZOOM):
    return {"frame": frame, "op": "text", "text": text, "x": ax, "y": ay, "fill": color,
            "alpha": 1, "m": [scale, 0, 0, scale, ax, ay]}


def nameplate(frame, ax, ay, name, rarity="Common", rarity_color="#7EEF6D", hp=1.0):
    """One mob's nameplate block in florr.io's draw order: bar, name x2, rarity x2."""
    return (healthbar_recs(frame, ax, ay, hp)
            + [text_rec(frame, ax - 34, ay + 39, name)] * 2
            + [text_rec(frame, ax + 8, ay + 60, rarity, rarity_color)] * 2)


def player_recs(frame, sx=600.0, sy=453.5):
    return [
        arc_rec(frame, sx, sy, 20.0, "#CFBB50", anchor=(sx, sy)),
        arc_rec(frame, sx, sy, 17.8, "#FFE763", anchor=(sx, sy)),
        arc_rec(frame, sx - 5.3, sy - 3.8, 3.4, "#111111", anchor=(sx - 5.3, sy - 3.8)),
        arc_rec(frame, sx + 5.3, sy - 3.8, 3.4, "#111111", anchor=(sx + 5.3, sy - 3.8)),
    ]


def gameplay_frame(frame, player_world=(5640.0, 6911.0), mobs=(), player_screen=(600.0, 453.5)):
    recs = list(player_recs(frame, *player_screen))
    for ax, ay, name, hp in mobs:
        recs += nameplate(frame, ax, ay, name, hp=hp)
        recs += [arc_rec(frame, ax, ay, 11.3, "#8AC255", anchor=(ax, ay))]
    recs += [minimap_rec(frame, *player_world)]
    return recs
```

- [ ] **Step 4: Write `test_canvas_decode.py`**

```python
import pytest

from canvas_decode import (
    group_by_frame, camera_from_frame, screen_to_world, mobs_from_frame,
)
from canvas_frame_fixtures import (
    ZOOM, arc_rec, minimap_rec, text_rec, healthbar_recs, nameplate, gameplay_frame,
)


def test_group_by_frame():
    recs = [arc_rec(0, 10, 10, 5, "red"), arc_rec(0, 20, 20, 5, "blue"), arc_rec(1, 10, 10, 5, "red")]
    frames = group_by_frame(recs)
    assert len(frames[0]) == 2 and len(frames[1]) == 1


def test_camera_read_directly_from_draw_calls():
    recs = gameplay_frame(0, player_world=(5640.0, 6911.0), mobs=[(800.0, -60.0, "Rock", 1.0)])
    cam = camera_from_frame(recs)
    assert abs(cam["zoom"] - ZOOM) < 1e-6
    assert abs(cam["player_world"][0] - 5640.0) < 0.5
    assert abs(cam["player_world"][1] - 6911.0) < 0.5
    assert cam["player_screen"] == (600.0, 453.5)


def test_camera_raises_without_minimap_dot():
    recs = [r for r in gameplay_frame(0) if abs(r["m"][0]) > 0.05]
    with pytest.raises(ValueError, match="camera"):
        camera_from_frame(recs)


def test_camera_raises_without_world_scale_reference():
    with pytest.raises(ValueError, match="camera"):
        camera_from_frame([minimap_rec(0, 100.0, 200.0)])


def test_screen_to_world_is_anchored_on_the_player():
    recs = gameplay_frame(0, player_world=(5000.0, 6000.0), mobs=[(800.0, 300.0, "Rock", 1.0)])
    cam = camera_from_frame(recs)
    # the player's own screen anchor maps back to the player's world position
    wx, wy = screen_to_world(*cam["player_screen"], cam)
    assert abs(wx - 5000.0) < 0.5 and abs(wy - 6000.0) < 0.5


def test_mob_carries_name_rarity_and_hp():
    recs = gameplay_frame(0, mobs=[(400.0, 200.0, "Beetle", 0.5)])
    # override the rarity text/colour on the mob's nameplate to Mythic
    recs = [r for r in recs if not (r["op"] == "text" and r["text"] == "Common")]
    recs += [text_rec(0, 400.0 + 8, 200.0 + 60, "Mythic", "#1FDBDE")] * 2
    mob = mobs_from_frame(recs, camera_from_frame(recs))[0]
    assert mob["name"] == "Beetle"
    assert mob["rarity_color"] == "#1FDBDE"
    assert abs(mob["hp"] - 0.5) < 0.05
    assert mob["sx"] == 400.0 and mob["sy"] == 200.0


def test_mob_with_a_bar_but_no_nameplate_text_reports_no_name():
    recs = list(gameplay_frame(0))                     # player + minimap, no mob
    recs += healthbar_recs(0, 400.0, 200.0, hp=1.0)    # a lone bar, no text
    mob = mobs_from_frame(recs, camera_from_frame(recs))[0]
    assert mob["name"] is None


def test_two_mobs_decode_independently():
    recs = gameplay_frame(0, mobs=[(400.0, 200.0, "Beetle", 1.0), (700.0, 500.0, "Scorpion", 1.0)])
    mobs = mobs_from_frame(recs, camera_from_frame(recs))
    assert sorted(m["name"] for m in mobs) == ["Beetle", "Scorpion"]
```

Run `./venv/bin/python -m pytest test_canvas_decode.py -v` after Step 2/3.
Expected: all pass. If `test_mob_carries_name_rarity_and_hp`'s rarity override is brittle against `nameplate`'s exact text layout, instead build that frame from `player_recs` + `minimap_rec` + `healthbar_recs` + explicit `text_rec`s rather than editing `gameplay_frame`'s output — the point is `mobs_from_frame` reads name[0], rarity[1], rarity_color, hp.

- [ ] **Step 5: Full suite + commit**

Run: `./venv/bin/python -m pytest -q` — the new file passes, nothing else changed.

```bash
git add canvas_hook.js canvas_decode.py canvas_frame_fixtures.py test_canvas_decode.py
git commit -m "feat: vendor canvas_decode + canvas_hook from florragent

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `cdp_bridge.inject_canvas_hook` + `drain_canvas_log`; delete `scroll_wheel`

**Files:**
- Modify: `cdp_bridge.py` — add two functions after `capture_screenshot`; delete `scroll_wheel`.
- Test: `test_cdp_bridge.py` — delete the two `test_scroll_wheel_*` tests; add tests for the two new functions.

**Interfaces:**
- Consumes: `_send_cdp_command`, `find_florr_tab` (existing); the file `canvas_hook.js` (Task 1) read relative to `cdp_bridge.py`'s dir.
- Produces:
  - `inject_canvas_hook(timeout=5) -> None` — idempotent; raises `RuntimeError` on stale-version (after triggering a reload) or if the no-reload eval didn't take (after a reload). A matching already-installed hook is a no-op.
  - `drain_canvas_log(timeout=5) -> list[dict]` — reads + clears `window.__canvasLog`; `[]` on any miss.

- [ ] **Step 1: Write the failing tests**

In `test_cdp_bridge.py`, delete `test_scroll_wheel_unwraps_runtime_evaluate_and_dispatches_wheel` and `test_scroll_wheel_falls_back_to_default_centre_on_bad_eval_shape`. Add:

```python
import hashlib
from pathlib import Path

_HOOK_SRC = (Path(cdp_bridge.__file__).parent / "canvas_hook.js").read_text()
_HOOK_VER = hashlib.sha256(_HOOK_SRC.encode()).hexdigest()[:16]


def _eval_result(value):
    return {"id": 1, "result": {"result": {"value": value}}}


def test_drain_canvas_log_unwraps_and_clears():
    sent = []

    def fake_send(method, params=None, timeout=5):
        sent.append((method, params))
        return _eval_result([{"frame": 1, "op": "fill"}])

    with patch("cdp_bridge._send_cdp_command", side_effect=fake_send):
        out = cdp_bridge.drain_canvas_log()
    assert out == [{"frame": 1, "op": "fill"}]
    assert sent[0][0] == "Runtime.evaluate"
    assert "__canvasLog" in sent[0][1]["expression"]


def test_drain_canvas_log_returns_empty_on_bad_shape():
    with patch("cdp_bridge._send_cdp_command", return_value={"id": 1, "result": {}}):
        assert cdp_bridge.drain_canvas_log() == []


def test_inject_canvas_hook_noop_when_matching_version_installed():
    calls = []

    def fake_send(method, params=None, timeout=5):
        calls.append(method)
        expr = (params or {}).get("expression", "")
        if "__canvasHookInstalled" in expr and "Version" not in expr:
            return _eval_result(True)                      # already installed
        if "__canvasHookInstalledVersion" in expr:
            return _eval_result(_HOOK_VER)                 # same version
        return _eval_result(None)

    with patch("cdp_bridge._send_cdp_command", side_effect=fake_send):
        cdp_bridge.inject_canvas_hook()
    # matching version installed -> must NOT reload and NOT raise
    assert "Page.reload" not in calls


def test_inject_canvas_hook_reloads_and_raises_on_stale_version(monkeypatch):
    calls = []

    def fake_send(method, params=None, timeout=5):
        calls.append(method)
        expr = (params or {}).get("expression", "")
        if "__canvasHookInstalled" in expr and "Version" not in expr:
            return _eval_result(True)
        if "__canvasHookInstalledVersion" in expr:
            return _eval_result("deadbeefdeadbeef")        # different version
        return _eval_result(None)

    with patch("cdp_bridge._send_cdp_command", side_effect=fake_send):
        with pytest.raises(RuntimeError, match="stale|reload|旧"):
            cdp_bridge.inject_canvas_hook()
    assert "Page.reload" in calls


def test_inject_canvas_hook_reloads_and_raises_when_eval_did_not_take(monkeypatch):
    # fresh page (not installed), eval the hook, then drains stay empty -> reload + raise
    calls = []

    def fake_send(method, params=None, timeout=5):
        calls.append(method)
        expr = (params or {}).get("expression", "")
        if "__canvasHookInstalled" in expr and "Version" not in expr:
            return _eval_result(False)                     # not installed yet
        if "__canvasHookInstalledVersion" in expr:
            return _eval_result(None)
        if "__canvasLog" in expr:
            return _eval_result([])                        # drains never grow
        return _eval_result(None)

    monkeypatch.setattr(cdp_bridge.time, "sleep", lambda *a: None)
    with patch("cdp_bridge._send_cdp_command", side_effect=fake_send):
        with pytest.raises(RuntimeError, match="reload|take|生效"):
            cdp_bridge.inject_canvas_hook()
    assert "Page.reload" in calls
```

(Adjust the `expr` substring checks to match the exact JS you write in Step 3.)

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_cdp_bridge.py -k "canvas" -v`
Expected: FAIL — `AttributeError: module 'cdp_bridge' has no attribute 'drain_canvas_log'`.

- [ ] **Step 3: Implement in `cdp_bridge.py`**

Add near the top imports: `import hashlib`, `from pathlib import Path` (if not already there — check).

Delete the entire `scroll_wheel` function.

Add after `capture_screenshot`:

```python
_CANVAS_HOOK_PATH = Path(__file__).with_name("canvas_hook.js")


def _eval_value(expression, timeout=5):
    """Runtime.evaluate + returnByValue, unwrapped to the primitive/JSON value. None on miss."""
    resp = _send_cdp_command(
        "Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=timeout)
    return resp.get("result", {}).get("result", {}).get("value")


def drain_canvas_log(timeout=5):
    """读空 window.__canvasLog(canvas_hook.js 往里塞每帧的绘制记录), 返回记录列表.
    一次 Runtime.evaluate 里读 + 清, 中间不会漏帧. 拿不到 → []."""
    v = _eval_value(
        "(()=>{const l=window.__canvasLog||[];window.__canvasLog=[];return l;})()", timeout)
    return v if isinstance(v, list) else []


def inject_canvas_hook(timeout=5):
    """把 canvas_hook.js 注进 florr.io 标签页(patch CanvasRenderingContext2D 记录绘制调用).
    幂等: 同版本已装 → 直接返回. 移植自 florragent 的 _inject_canvas_hook, 换成裸 CDP:
      1. Runtime.evaluate 注 hook(免 reload 路径).
      2. Page.addScriptToEvaluateOnNewDocument 同一份(跨 reload 持久).
      3. 版本指纹(sha256[:16]): 页面上装的是别的版本 → Page.reload + 抛 RuntimeError
         (patchProto 的 per-prototype guard 不能热替).
      4. 免 reload 注完 drain 一次 + sleep(0.5) + 再 drain: 第二次还是空 → florr 在 patch
         落地前就绑了 ctx 方法引用 → Page.reload + 抛 RuntimeError.
    抛 RuntimeError 时调用方(enemy_detect.scan_enemies)会当"本次没检测到"退化成 wander,
    下次扫描再重试(幂等)."""
    src = _CANVAS_HOOK_PATH.read_text()
    version = hashlib.sha256(src.encode()).hexdigest()[:16]
    installed = _eval_value("!!window.__canvasHookInstalled", timeout)
    installed_ver = _eval_value("window.__canvasHookInstalledVersion || null", timeout)
    if installed and installed_ver != version:
        _send_cdp_command("Page.addScriptToEvaluateOnNewDocument", {"source": src}, timeout=timeout)
        _send_cdp_command("Page.reload", {}, timeout=timeout)
        raise RuntimeError("canvas hook 版本不一致(页面上是旧版) —— 已 reload, 请重进游戏后重试")
    if installed:
        return
    _send_cdp_command("Page.addScriptToEvaluateOnNewDocument", {"source": src}, timeout=timeout)
    _eval_value(f"window.__canvasHookInstalledVersion = {version!r};\n" + src, timeout)
    drain_canvas_log(timeout)                       # discard the injection's own output
    time.sleep(0.5)
    if not drain_canvas_log(timeout):
        _send_cdp_command("Page.reload", {}, timeout=timeout)
        raise RuntimeError("canvas hook 注入没生效 —— 已 reload, 请重进游戏后重试")
```

- [ ] **Step 4: Run tests**

Run: `./venv/bin/python -m pytest test_cdp_bridge.py -q`
Expected: PASS (the 5 new canvas tests + the rest; the 2 `scroll_wheel` tests are gone).

Run: `./venv/bin/python -m pytest -q`
Expected: PASS — **except** `main.py`'s `ensure_zoom_for_rarity` still calls `cdp_bridge.scroll_wheel`. `py_compile main.py` still succeeds (name resolved at call time), and no test exercises `ensure_zoom_for_rarity`'s scroll path against the real deleted function *except* the `test_ensure_zoom_*` tests in `test_main_worker.py` which stub `main.cdp_bridge.scroll_wheel` via `_stub_zoom_env`. Those stubs use `monkeypatch.setattr(main.cdp_bridge, "scroll_wheel", ...)` — `setattr` on a missing attr with default `raising=True` will now FAIL. **So: in this task, also update `_stub_zoom_env` in `test_main_worker.py` to `monkeypatch.setattr(main.cdp_bridge, "scroll_wheel", lambda *a, **k: calls["scroll"].append(a[0] if a else None), raising=False)`.** (Task 4 deletes the zoom tests entirely; this keeps the suite green in between.)

- [ ] **Step 5: Commit**

```bash
git add cdp_bridge.py test_cdp_bridge.py test_main_worker.py
git commit -m "feat: cdp_bridge.inject_canvas_hook + drain_canvas_log; drop scroll_wheel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: `enemy_detect.scan_enemies` on canvas decode; delete dead pixel code

**Files:**
- Modify: `enemy_detect.py` — imports; delete pixel-recognition code; reimplement `scan_enemies`; add `_species_from_name` / `_tier_from_color` / `_frame_buffer`.
- Test: `test_enemy_detect.py` — delete pixel tests; add mapping + `scan_enemies` tests.

**Interfaces:**
- Consumes: `canvas_decode.group_by_frame / camera_from_frame / mobs_from_frame` (Task 1); `cdp_bridge.inject_canvas_hook / drain_canvas_log` (Task 2).
- Produces: `scan_enemies(image=None, conf=0.4, model_path=None) -> list[detection-dict]` (params kept for caller compatibility, ignored). `_species_from_name(name) -> str | None`. `_tier_from_color(hex) -> str`.

- [ ] **Step 1: Write the failing tests**

In `test_enemy_detect.py`:
- Delete: `test_sample_rarity_*` (all), `test_find_hp_bar_locates_bar_and_rejects_blob`, `test_measure_hp_bar_thickness_*`, `test_scan_bar_thickness_empty_for_blank_image`, `test_load_enemy_model_exposes_expected_classes` (if present), `test_scan_enemies_returns_empty_list_for_blank_image`. Delete the now-unused imports (`sample_rarity`, `_find_hp_bar`, `_hex_to_bgr`, `RARITY_COLORS`, `measure_hp_bar_thickness`, `scan_bar_thickness`, `_name_tag_image` helper + `_BG_BGR` / `_BAR_BGR` constants and `_HAS_MODEL` / `_SKIP_REASON` if only those tests used them — grep first).

Append:

```python
from enemy_detect import scan_enemies, _species_from_name, _tier_from_color
import enemy_detect as _ed
from canvas_frame_fixtures import gameplay_frame, text_rec, healthbar_recs, minimap_rec, player_recs


def test_species_from_name_english_slugs():
    assert _species_from_name("Beetle") == "beetle"
    assert _species_from_name("Scorpion") == "scorpion"
    assert _species_from_name("Sand Centipede") == "sand_centipede"
    assert _species_from_name("Soldier Fire Ant") == "soldier_fire_ant"
    assert _species_from_name("Sandstorm") == "sandstorm"
    assert _species_from_name("Cactus") == "cactus"


def test_species_from_name_rejects_non_desert_and_none():
    assert _species_from_name("Ladybug") is None
    assert _species_from_name("Player #12") is None
    assert _species_from_name(None) is None
    assert _species_from_name("") is None


def test_tier_from_color():
    assert _tier_from_color("#1FDBDE") == "Mythic"
    assert _tier_from_color("#7EEF6D") == "Common"
    assert _tier_from_color("#FF2B75") == "Ultra"
    assert _tier_from_color("#555555") == "Unique"
    assert _tier_from_color(None) == "Common"
    assert _tier_from_color("#abcdef") == "Common"


def _stub_canvas(monkeypatch, records):
    monkeypatch.setattr(_ed.cdp_bridge, "inject_canvas_hook", lambda *a, **k: None)
    monkeypatch.setattr(_ed.cdp_bridge, "drain_canvas_log", lambda *a, **k: list(records))
    _ed._frame_buffer[:] = []


def test_scan_enemies_maps_a_two_mob_frame(monkeypatch):
    # two consecutive frames so frames-1 (latest) is discarded and frames-2 is decoded
    f_old = gameplay_frame(0, mobs=[(400.0, 200.0, "Beetle", 1.0)])
    # override Beetle's rarity to Mythic
    f_old = [r for r in f_old if not (r["op"] == "text" and r["text"] == "Common")]
    f_old += [text_rec(0, 408.0, 260.0, "Mythic", "#1FDBDE")] * 2
    f_old += [n_ for nm in [("Cactus", 700.0, 500.0)]
              for n_ in ([text_rec(0, 0, 0, "x")][:0])]   # no-op; keep list flat
    f_new = gameplay_frame(1)                              # just player+minimap, newer
    _stub_canvas(monkeypatch, f_old + f_new)
    dets = scan_enemies()
    assert len(dets) == 1
    d = dets[0]
    assert d["species"] == "beetle"
    assert d["rarity"] == "Mythic"
    assert d["screen_pos"] == (400.0, 200.0)
    assert d["confidence"] == 1.0


def test_scan_enemies_drops_non_desert_names(monkeypatch):
    f_old = gameplay_frame(0, mobs=[(400.0, 200.0, "Ladybug", 1.0)])
    f_new = gameplay_frame(1)
    _stub_canvas(monkeypatch, f_old + f_new)
    assert scan_enemies() == []


def test_scan_enemies_empty_when_fewer_than_two_frames(monkeypatch):
    _stub_canvas(monkeypatch, gameplay_frame(0, mobs=[(400.0, 200.0, "Beetle", 1.0)]))
    assert scan_enemies() == []


def test_scan_enemies_empty_when_camera_undecodable(monkeypatch):
    # frames present but no minimap dot -> camera_from_frame raises -> []
    f0 = [r for r in gameplay_frame(0, mobs=[(400.0, 200.0, "Beetle", 1.0)]) if abs(r["m"][0]) > 0.05]
    f1 = [r for r in gameplay_frame(1) if abs(r["m"][0]) > 0.05]
    _stub_canvas(monkeypatch, f0 + f1)
    assert scan_enemies() == []
```

(The `f_old` nested-comprehension no-op line is ugly — replace it with a clean second mob if you want a 2-mob assertion; the essential coverage is one mapped mob + drop-non-desert + <2-frames + camera-raises.)

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -k "species_from_name or tier_from_color or scan_enemies" -v`
Expected: FAIL — import errors for the new names.

- [ ] **Step 3: Rewrite `enemy_detect.py`'s imports + delete dead code**

Replace the top of the file (lines 1–13, the `import math` … `import utils` block plus the YOLO_OFFLINE env lines) with:

```python
import math

import utils
import cdp_bridge
import canvas_decode
```

Delete: `RARITY_COLORS`, `_hex_to_bgr`, `MIN_RARITY_PIXEL_RATIO`, `_find_hp_bar`, `sample_rarity`, `measure_hp_bar_thickness`, `_model = None`, `load_enemy_model`, `scan_bar_thickness`, and the OLD body of `scan_enemies`.

Keep: `RARITY_ORDER`, `RARITY_RANK`, `SPECIES_RANK`, `_AVOID_PAIRS`, `_CAUTIOUS_PAIRS`, `classify_action`, `priority_score`, `MYTHIC_KITE_SPECIES`, `MYTHIC_TARGET_RANK`, `mythic_candidates`, `SCREEN_CENTER`, `pick_mythic_target`, `mythic_move_target`, `aim_mouse_target`, `flee_mouse_target`, `CHASE_STALL_WINDOW`, `chase_is_stalled`, `CHASE_MIN_CONF`, `select_action`.

- [ ] **Step 4: Add the new `scan_enemies` + helpers**

Where `scan_enemies` was (after `select_action`), add:

```python
_DESERT_SPECIES = {"scorpion", "beetle", "cactus", "sandstorm", "sand_centipede", "soldier_fire_ant"}
_SPECIES_ALIASES = {}   # florr English name (slugified) → desert slug, for any spelling mismatch;
                        # fill after a live check. e.g. {"centipede": "sand_centipede"}

_RANK_BY_RARITY_COLOR = {
    "#7EEF6D": 0, "#FFE65D": 1, "#4D52E3": 2, "#861FDE": 3, "#DE1F1F": 4,
    "#1FDBDE": 5, "#FF2B75": 6, "#2BFFA3": 7, "#555555": 9,
}


def _species_from_name(name):
    """florr 客户端英文名 → desert 物种 slug. 六种沙漠怪之外(路过的玩家 / 别的
    生态的怪)→ None(跳过). 客户端语言必须是 English."""
    if not name:
        return None
    slug = name.strip().lower().replace(" ", "_")
    if slug in _DESERT_SPECIES:
        return slug
    return _SPECIES_ALIASES.get(slug)


def _tier_from_color(rarity_color):
    """名牌稀有度词的颜色 → RARITY_ORDER 里的档名. 认不出 / None → Common
    (跟旧 sample_rarity 读失败时同款兜底, 不会误触发规避)."""
    rank = _RANK_BY_RARITY_COLOR.get(rarity_color)
    return RARITY_ORDER[rank] if rank is not None else "Common"


_frame_buffer = []   # drain_canvas_log 每次读空页面 log, 跨调用在这里攒; 每次裁到最新一帧


def scan_enemies(image=None, conf=0.4, model_path=None):
    """解码最新一帧完整的 canvas 绘制记录, 返回检测字典列表(跟旧 YOLO 版同结构:
    species / rarity / screen_pos / bbox / confidence). image/conf/model_path 保留
    只为兼容旧调用点, 不再用 —— 识别已经从"截图跑 YOLO"换成"解码 canvas 绘制调用".
    帧解不出(画面里没怪 → camera_from_frame 抛) → [](跟旧模型没框一个意思)."""
    try:
        cdp_bridge.inject_canvas_hook()
        _frame_buffer.extend(cdp_bridge.drain_canvas_log())
        frames = canvas_decode.group_by_frame(_frame_buffer)
        if len(frames) < 2:
            return []
        keys = sorted(frames)
        recs = frames[keys[-2]]                 # 最新那帧可能还在画, 取次新的
        _frame_buffer[:] = [r for r in _frame_buffer if r.get("frame", -1) >= keys[-1]]
        cam = canvas_decode.camera_from_frame(recs)
        mobs = canvas_decode.mobs_from_frame(recs, cam)
    except (ValueError, RuntimeError, KeyError, TypeError):
        return []

    out = []
    for m in mobs:
        sp = _species_from_name(m.get("name"))
        if sp is None:
            continue
        sx, sy = m["sx"], m["sy"]
        out.append({
            "species": sp,
            "rarity": _tier_from_color(m.get("rarity_color")),
            "screen_pos": (sx, sy),
            "bbox": (sx - 1, sy - 1, sx + 1, sy + 1),
            "confidence": 1.0,
        })
    return out
```

- [ ] **Step 5: Run tests**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -q`
Expected: PASS (mapping + `scan_enemies` tests; the kept `select_action` / mythic / `aim_mouse_target` / `chase_is_stalled` / `classify_action` / `priority_score` tests untouched and green).

Run: `./venv/bin/python -m py_compile enemy_detect.py`

- [ ] **Step 6: Full suite**

Run: `./venv/bin/python -m pytest -q`
Expected: `main.py`'s `scan_bar_thickness` call inside `ensure_zoom_for_rarity` now references a deleted `enemy_detect.scan_bar_thickness` — `py_compile main.py` still passes (call-time resolution) and `test_main_worker.py::test_ensure_zoom_*` stub `enemy_detect.scan_bar_thickness` via `_stub_zoom_env`'s `monkeypatch.setattr(main.enemy_detect, "scan_bar_thickness", ...)` which will now FAIL (`raising=True` on a missing attr). **In this task also change that line in `_stub_zoom_env` to `..., raising=False)`.** (Task 4 deletes those tests; this bridges.)

- [ ] **Step 7: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py test_main_worker.py
git commit -m "feat: scan_enemies decodes the canvas frame instead of running YOLO

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `main.py` cleanup — delete zoom gate + model plumbing; delete `debug_enemy_detect.py`

**Files:**
- Modify: `main.py` — delete `ENEMY_MODEL_PATH`, the `ZOOM_*` constants, `ensure_zoom_for_rarity`, the `run_worker` zoom-call block, the `run_worker` model-file guard; `_maybe_scan_enemies` drops `model_path=`.
- Delete: `debug_enemy_detect.py`.
- Test: `test_main_worker.py` — delete the `test_ensure_zoom_*` block + `_stub_zoom_env`; `_maybe_scan_enemies` tests drop any `model_path` reference.

**Interfaces:** no new API. `_maybe_scan_enemies(enemy_ai_enabled, now, last_enemy_scan, prev_decision, prev_detections) -> (decision, detections, last_enemy_scan, scanned)` — unchanged signature, just calls `enemy_detect.scan_enemies()` bare.

- [ ] **Step 1: Delete the zoom tests**

In `test_main_worker.py`: delete every `def test_ensure_zoom_*` function and the `_stub_zoom_env` helper. Grep for other references to `ensure_zoom_for_rarity` / `ZOOM_` / `scroll_wheel` in that file and remove them.

- [ ] **Step 2: Run to verify the suite still imports**

Run: `./venv/bin/python -m pytest test_main_worker.py -q`
Expected: PASS (fewer tests; `_maybe_scan_enemies` / mythic / `move_to_position` / worker tests remain).

- [ ] **Step 3: Delete from `main.py`**

- The line `ENEMY_MODEL_PATH = "models/desert.pt"`.
- The five `ZOOM_MIN_THICK` / `ZOOM_MIN_SAMPLES` / `ZOOM_SCROLL_AMOUNT` / `ZOOM_MAX_SCROLLS` / `ZOOM_WAIT_CAP` lines.
- `import statistics` (only `ensure_zoom_for_rarity` used it — grep to confirm) — leave it if anything else uses it.
- The entire `def ensure_zoom_for_rarity(...)` function.
- In `run_worker`, the block:
  ```python
          zoom_ok = ensure_zoom_for_rarity(w["enemy_ai_enabled"])
          if w["enemy_ai_enabled"] and not zoom_ok:
              print("⚠️ 视角未调到位, 本轮稀有度识别可能不准 (Mythic 锁定可能不触发)")
  ```
  → delete it entirely (the `auto_farming(...)` call right after stays).
- In `run_worker`, the `w["enemy_ai_enabled"]` model guard:
  ```python
      if w["enemy_ai_enabled"]:
          if cfg["map"] != "desert":
              print(...); w["enemy_ai_enabled"] = False
          elif not os.path.isfile(ENEMY_MODEL_PATH):
              print(...); w["enemy_ai_enabled"] = False
  ```
  → delete the whole `if w["enemy_ai_enabled"]:` block. (Canvas decode needs no model and works regardless of map; the `enemy_ai_enabled` flag from config still gates everything downstream.) If `import os` becomes unused in `main.py`, leave it (likely used elsewhere — grep).
- In `_maybe_scan_enemies`, change `enemy_detect.scan_enemies(model_path=ENEMY_MODEL_PATH)` → `enemy_detect.scan_enemies()`.

- [ ] **Step 4: Delete `debug_enemy_detect.py`**

```bash
git rm debug_enemy_detect.py
```

- [ ] **Step 5: Compile + full suite**

Run: `./venv/bin/python -m py_compile main.py enemy_detect.py cdp_bridge.py canvas_decode.py`
Run: `./venv/bin/python -m pytest -q`
Expected: PASS — whole suite. No `ensure_zoom` / `scroll_wheel` / `scan_bar_thickness` / `sample_rarity` references remain (`grep -rn "ensure_zoom_for_rarity\|scroll_wheel\|scan_bar_thickness\|sample_rarity\|ENEMY_MODEL_PATH" *.py` → nothing).

- [ ] **Step 6: Diagnostic smoke**

Run:
```bash
./venv/bin/python -c "
import enemy_detect, canvas_decode, cdp_bridge
from canvas_frame_fixtures import gameplay_frame, text_rec
f0 = gameplay_frame(0, mobs=[(400.0,200.0,'Beetle',1.0)])
f0 = [r for r in f0 if not (r['op']=='text' and r['text']=='Common')] + [text_rec(0,408,260,'Mythic','#1FDBDE')]*2
f1 = gameplay_frame(1)
enemy_detect.cdp_bridge.inject_canvas_hook = lambda *a,**k: None
enemy_detect.cdp_bridge.drain_canvas_log = lambda *a,**k: f0+f1
enemy_detect._frame_buffer[:] = []
print(enemy_detect.scan_enemies())
"
```
Expected: `[{'species': 'beetle', 'rarity': 'Mythic', 'screen_pos': (400.0, 200.0), 'bbox': (399.0, 199.0, 401.0, 201.0), 'confidence': 1.0}]`.

- [ ] **Step 7: Commit + push**

```bash
git add -A
git commit -m "refactor: drop the zoom gate and YOLO model plumbing from main.py

The zoom gate existed only to make pixel sample_rarity readable; canvas
decode is zoom-independent, so ensure_zoom_for_rarity / ZOOM_* /
scroll_wheel / the desert-only model guard / debug_enemy_detect.py all go.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push origin main
```

---

## Self-Review

**1. Spec coverage:**

| Spec item | Task |
|---|---|
| Vendor `canvas_decode.py` (trimmed) + `canvas_hook.js` | Task 1 Steps 1-2 |
| `camera_from_frame` / `mobs_from_frame` / `group_by_frame` available + tested | Task 1 Steps 2, 4 |
| `cdp_bridge.inject_canvas_hook` (no-reload + persist + version + reload-fallback) | Task 2 Step 3 |
| `cdp_bridge.drain_canvas_log` (unwrap `result.result.value`, `[]` on miss) | Task 2 Step 3 |
| Delete `scroll_wheel` + its 2 tests | Task 2 Steps 1, 3 |
| `scan_enemies` → decode newest-complete frame → detection dicts; `[]` on ValueError/`<2 frames` | Task 3 Step 4 |
| `_species_from_name` (English slug, non-desert → None) | Task 3 Step 4 |
| `_tier_from_color` (colour → `RARITY_ORDER` tier, unknown → Common) | Task 3 Step 4 |
| `_frame_buffer` accumulate + prune to newest frame | Task 3 Step 4 |
| Delete `_find_hp_bar` / `sample_rarity` / `measure_hp_bar_thickness` / `scan_bar_thickness` / `load_enemy_model` / `RARITY_COLORS` / `MIN_RARITY_PIXEL_RATIO` / `_hex_to_bgr` + `cv2`/`numpy`/`pyautogui`/`ultralytics` imports | Task 3 Step 3 |
| Keep `RARITY_ORDER` / `classify_action` / `priority_score` / mythic / `select_action` / `aim_mouse_target` / `flee_mouse_target` / `chase_is_stalled` | Task 3 Step 3 |
| Delete zoom gate (`ensure_zoom_for_rarity`, `ZOOM_*`), its `run_worker` call, the `"视角未调到位"` warning | Task 4 Step 3 |
| Delete `run_worker` desert-only + model-file guard; `ENEMY_MODEL_PATH` | Task 4 Step 3 |
| `_maybe_scan_enemies` drops `model_path=` | Task 4 Step 3 |
| Delete `debug_enemy_detect.py` | Task 4 Step 4 |
| `test_enemy_detect.py` pixel tests removed; kept tests unaffected | Task 3 Step 1 |
| `test_main_worker.py` `test_ensure_zoom_*` removed; `_maybe_scan_enemies` tests unaffected | Task 4 Step 1 |
| `test_canvas_decode.py` new; fixtures vendored | Task 1 Steps 3-4 |

No gaps.

**2. Placeholder scan:** No "TBD" in code. `_SPECIES_ALIASES = {}` is an intentional empty extension point documented inline (English slugs match the desert slugs directly for all six species; the alias map is only for a spelling surprise a live run might reveal). The two `test_scan_enemies_maps_a_two_mob_frame` "no-op nested comprehension" lines are flagged in the step text as replaceable — the reviewer/implementer can drop them for a clean second mob; the essential assertions don't depend on them.

**3. Type consistency:**
- `canvas_decode.camera_from_frame(records) -> dict | raises` / `mobs_from_frame(records, camera) -> list[dict]` — same in Task 1 tests, Task 3 `scan_enemies`.
- `cdp_bridge.inject_canvas_hook(timeout=5) -> None (raises RuntimeError)` / `drain_canvas_log(timeout=5) -> list` — same in Task 2 impl/tests and Task 3 `scan_enemies`.
- `scan_enemies(image=None, conf=0.4, model_path=None) -> list[detection-dict]` — Task 3 impl; Task 4's `_maybe_scan_enemies` calls it bare; `test_main_worker.py` stubs it (unaffected).
- `_species_from_name(name) -> str | None`, `_tier_from_color(hex) -> str` — Task 3 impl + tests; used only inside `scan_enemies`.
- detection-dict keys `species/rarity/screen_pos/bbox/confidence` — produced by `scan_enemies`, consumed unchanged by `select_action` / mythic (their tests build the same shape and are kept).

No inconsistencies.
