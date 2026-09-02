"""诊断: canvas 解码到底从画面里读出了什么怪.

用途 —— "识别不到 msandstorm / usandstorm" 这类问题, 先跑这个看 canvas_decode
到底给了什么, 再决定改 _species_from_name / _tier_from_color / 还是 hook。

跑法 (florr.io 已在那个带 --remote-debugging-port=9222 的 Chrome 里打开, 画面里
有目标怪):

    python debug_canvas_enemies.py

它会连续 drain 几秒, 取最新一帧完整记录, 打印:
  1. 这一帧的原始统计 (记录数 / 帧号跨度 / 血条锚点数 / 文本记录数)
  2. 每个血条块 (nameplate block): anchor / hp / 所有文本 + 每条文本的 fill 色
  3. mobs_from_frame 的结果: name / rarity / rarity_color / hp / 屏幕坐标
  4. 每个 mob 过 _species_from_name / _tier_from_color 之后变成什么
  5. scan_enemies() 最终返回的检测列表
不改游戏, 只读。
"""
import time

import cdp_bridge
import canvas_decode
import enemy_detect


def _drain_for(seconds=3.0):
    """反复 drain, 把这段时间内的所有绘制记录攒起来。"""
    buf = []
    try:
        cdp_bridge.inject_canvas_hook()
    except RuntimeError as e:
        print(f"⚠️ inject_canvas_hook 抛错: {e}")
        print("   (hook 没装上 —— 后面基本会是空的; 先重进游戏再跑)")
    end = time.time() + seconds
    while time.time() < end:
        try:
            buf.extend(cdp_bridge.drain_canvas_log())
        except Exception as e:  # noqa: BLE001  诊断脚本, 什么都想看见
            print(f"⚠️ drain_canvas_log 抛错: {type(e).__name__}: {e}")
            break
        time.sleep(0.1)
    return buf


def main():
    raw = _drain_for(3.0)
    print(f"\n=== 原始 drain: {len(raw)} 条记录 ===")
    if not raw:
        print("空。hook 没生效, 或者画面里什么都没画 (florr 标签页在后台?)。")
        return

    frames = canvas_decode.group_by_frame(raw)
    keys = sorted(frames)
    print(f"帧号: {len(keys)} 个不同值, 范围 {keys[0]}..{keys[-1]}")
    if len(keys) < 2:
        print("⚠️ 少于 2 个不同帧号 —— __canvasFrame 没在推进 (florr 抓着 patch 之前的")
        print("   requestAnimationFrame 引用)。canvas 解码这时候永远是空的。重进游戏。")
        return

    recs = frames[keys[-2]]  # 最新那帧可能没画完, 取次新
    ops = {}
    texts = []
    for r in recs:
        ops[r.get("op")] = ops.get(r.get("op"), 0) + 1
        if r.get("op") == "text":
            texts.append((r.get("text"), r.get("fill")))
    print(f"\n=== 次新帧 (frame {keys[-2]}): {len(recs)} 条 ===")
    print(f"op 分布: {ops}")
    print(f"文本记录 {len(texts)} 条:")
    for t, fill in texts:
        print(f"    text={t!r:24}  fill={fill!r}")

    print("\n=== _bar_blocks (nameplate 块) ===")
    blocks = canvas_decode._bar_blocks(recs)
    if not blocks:
        print("一个都没有。画面里没有带 #222222 血条的怪 —— 或者 sandstorm 根本不画")
        print("标准 nameplate (那样 canvas 解码结构上就看不到它, 得走 body 特征那条路)。")
    for i, b in enumerate(blocks):
        print(f"  块#{i}: anchor=({b['anchor'][0]:.0f},{b['anchor'][1]:.0f}) hp={b['hp']}")
        for t, c in zip(b["texts"], b["text_colors"]):
            print(f"        text={t!r:24}  fill={c!r}")

    print("\n=== camera_from_frame ===")
    try:
        cam = canvas_decode.camera_from_frame(recs)
        print(f"  zoom={cam['zoom']:.4f}  player_world={cam['player_world']}  "
              f"player_screen={cam['player_screen']}")
    except ValueError as e:
        print(f"  ⚠️ 抛 ValueError: {e}")
        print("  (这一帧解不出相机 —— mobs_from_frame 也就没法跑; 换一帧 / 靠近一个怪再试)")
        return

    print("\n=== mobs_from_frame -> 映射 ===")
    mobs = canvas_decode.mobs_from_frame(recs, cam)
    if not mobs:
        print("  空。有 nameplate 块但都被当成玩家自己 / 别的玩家过滤掉了, 或者没有块。")
    for m in mobs:
        sp = enemy_detect._species_from_name(m.get("name"))
        tier = enemy_detect._tier_from_color(m.get("rarity_color"))
        print(f"  name={m.get('name')!r:20} rarity_word={m.get('rarity')!r:12} "
              f"rarity_color={m.get('rarity_color')!r:10} hp={m.get('hp')}")
        print(f"      -> _species_from_name={sp!r}   _tier_from_color={tier!r}   "
              f"screen=({m['sx']:.0f},{m['sy']:.0f})")
        if sp is None:
            print("      ✗ species=None -> 这个 mob 被 scan_enemies 丢掉")
        elif tier == "Common" and m.get("rarity_color") not in (None, "#7EEF6D"):
            print(f"      ✗ rarity_color {m.get('rarity_color')!r} 不在 _RANK_BY_RARITY_COLOR 里 "
                  f"-> 当成 Common")

    print("\n=== enemy_detect.scan_enemies() 最终结果 ===")
    enemy_detect._frame_buffer[:] = []
    dets = enemy_detect.scan_enemies()
    if not dets:
        print("  []  (scan_enemies 没返回任何检测)")
    for d in dets:
        print(f"  {d}")


if __name__ == "__main__":
    main()
