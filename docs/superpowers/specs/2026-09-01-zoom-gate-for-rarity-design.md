# Zoom gate for rarity detection — design

## Problem

`sample_rarity()` only reads a mob's rarity correctly when the game camera is
zoomed in far enough that the mob's HP bar renders ≥ ~4 px thick. Live `--watch`
on the real client: at bar thickness 2 (zoomed out) a real Mythic tag word gives
only 2–8 matching pixels — below the ratio floor — so every mob reads `Common`,
the Mythic latch never fires, and `select_action` picks a sandstorm instead of
the point-blank青怪. At thickness ≥ 4 the same mob reads `-> Mythic  Mythic=120+`.

The camera zoom is a manual scroll-wheel setting. The bot runs unattended, so it
must set the zoom itself before each farming round rather than relying on the user
to have zoomed in.

## Scope

In scope:
- A per-round, pre-farming step that scroll-wheel-zooms the camera in until nearby
  mobs' HP bars measure thick enough for rarity detection, then hands off to
  `auto_farming()`.
- A small reusable helper that measures HP-bar thickness across a detection list.

Not in scope:
- Zoom-out (the failure mode is always "too far out"; we only ever zoom in).
- Any change to `sample_rarity` / the rarity math itself — this is a camera-setup
  step, not a detection fix.
- Running when `enemy_ai_enabled` is false — zoom only matters for rarity, which
  only the enemy layer consumes.
- The pre-existing sandstorm red-contamination and sand_centipede no-bar issues.

## Behaviour

### `ensure_zoom_for_rarity()` — the scroll loop

Signature `ensure_zoom_for_rarity(enemy_ai_enabled) -> bool` (the flag is passed
in, matching `_maybe_scan_enemies`'s style; `run_worker` already holds it as
`w["enemy_ai_enabled"]`). Called once per round in `run_worker()`, immediately
after `lazy_theta_pathing()` reports the player reached the farming area and
**before** `auto_farming()`. Returns `False` at once (no scan, no scroll) when
`enemy_ai_enabled` is false. Overlay state `"调整视角"` while it runs. Best-effort: any exception is caught, logged as one
warning line, and the round proceeds to `auto_farming()` anyway.

Per iteration:

1. If `afk_watch.poll_afk_pause()` → `sleep(0.2)`, `continue` (don't count the
   iteration). Same pattern as `move_to_position` / `lazy_theta_pathing`.
2. `detections = enemy_detect.scan_enemies(model_path=ENEMY_MODEL_PATH)` (fresh
   screenshot inside).
3. `thicks = enemy_detect.measure_hp_bar_thickness(detections, <that screenshot>)`.
   `scan_enemies` currently takes its own screenshot and discards it — the loop
   takes one screenshot of its own (`pyautogui.screenshot` → BGR, same as
   `scan_enemies` does) and passes it to **both** `scan_enemies(image=...)` and
   `measure_hp_bar_thickness`, so the boxes and the thickness measurement are from
   the same frame.
4. **< `ZOOM_MIN_SAMPLES` (2) thicknesses** (few/no mobs on screen, or none with a
   locatable bar): do **not** scroll. `sleep(2)`. If total elapsed ≥
   `ZOOM_WAIT_CAP` (60 s) → log "视角调整: 周围一直没有可测的怪, 照常开刷" and
   return. Else `continue`.
5. `median = statistics.median(thicks)`.
   - `median >= ZOOM_MIN_THICK` (4) → log "视角OK (血条中位厚度 {median})" and
     return (success).
   - Else zoom in: `pyautogui.moveTo(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)`,
     `pyautogui.scroll(scroll_amount)`, `sleep(0.4)`. Increment `scroll_count`.
6. **Direction self-correction:** keep `prev_median`. On an iteration that just
   scrolled, if `median < prev_median - 0.5` (thinner than before → we zoomed the
   wrong way) → `scroll_amount = -scroll_amount` and log "视角: 滚轮方向反了, 已翻转".
   Only flip once per run is enough in practice, but the guard is unconditional
   (a second wrong-direction reading flips it back).
7. `scroll_count >= ZOOM_MAX_SCROLLS` (15) → log "视角调整: 滚了 15 次仍没到目标厚度
   (可能已到最大 zoom), 照常开刷" and return.

Return value: `bool` (reached target or not) — logged by the caller, not
otherwise acted on (the round always proceeds).

### `enemy_detect.measure_hp_bar_thickness(detections, image)` — the helper

