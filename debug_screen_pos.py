"""实时打印鼠标当前屏幕像素坐标, 用来量真实UI元素(按钮/下拉框)的坐标 ——
跟map_select.py/area_select.py不是一回事, 那两个是在存好的小地图图片上取点,
这个是量屏幕上任意位置的真实像素坐标(_START_BUTTON_POS这类常量就得靠这个量).

用法: 跑起来, 把鼠标移到你要量的位置停住, 读终端里刷出来的坐标, 想量下一个点
就挪过去接着读. Ctrl+C结束.
"""
import time
import pyautogui


def main():
    print("🖱️  实时坐标读取中, 把鼠标移到目标位置停住, 读下面刷出来的坐标 (Ctrl+C退出)\n")
    try:
        while True:
            x, y = pyautogui.position()
            print(f"\r当前坐标: ({x}, {y})   ", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n👋 结束")


if __name__ == "__main__":
    main()
