"""隔离测试: switch_server()到底管不管用.

跟main.py整套循环逻辑完全无关, 只测一件事: 通过CDP执行cp6.forceServerID(...)
这个动作, 在真实游戏里到底会发生什么 —— 有没有真的换到新服务器、选完之后卡在
哪个画面(开局菜单/直接进局/别的中间状态)。上main.py无人值守主循环前务必先跑
一遍这个亲眼确认, 别让没验证过的新动作直接进自动重试逻辑(比照这项目里toggle_map
那次误触发的教训 —— 没验证的假设很容易在实机上跟预期不一样, 而且不容易从日志
倒推出哪一步错了).

用法: Chrome得用--remote-debugging-port=9222启动(见cdp_bridge.py模块文档),
florr.io标签页开着. 切到florr.io标签页确保它在最前面, 然后跑这个脚本.
5秒倒计时期间去切换窗口.
"""
import time
import pyautogui
from utils import (
    apply_map,
    switch_server,
    on_death_screen,
    on_start_screen,
    get_player_position,
)


def main():
    apply_map("desert")  # MAP全局变量不设就是空字符串, get_player_position()内部
                          # 拼路径会变成'./maps/.png', 读不到文件.
    print("🔍 换服务器隔离测试")
    print(f"   屏幕尺寸: {pyautogui.size()}")
    print("\n⏳ 5秒后开始, 这段时间切到florr.io标签页...\n")
    for i in range(5, 0, -1):
        print(f"   {i}...")
        time.sleep(1)

    print(f"\n📸 切换前: 鼠标当前位置 {pyautogui.position()}")
    before_pos = get_player_position()
    print(f"   切换前玩家位置(小地图坐标): {before_pos}")
    before_img = pyautogui.screenshot()
    before_img.save("./debug_before_switch.png")
    print("   已保存 debug_before_switch.png\n")

    print("🌐 执行 switch_server()...")
    server_id = switch_server()
    print(f"   选中的服务器码: {server_id}\n")

    time.sleep(2)  # 给画面转场留点时间
    print(f"📸 切换后: 鼠标停在 {pyautogui.position()}")
    print(f"   屏幕尺寸(切换后, 用来对比全屏有没有被顶掉): {pyautogui.size()}")
    after_pos = get_player_position()
    print(f"   切换后玩家位置(小地图坐标): {after_pos}")
    print(f"   on_death_screen()={on_death_screen()}  on_start_screen()={on_start_screen()}")

    after_img = pyautogui.screenshot()
    after_img.save("./debug_after_switch.png")
    print("   已保存 debug_after_switch.png\n")

    print("=" * 60)
    print("对比 debug_before_switch.png 和 debug_after_switch.png 这两张图, 确认:")
    print("  1. 屏幕尺寸切换前后一致 —— 不一致说明全屏被顶掉了")
    print("  2. 画面确实变了(重新进了一局/回到开局菜单) —— 没变说明forceServerID")
    print("     没真的执行/服务器码已失效")
    print("  3. on_start_screen()或on_death_screen()有没有命中, 命中了main.py现有")
    print("     逻辑就能接得住; 都没命中但玩家位置也测不到, 说明卡在了某个中间")
    print("     状态, 需要另外处理")
    print("一起把这两张图和上面几行日志发我")
    print("=" * 60)


if __name__ == "__main__":
    main()
