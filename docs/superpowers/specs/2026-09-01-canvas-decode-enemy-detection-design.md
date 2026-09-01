# Canvas-decode enemy detection — design

## Problem

`enemy_detect.scan_enemies` runs a YOLO model (`models/desert.pt`) on a screenshot,
then `sample_rarity` pixel-matches a mob's rarity tag. Both fail on the real
client: YOLO drops mobs / mislocates boxes, and the rarity tag is ~2 px tall at
normal camera zoom so `sample_rarity` reads `Common` for everything — the Mythic
latch never fires. The zoom gate added to compensate (`ensure_zoom_for_rarity`)
zoomed the camera in, which broke `move_to_position`'s tuned steering gain and
walked the bot out of the farming area; the follow-up `_move_gain_for_zoom`
compensation failed its own review and was reverted (`d02c02c`).

The sibling project `/Users/macmima1234/florragent` reads the same game state
**exactly** by decoding `CanvasRenderingContext2D` draw calls: it intercepts every
draw, tracks the CTM per context, and reads mob screen position, name, rarity
(from the nameplate's rarity-text colour), and HP straight off the render frame —
zoom-independent, no model, fails loudly rather than guessing.

## Scope

**In scope:** replace `enemy_detect.scan_enemies`'s data source with canvas decode.
Everything downstream of it — `select_action`, the Mythic latch, chase-gate, flee,
`_wander_enemy_watch` — is unchanged; it already consumes
`{species, rarity, screen_pos, bbox, confidence}` dicts.

**Not in scope (this round):**
- Pathing's player position (`get_player_position` stays on minimap pixel reads).
- Death / start-screen detection (`on_death_screen` / `on_start_screen` stay).
- Enemy AI on non-desert maps (still gated by `enemy_ai_enabled`; the model-file
  guard in `run_worker` goes away, but no new map wiring).
- Any tuning of `select_action` / Mythic radii.

## Architecture

### New: `canvas_decode.py` (vendored, trimmed from florragent)

A ~180-line subset, copied as-verbatim-as-possible (it is battle-tested in
florragent):

- Constants: `MINIMAP_MAX_SCALE`, `HEALTHBAR_BG` / `HEALTHBAR_DAMAGE` /
  `HEALTHBAR_SECONDARY`, `PLAYER_BODY_COLOR`, `PLAYER_RARITY_PATTERN`,
  `SELF_DISAMBIGUATION_RADIUS`.
- Helpers: `group_by_frame`, `_scale`, `_anchor`, `_is_minimap`, `_is_rotated`,
  `_bar_width`, `_bar_blocks`, `_is_player_anchor`, `_is_player_block`,
  `screen_to_world`, `camera_from_frame`, `mobs_from_frame`.
- **Not** vendored: `entities_from_frame`, `other_players_from_frame`,
  `player_from_frame`, `inventory_slots_from_frame`, `death_panel_continue_button`,
  `start_menu_start_button`, `load_ndjson`, `decode_frame` (their concerns are
  out of scope).

`camera_from_frame(records) -> {"zoom", "player_world", "player_screen"}` raises
`ValueError` if any of the three can't be read (no mob on screen ⇒ no scale
reference). `mobs_from_frame(records, camera)` returns a list of
`{"name", "rarity", "rarity_color", "hp", "sx", "sy", "x", "y"}`.

### New: `canvas_hook.js` (vendored verbatim)

florragent's `scripts/canvas_hook.js`. Patches `CanvasRenderingContext2D.prototype`
(and `OffscreenCanvasRenderingContext2D`), tracks the CTM through
save/restore/transform, and appends bar strokes + arc fills + nearby text to
`window.__canvasLog` (self-pruning, capped at 50 000). Idempotent via
`window.__canvasHookInstalled`.

### `cdp_bridge.py` — two new functions

- `inject_canvas_hook(timeout=5)` — port of florragent's `_inject_canvas_hook` to
  raw CDP (no Playwright):
  1. `Runtime.evaluate` the hook source (no-reload path).
  2. `Page.addScriptToEvaluateOnNewDocument` with the same source (persists across
     reloads).
  3. Version fingerprint (`sha256(hook)[:16]` stashed on
     `window.__canvasHookInstalledVersion`): if a *different* version is already
     patched, `Page.reload` and raise `RuntimeError` telling the caller to retry
     (patchProto's per-prototype guard can't be hot-swapped).
  4. After the no-reload eval, drain once + `sleep(0.5)` + drain again; if the
     second drain is empty, `Page.reload` and raise `RuntimeError` (florr bound a
     `ctx` method reference before the patch landed).
  Idempotent: a matching-version already-installed hook is a cheap no-op.
