import pytest

from canvas_decode import (
    group_by_frame, camera_from_frame, screen_to_world, mobs_from_frame,
    point_blank_shielded_mob,
)
from canvas_frame_fixtures import (
    ZOOM, arc_rec, minimap_rec, text_rec, healthbar_recs, nameplate, player_recs,
    gameplay_frame,
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
    # florr draws a nameplate as bar strokes, then name text x2, then rarity text x2, all
    # contiguous at the mob's anchor. Build the frame from primitives so the Mythic rarity
    # override lands inside that run rather than after gameplay_frame's trailing minimap draw
    # (which is outside the block's contiguous text scan). The assertion targets are what
    # matter: mobs_from_frame reads name[0], rarity[1], rarity_color, hp.
    ax, ay = 400.0, 200.0
    recs = (
        list(player_recs(0))
        + healthbar_recs(0, ax, ay, hp=0.5)
        + [text_rec(0, ax - 34, ay + 39, "Beetle")] * 2
        + [text_rec(0, ax + 8, ay + 60, "Mythic", "#1FDBDE")] * 2
        + [minimap_rec(0, 5640.0, 6911.0)]
    )
    mob = mobs_from_frame(recs, camera_from_frame(recs))[0]
    assert mob["name"] == "Beetle"
    assert mob["rarity"] == "Mythic"
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


def _labelled(ax, ay, name, rarity, color):
    return ([text_rec(0, ax - 21, ay + 39, name)] * 2
            + [text_rec(0, ax + 10, ay + 48, rarity, color)] * 2)


def test_batched_bars_then_batched_labels_first_mob_recovered():
    # florr at desert density draws several mobs' HP bars back to back, THEN their name +
    # rarity text back to back. Old stream-order consumption gave the first mob in the batch
    # an empty nameplate and dropped it — a point-blank Mythic sandstorm was lost this way.
    recs = list(player_recs(0))
    recs += healthbar_recs(0, 400.0, 200.0, hp=1.0)          # mob A bars
    recs += healthbar_recs(0, 700.0, 500.0, hp=1.0)          # mob B bars, straight after
    recs += _labelled(400.0, 200.0, "Sandstorm", "Mythic", "#1FDBDE")   # A labels
    recs += _labelled(700.0, 500.0, "Beetle", "Legendary", "#DE1F1F")   # B labels
    recs += [minimap_rec(0, 5000.0, 6000.0)]

    got = {m["name"]: m["rarity_color"] for m in mobs_from_frame(recs, camera_from_frame(recs))}
    assert got == {"Sandstorm": "#1FDBDE", "Beetle": "#DE1F1F"}


def test_stacked_nameplates_no_cross_contamination():
    # 3 nameplates ~40px apart (a corner pile at min zoom). Clean per-mob draw order.
    # Nearest-anchor assignment let a neighbour's rarity word land in slot 0 (name); each
    # mob must keep its OWN rarity, read from its OWN label run.
    recs = list(player_recs(0))
    for ax, ay, rarity, color in [(120.0, 1040.0, "Rare", "#4D52E3"),
                                  (150.0, 1048.0, "Epic", "#861FDE"),
                                  (95.0, 1055.0, "Unusual", "#FFE65D")]:
        recs += healthbar_recs(0, ax, ay, hp=1.0)
        recs += _labelled(ax, ay, "Fire Ant", rarity, color)
    recs += [minimap_rec(0, 5000.0, 6000.0)]

    mobs = mobs_from_frame(recs, camera_from_frame(recs))
    assert all(m["name"] == "Fire Ant" for m in mobs)
    assert sorted(m["rarity"] for m in mobs) == ["Epic", "Rare", "Unusual"]


def _bar_stroke(frame, ax, ay, color, width):
    return {"frame": frame, "op": "stroke", "x": ax, "y": ay, "r": None,
            "bbox": [ax - 30, ay, ax - 30 + width, ay], "n": 2, "fill": "#FFFFFF",
            "stroke": color, "lw": 6, "alpha": 1, "m": [ZOOM, 0, 0, ZOOM, ax, ay]}


def _shield_bar(frame, ax, ay, hp):
    # observed live on the point-blank Mythic sandstorm: two #222222 backgrounds with a
    # #42E3F5 cyan bar between them, then the damage track and value bar. No nameplate text.
    return [_bar_stroke(frame, ax, ay, "#222222", 60.0),
            _bar_stroke(frame, ax, ay, "#42E3F5", 40.0),
            _bar_stroke(frame, ax, ay, "#222222", 60.0),
            _bar_stroke(frame, ax, ay, "#DD3434", 60.0),
            _bar_stroke(frame, ax, ay, "#75DD34", 60.0 * hp)]


def test_point_blank_shielded_mob_detected_on_player_anchor():
    recs = list(player_recs(0, 693.8, 472.5))
    recs += healthbar_recs(0, 1200.0, 800.0, hp=1.0)                       # a normal ranged mob
    recs += _labelled(1200.0, 800.0, "Sandstorm", "Legendary", "#DE1F1F")
    recs += _shield_bar(0, 693.8, 472.5, 0.66)                            # the point-blank one
    recs += [minimap_rec(0, 2200.0, 11500.0)]
    cam = camera_from_frame(recs)

    pb = point_blank_shielded_mob(recs, cam)
    assert pb is not None and abs(pb["hp"] - 0.66) < 0.02
    assert pb["sx"] == 693.8 and pb["sy"] == 472.5


def test_point_blank_shielded_mob_none_without_cyan_bar_or_off_anchor():
    base = list(player_recs(0, 600.0, 453.5)) + [minimap_rec(0, 2200.0, 11500.0)]
    # a plain (no cyan) bar on the player anchor -> not the shielded case
    plain = base + healthbar_recs(0, 600.0, 453.5, hp=0.5)
    assert point_blank_shielded_mob(plain, camera_from_frame(plain)) is None
    # a cyan-bar mob but 200px off the player anchor -> a normal shielded mob, not point-blank
    off = base + healthbar_recs(0, 900.0, 700.0, hp=1.0) + _shield_bar(0, 900.0, 700.0, 0.8)
    assert point_blank_shielded_mob(off, camera_from_frame(off)) is None