```
def measure_hp_bar_thickness(detections, image) -> list[int]:
    """每个检测框跑 _find_hp_bar, 收集找到的血条厚度 (第4个返回值), 跳过 None.
    顺序跟 detections 一致. 用来判断相机 zoom 够不够 sample_rarity 工作."""
    out = []
    for d in detections:
        bar = _find_hp_bar(image, d["bbox"])
        if bar is not None:
            out.append(bar[3])
    return out
```

Pure, no I/O. `_find_hp_bar` already exists and returns
`(bar_x0, bar_y, bar_x1, thick)`.

## Architecture

### `enemy_detect.py`

- Add `measure_hp_bar_thickness(detections, image) -> list[int]` next to
  `_find_hp_bar` / `sample_rarity`.

### `main.py`

- Tuning block (`# ===== 索敌配置 =====`) gains:
  ```
  ZOOM_MIN_THICK    = 4    # 血条中位厚度到这个像素数, sample_rarity 才稳
  ZOOM_MIN_SAMPLES  = 2    # 至少要几条血条样本才据此判定 (少于就等 mob)
  ZOOM_SCROLL_AMOUNT = 2   # 每次滚轮往里推的量 (正=拉近; 方向不对循环里会自翻转)
  ZOOM_MAX_SCROLLS  = 15   # 滚这么多次还没到就放弃 (可能已是最大 zoom)
  ZOOM_WAIT_CAP     = 60   # 周围没 mob 时最多等这么多秒, 之后照常开刷
  ```
- Add `ensure_zoom_for_rarity() -> bool` near `_maybe_scan_enemies`.
- `run_worker()`: after `if lazy_theta_pathing(...)` succeeds and before
  `auto_farming(...)`, call `ensure_zoom_for_rarity()` (its own internal
  `enemy_ai_enabled` guard means the call site doesn't need to check).

`scan_enemies` needs an `image=` pass-through — it already has one
(`scan_enemies(image=None, ...)`), so no change there.

## Error handling

- `ensure_zoom_for_rarity` wraps its body in `try/except Exception` → log
  `⚠️ 视角调整出错, 照常开刷: {e}` and return `False`. It must never break the
  round loop (same posture as `_maybe_scan_enemies`).
- `pyautogui.screenshot` / `scroll` failure → caught by the same wrapper.
- Worker stop (SIGTERM / stdin EOF) interrupts the `sleep` calls normally — no
  special handling.

## Testing

`test_enemy_detect.py`:
- `measure_hp_bar_thickness` — synthetic image with two green HP bars of known
  thickness → returns `[t1, t2]`; a detection whose bbox has no bar is skipped;
  empty detections → `[]`.

`test_main_worker.py` (monkeypatch style already used for `move_to_position` /
`_maybe_scan_enemies`):
- Stub `enemy_detect.scan_enemies`, `enemy_detect.measure_hp_bar_thickness`,
  `main.pyautogui.scroll` / `moveTo` / `screenshot`, `main.time.sleep`,
  `main.overlay`, `main.afk_watch.poll_afk_pause`.
- **reaches target:** `measure_hp_bar_thickness` returns `[2,2]` then `[3,3]` then
  `[4,4]` on successive calls → returns `True`, `scroll` called exactly twice.
- **scroll cap:** always returns `[2,2]` → returns `False` after
  `ZOOM_MAX_SCROLLS` scrolls, no infinite loop.
- **no mobs then mobs:** returns `[]` twice (no scroll, sleeps) then `[4,4]` →
  `True`, `scroll` never called for the empty rounds.
- **wait cap:** always `[]`, with `time.sleep` advancing a fake clock → returns
  `False` at `ZOOM_WAIT_CAP`, `scroll` never called.
- **direction flip:** `[3,3]` → scroll → `[2,2]` (got worse) → next scroll uses the
  negated amount; assert the sign of the value passed to `scroll` flipped.
- **disabled:** `ensure_zoom_for_rarity(False)` returns immediately (`False`), no
  scan, no scroll.

## Tuning knobs (need real-machine calibration)

All five `ZOOM_*` constants are placeholder defaults. `ZOOM_SCROLL_AMOUNT`'s sign
in particular is a guess (the loop self-corrects, but a correct initial sign saves
one wasted scroll); `ZOOM_MAX_SCROLLS` and the `0.4 s` post-scroll settle time
depend on the client's zoom animation speed.