- `drain_canvas_log(timeout=5) -> list[dict]` —
  `Runtime.evaluate("(()=>{const l=window.__canvasLog||[];window.__canvasLog=[];return l;})()", returnByValue=True)`
  → `resp["result"]["result"]["value"]` (the double-nested unwrap `scroll_wheel`
  already documents), `[]` on any miss.

### `enemy_detect.py` — `scan_enemies` reimplemented

```python
_frame_buffer = []   # module-level: drain empties the page log each call, accumulate here

def scan_enemies(image=None, conf=0.4, model_path=None):
    """Decode the newest complete canvas frame into detection dicts. image / conf /
    model_path are ignored (kept so callers don't break) — recognition is now
    canvas-draw-call decode, not YOLO on a screenshot.

    Returns [] when no frame is decodable (no mob on screen ⇒ camera_from_frame
    raises ⇒ same 'nothing detected' as the old model returning no boxes)."""
    try:
        cdp_bridge.inject_canvas_hook()
        _frame_buffer.extend(cdp_bridge.drain_canvas_log())
        frames = canvas_decode.group_by_frame(_frame_buffer)
        if len(frames) < 2:
            return []
        keys = sorted(frames)
        latest_complete = keys[-2]          # -1 may still be mid-render
        recs = frames[latest_complete]
        _frame_buffer[:] = [r for r in _frame_buffer if r["frame"] >= keys[-1]]
        cam = canvas_decode.camera_from_frame(recs)
        mobs = canvas_decode.mobs_from_frame(recs, cam)
    except (ValueError, RuntimeError, KeyError) as e:
        return []
    out = []
    for m in mobs:
        sp = _species_from_name(m.get("name"))
        if sp is None:
            continue
        out.append({
            "species": sp,
            "rarity": _tier_from_color(m.get("rarity_color")),
            "screen_pos": (m["sx"], m["sy"]),
            "bbox": (m["sx"] - 1, m["sy"] - 1, m["sx"] + 1, m["sy"] + 1),
            "confidence": 1.0,
        })
    return out
```

- `_species_from_name(name)` — florr client language is **English** (confirmed with
  the user). `name.strip().lower().replace(" ", "_")`; accept only the six desert
  species (`scorpion`, `beetle`, `cactus`, `sandstorm`, `sand_centipede`,
  `soldier_fire_ant`); anything else → `None` (skipped — a passing player, a mob
  from another biome). A small `_SPECIES_ALIASES` dict handles any name florr
  spells differently from the slug (filled after a live check).
- `_tier_from_color(hex)` — invert the rarity colour table (florr-auto-pathing's
  own `RARITY_COLORS` has the same hex values, minus the `#`): `#1FDBDE` → `Mythic`,
  etc.; unknown / `None` → `Common` (fail-permissive, matches the old
  `sample_rarity` default). Return value is a `RARITY_ORDER` string that
  `classify_action` / `priority_score` / the Mythic gate already consume.

**Deleted** (dead once YOLO is gone): `_find_hp_bar`, `sample_rarity`,
`measure_hp_bar_thickness`, `scan_bar_thickness`, `load_enemy_model`,
`RARITY_COLORS`, `MIN_RARITY_PIXEL_RATIO`, `_hex_to_bgr`, and the
`from ultralytics import YOLO` / `import cv2` / `import pyautogui` imports (unless
still needed elsewhere in the file — check). **Kept**: `RARITY_ORDER`,
`RARITY_RANK`, `SPECIES_RANK`, `classify_action`, `priority_score`,
`aim_mouse_target`, `flee_mouse_target`, `chase_is_stalled`, `select_action`, the
Mythic block, `SCREEN_CENTER`.

`screen_pos` is the mob's true screen-pixel anchor (`sx, sy` from the CTM). The
Mythic / flee / chase math keeps `center = SCREEN_CENTER = (SCREEN_WIDTH/2,
SCREEN_HEIGHT/2)`: florr locks the camera on the player, so `camera["player_screen"]`
sits within a few px of screen centre — negligible against the 400–850 px radii in
play, and it avoids threading a per-frame centre through every call site.

### `main.py` — remove the now-dead zoom gate + model plumbing

- Delete `ensure_zoom_for_rarity`, its `ZOOM_*` constants, and the `run_worker`
  block that calls it + the `"⚠️ 视角未调到位"` warning.
- Delete `cdp_bridge.scroll_wheel` (only the zoom gate used it) and its two
  `test_cdp_bridge.py` tests.
- `run_worker`: delete the `w["enemy_ai_enabled"]` guard that checks `cfg["map"] ==
  "desert"` and `os.path.isfile(ENEMY_MODEL_PATH)` (canvas decode needs no model;
  the `enemy_ai_enabled` flag itself stays).
