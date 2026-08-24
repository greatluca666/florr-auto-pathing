"""最小化隔离测试: 单独测"点这一个坐标到底有没有生效", 不掺杂Esc/开下拉框
那些步骤 —— switch_server()换了3版点击方式/坐标都没能让"服务器"框的值变化,
需要把"点击本身有没有传进游戏"这一件事单独摘出来测, 排除掉其它步骤的干扰.

用法: 你自己手动先把设置面板打开、服务器下拉展开(眼睛能看见Juliett那一行),
然后跑这个脚本. 倒计时结束后它只做一件事: 在(233, 305)点一下(默认坐标, 也可以
传别的坐标进来测), 点前点后各截一张图存下来.
"""
import sys
import time
import pyautogui


def main():
    x, y = (233, 305)
    if len(sys.argv) == 3:
        x, y = int(sys.argv[1]), int(sys.argv[2])

    print("🔍 单点点击隔离测试")
    print(f"   目标坐标: ({x}, {y})")
    print("\n⏳ 5秒后点击, 这段时间自己把设置面板+服务器下拉打开...\n")
    for i in range(5, 0, -1):
        print(f"   {i}...")
        time.sleep(1)

    before_img = pyautogui.screenshot()
    before_img.save("./debug_click_before.png")
    print(f"📸 点击前截图已保存: debug_click_before.png (鼠标当前 {pyautogui.position()})")

    pyautogui.moveTo(x, y)
    time.sleep(0.2)
    print(f"🖱️  moveTo后鼠标实际位置: {pyautogui.position()} (应该等于目标坐标)")
    pyautogui.click()
    time.sleep(0.5)

    after_img = pyautogui.screenshot()
    after_img.save("./debug_click_after.png")
    print("📸 点击后截图已保存: debug_click_after.png")
    print("\n对比两张图, 看'服务器'框显示的值变没变.")


if __name__ == "__main__":
    main()
