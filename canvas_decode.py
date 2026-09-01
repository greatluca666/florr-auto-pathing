"""Load canvas draw-call logs and decode them into per-frame game state.

The camera is READ, not estimated. florr.io hands us everything we need inside the draw calls:

  * every draw belonging to one visual entity is emitted under that entity's own CTM, so the
    matrix translation `m[4], m[5]` IS the entity's screen anchor — grouping by it aggregates
    body + outline + eyes + health bar into one entity with no spatial clustering heuristics;
  * mob nameplates (a 3-stroke health bar plus name and rarity text) are drawn at the plain
    camera scale, which gives the world->screen zoom exactly;
  * the minimap draws the player under a tiny CTM whose pre-transform coordinate is the
    player's ABSOLUTE world position, recoverable as `(x - m[4]) / m[0]`.

An earlier version estimated camera motion from the median displacement of appearance-matched
draws. That assumption ("most on-screen content is world-static") is false for this game — the
player's own petals and the mobs outnumber static scenery — and it is unnecessary given the
above, so it was removed rather than tuned. Everything here fails loudly (ValueError) rather
than returning coordinates it cannot justify.
"""
# VENDORED from /Users/macmima1234/florragent/scripts/canvas_decode.py (2026-09-01). Trimmed to
# the camera + mob subset florr-auto-pathing's enemy_detect needs. Keep in sync with upstream.
import math
import re
from pathlib import Path
import json

# Draw-call signatures observed in data/raw_captures/canvas_move_right_only.ndjson (see render_spec.md).
MINIMAP_MAX_SCALE = 0.05      # minimap CTM scale is ~0.0084; world draws are ~0.76+
HEALTHBAR_BG = "#222222"      # nameplate bar: dark background (full width)
HEALTHBAR_DAMAGE = "#DD3434"  # red damage track
HEALTHBAR_SECONDARY = "#42E3F5"  # a narrower cyan bar some entities draw above the health bar
PLAYER_BODY_COLOR = "#FFE763" # the player flower's gold body, and its minimap dot
ABSORB_FRACTION = 1.0         # sub-anchors within this fraction of a body's radius belong to it

# Another player's nameplate shows their account level ("2级") in the slot a mob's rarity tier
# name would occupy. No real mob rarity name has this shape (they're all quality words --
# 普通/不凡/稀有/etc. -- never a bare number). See
# docs/superpowers/specs/2026-08-18-alone-and-danger-awareness-design.md.
PLAYER_RARITY_PATTERN = re.compile(r"^\d+级$")

# UI-space fills (the petal inventory bar, HUD icons) render at CTM scale ~1.0, distinct from
# the world/camera zoom (measured 0.7-0.95 throughout this project) and the minimap's ~0.01.
# Tightened to 0.01 to exclude HUD avatar-card eyes (scale ~1.05) that falsely matched at 0.05.
INVENTORY_UI_SCALE_TOL = 0.01

# How close a player-style nameplate ("37级") must be to a gold-body candidate's screen anchor
# to count as belonging to it, when camera_from_frame needs to break a same-radius tie between
# multiple gold flower bodies (see that function). Measured live 2026-08-19 (main account, a
# crowded area) at ~17-20px between a real other-player's nameplate and their own body -- 100px
# leaves generous margin without risking a match onto an unrelated nearby nameplate.
SELF_DISAMBIGUATION_RADIUS = 100.0


def _is_player_block(texts):
    return len(texts) > 1 and PLAYER_RARITY_PATTERN.match(texts[1]) is not None


def load_ndjson(path):
    records = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def group_by_frame(records):
    frames = {}
    for r in records:
        frames.setdefault(r["frame"], []).append(r)
    return frames


def _scale(rec):
    m = rec.get("m")
    return math.hypot(m[0], m[1]) if m else 0.0


def _anchor(rec):
    m = rec["m"]
    return (m[4], m[5])


def _is_minimap(rec):
    return rec.get("m") is not None and _scale(rec) < MINIMAP_MAX_SCALE


def _is_rotated(rec):
    return abs(rec["m"][1]) > 1e-9