- Delete `ENEMY_MODEL_PATH`; `_maybe_scan_enemies` calls `enemy_detect.scan_enemies()`
  with no `model_path`.
- `ENEMY_SCAN_INTERVAL` stays (drain+decode is cheap, but throttling the CDP round
  trip is still worth it).

`_maybe_scan_enemies`, `_wander_enemy_watch`, the Mythic latch, chase-gate, flee,
`_drive_and_check_stall` — untouched.

### `models/desert.pt`, `debug_enemy_detect.py`

`models/*.pt` is gitignored / user-supplied — leave the files, they're just unused.
`debug_enemy_detect.py` is entirely YOLO/`sample_rarity`-based → **delete it**
(git keeps it recoverable); a canvas-decode diagnostic can come later if wanted.

## Error handling

- `inject_canvas_hook` raising `RuntimeError` (needs-reload / stale / no florr tab)
  → caught in `scan_enemies` → `[]` → `_maybe_scan_enemies` degrades the tick to
  wander (its existing `try/except`). Next scan retries the injection (idempotent).
- `camera_from_frame` `ValueError` (no mob ⇒ no scale ref) → `[]`. Correct: no mob
  on screen genuinely means no detections.
- `drain_canvas_log` empty / malformed → `[]` from the helper, `< 2 frames` guard
  → `[]`.
- `_frame_buffer` is pruned to the newest frame every call (florragent's
  `next_frame` pattern); the hook self-caps the page-side log at 50 000.

## Testing

- **`test_canvas_decode.py`** (new) — port the relevant cases from florragent's
  `tests/test_canvas_decode.py`; fixture = a handful of real in-game frames
  extracted from florragent's `data/raw_captures/canvas_combat_test.ndjson` (it has
  mob nameplates), checked in as `tests/fixtures/canvas_combat_frames.ndjson`.
  Cover: `group_by_frame`; `camera_from_frame` returns the three fields on a
  combat frame and raises `ValueError` on a mob-less frame; `mobs_from_frame`
  yields the expected names/rarities/positions for a known frame.
- **`test_enemy_detect.py`** — delete the `sample_rarity` / `_find_hp_bar` /
  `measure_hp_bar_thickness` / `scan_bar_thickness` tests. Keep everything else
  (they build detection dicts directly, unaffected). Add:
  - `_species_from_name` — `"Beetle"` → `beetle`, `"Sand Centipede"` →
    `sand_centipede`, `"Player #12"` / `"Ladybug"` → `None`.
  - `_tier_from_color` — `"#1FDBDE"` → `Mythic`, `"#7EEF6D"` → `Common`, `None` →
    `Common`, `"#abcdef"` → `Common`.
  - `scan_enemies`: monkeypatch `enemy_detect.cdp_bridge.inject_canvas_hook` to a
    no-op and `...drain_canvas_log` to return a synthetic two-mob frame's records;
    reset `enemy_detect._frame_buffer[:] = []` at the top of each such test; assert
    it maps to two detection dicts with the right `species` / `rarity` /
    `screen_pos`, and drops a non-desert `name`. Also: `< 2 frames` in the buffer →
    `[]`; `camera_from_frame` raising → `[]`.
- **`test_cdp_bridge.py`** — delete the two `scroll_wheel` tests. Add
  `inject_canvas_hook` (mocked `_send_cdp_command`: version match → no reload;
  version mismatch → reload + `RuntimeError`; empty second drain → reload +
  `RuntimeError`) and `drain_canvas_log` (unwraps `result.result.value`; `[]` on
  missing).
- **`test_main_worker.py`** — delete the `test_ensure_zoom_*` block; the
  `_maybe_scan_enemies` tests stub `enemy_detect.scan_enemies` so they're
  unaffected (drop any `model_path` assertion).
- Full suite green.

## Risks / unknowns (flagged, not blockers)

- **The prototype patch in florr-auto-pathing's dedicated Chrome** — florragent
  proves the technique, but this repo's CDP launch flags / tab lifecycle differ.
  The `--remote-allow-origins=*` flag is already set (see
  `switch-server-cdp-not-clicks` memory). First live run is the real test.
- **`camera_from_frame` live failure rate** — if the player's own gold-body anchor
  is often ambiguous, enemy AI degrades to wander frequently. Unverified here.
- **English mob-name spellings** — `_SPECIES_ALIASES` may need 1–2 entries after a
  live check (florr may write "Sand Centipede" vs "Centipede", etc.).
- **Fixture drift** — florragent's capture format must match what the vendored
  hook produces; extracting the fixture from florragent's own capture keeps them
  consistent, and the vendored hook is byte-identical to florragent's.