def camera_from_frame(records):
    """Read the camera for one frame: {"zoom", "player_world", "player_screen"}.

    zoom comes from a nameplate health bar (drawn unrotated at the camera scale), the absolute
    world position from the minimap player dot, and the screen anchor from the player's own gold
    body. Raises ValueError if any of the three is missing — a frame with no mob on screen has no
    scale reference, and guessing one would silently corrupt every world coordinate derived from it.
    """
    zoom = None
    for r in records:
        if r["op"] == "stroke" and r.get("stroke") == HEALTHBAR_BG and not _is_minimap(r):
            zoom = r["m"][0]
            break

    player_world = None
    for r in records:
        if _is_minimap(r) and r.get("fill") == PLAYER_BODY_COLOR and r.get("r") is not None:
            m = r["m"]
            player_world = ((r["x"] - m[4]) / m[0], (r["y"] - m[5]) / m[3])
            break

    player_screen = None
    if zoom is not None:
        candidates = [r for r in records
                      if (r["op"] == "fill" and r.get("r") is not None
                          and r.get("fill") == PLAYER_BODY_COLOR
                          and not _is_minimap(r) and not _is_rotated(r)
                          and abs(_scale(r) - zoom) < 1e-6)]   # excludes the larger UI-card avatar
        if candidates:
            max_r = max(c["r"] for c in candidates)
            largest = [c for c in candidates if abs(c["r"] - max_r) < 1e-6]
            if len(largest) > 1:
                # A same-radius tie: other real players can share the exact default flower
                # appearance (confirmed live 2026-08-19, main account, a crowded area -- three
                # identical gold bodies at once). Break it with a second, independent signal:
                # OTHER players draw a floating level nameplate ("37级") near their own body;
                # the env's own player does not (its HP is read from a separate UI element, not
                # a nameplate -- see player_from_frame). The candidate with NO nearby
                # player-style nameplate is presumed to be self.
                blocks = _bar_blocks(records)
                without_nameplate = [
                    c for c in largest
                    if not any(
                        _is_player_block(b["texts"])
                        and math.hypot(b["anchor"][0] - c["m"][4], b["anchor"][1] - c["m"][5])
                            <= SELF_DISAMBIGUATION_RADIUS
                        for b in blocks
                    )
                ]
                if len(without_nameplate) == 1:
                    largest = without_nameplate
            # Exactly one candidate (after the nameplate tie-break, if it applied) is genuinely
            # our own player. A tie that STILL isn't resolved (no candidate has a nameplate --
            # possible if the account is completely alone and something else glitches -- or
            # more than one candidate lacks one) can't be broken further -- fail loud rather
            # than silently picking one by draw order.
            if len(largest) == 1:
                player_screen = _anchor(largest[0])

    missing = [n for n, v in (("zoom", zoom), ("player_world", player_world),
                              ("player_screen", player_screen)) if v is None]
    if missing:
        raise ValueError(f"camera undetermined for this frame, missing: {', '.join(missing)}")
    return {"zoom": zoom, "player_world": player_world, "player_screen": player_screen}


def screen_to_world(x, y, camera):
    """Convert a screen anchor to absolute world coordinates, anchored on the player."""
    px, py = camera["player_screen"]
    wx, wy = camera["player_world"]
    z = camera["zoom"]
    return (wx + (x - px) / z, wy + (y - py) / z)


def _bar_width(rec):
    bbox = rec.get("bbox")
    return (bbox[2] - bbox[0]) if bbox else 0.0


def _bar_blocks(records, label_radius=100.0):
    """Yield one dict per nameplate block in draw order.

    A block is the run of bar strokes sharing an anchor, followed by its label text. Each
    coloured bar is measured against the most recent `#222222` background, because entities
    that draw a second, narrower cyan bar give each bar its own background.

    The remaining-health bar is identified by position, not colour: it is the stroke drawn
    immediately after the `#DD3434` damage track. Its colour is a damage-flash gradient, so
    matching a fixed green would miss every entity mid-flash.
    """
    blocks = []
    i, n = 0, len(records)
    while i < n:
        r = records[i]
        if not (r["op"] == "stroke" and r.get("stroke") == HEALTHBAR_BG and not _is_minimap(r)):
            i += 1
            continue
        anchor = _anchor(r)
        bg_width, hp, secondary = _bar_width(r), None, None
        value_pending = False
        while i < n and records[i]["op"] == "stroke" and _anchor(records[i]) == anchor:
            stroke, width = records[i].get("stroke"), _bar_width(records[i])
            if stroke == HEALTHBAR_BG:
                bg_width = width
                value_pending = False
            elif stroke == HEALTHBAR_DAMAGE:
                value_pending = True          # the next stroke is the remaining-health bar
            elif stroke == HEALTHBAR_SECONDARY and bg_width:
                secondary = width / bg_width
            elif value_pending and bg_width:
                hp = width / bg_width
                value_pending = False
            i += 1
        texts = []
        text_colors = []
        while i < n and records[i]["op"] == "text":
            label = _anchor(records[i])
            if math.hypot(label[0] - anchor[0], label[1] - anchor[1]) <= label_radius:
                t = records[i].get("text")
                if t not in texts:
                    texts.append(t)
                    text_colors.append(records[i].get("fill"))
            i += 1
        blocks.append({"anchor": anchor, "hp": hp, "secondary": secondary, "texts": texts,
                        "text_colors": text_colors})
    return blocks


def _is_player_anchor(anchor, camera, tol=1.0):
    px, py = camera["player_screen"]
    return math.hypot(anchor[0] - px, anchor[1] - py) <= tol


def mobs_from_frame(records, camera, label_radius=100.0):
    """Parse nameplate blocks into mobs: name, rarity, health fraction, screen and world position.

    florr.io emits one block per mob in draw order — the bar strokes at the mob's anchor, then
    the name text, then the rarity text, each drawn twice (stroke pass then fill pass). Label
    text is only claimed within `label_radius` of the bar anchor, so a banner drawn straight
    after a nameplate is not mistaken for that mob's name, and a bar with no nameplate reports
    no name. The block at the player's own anchor is excluded — see `player_from_frame`.
    """
    mobs = []
    for block in _bar_blocks(records, label_radius):
        if _is_player_anchor(block["anchor"], camera):
            continue
        if _is_player_block(block["texts"]):
            continue
        ax, ay = block["anchor"]
        wx, wy = screen_to_world(ax, ay, camera)
        texts = block["texts"]
        colors = block["text_colors"]
        mobs.append({
            "name": texts[0] if texts else None,
            "rarity": texts[1] if len(texts) > 1 else None,
            "rarity_color": colors[1] if len(colors) > 1 else None,
            "hp": block["hp"],
            "sx": ax, "sy": ay, "x": wx, "y": wy,
        })
    return mobs
