import numpy as np

from utils import if_in_area, _ensure_grayscale_2d, next_server_option


def test_if_in_area_normal_corner_order():
    area = [(0, 0), (10, 10)]
    assert if_in_area([area], (5, 5)) is True
    assert if_in_area([area], (20, 20)) is False


def test_if_in_area_flipped_x_corner_order():
    # main.py的farming_area实际写的是[(20, 15), (9, 76)] —— 第一个角x比第二个角
    # x还大. 之前if_in_area直接假设area[0]是"左上角"、area[1]是"右下角", 对这种
    # 顺序会导致x区间变成 20<=x<=9 永远判不出True, 玩家哪怕站在区域正中间都会被
    # 判定"不在区域内".
    area = [(20, 15), (9, 76)]
    assert if_in_area([area], (16, 46)) is True
    assert if_in_area([area], (14, 45)) is True
    assert if_in_area([area], (0, 0)) is False
    assert if_in_area([area], (25, 50)) is False


def test_if_in_area_flipped_y_corner_order():
    area = [(0, 76), (10, 15)]
    assert if_in_area([area], (5, 45)) is True
    assert if_in_area([area], (5, 100)) is False


def test_if_in_area_checks_multiple_areas():
    areas = [[(0, 0), (5, 5)], [(20, 20), (25, 25)]]
    assert if_in_area(areas, (22, 22)) is True
    assert if_in_area(areas, (12, 12)) is False


def test_ensure_grayscale_2d_squeezes_trailing_channel_dim():
    # 复现实测撞到的场景: cv2.imread(path, cv2.IMREAD_GRAYSCALE)在某些
    # OpenCV/平台组合(Windows)下吐出(H,W,1)而不是纯(H,W), 导致下游
    # calibrate_player()的`rows, cols = map.shape`拆包报错.
    img_3d = np.zeros((10, 20, 1), dtype=np.uint8)
    img_3d[3, 5, 0] = 255
    result = _ensure_grayscale_2d(img_3d)
    assert result.shape == (10, 20)
    assert result[3, 5] == 255


def test_ensure_grayscale_2d_leaves_true_2d_untouched():
    img_2d = np.zeros((10, 20), dtype=np.uint8)
    img_2d[3, 5] = 255
    result = _ensure_grayscale_2d(img_2d)
    assert result.shape == (10, 20)
    assert result[3, 5] == 255


def test_ensure_grayscale_2d_passes_through_none():
    # cv2.imread在文件读不到时返回None(不是抛异常) —— 不能让形状归一化
    # 逻辑在这种情况下自己先炸了(None.ndim会AttributeError).
    assert _ensure_grayscale_2d(None) is None


def test_next_server_option_never_repeats_consecutively():
    # 不依赖模块级_last_server_index的起始值(测试跑的顺序会影响它) —— 只断言
    # "连续两次调用不会选中同一个", 不断言具体从哪个开始.
    positions = [(1, 1), (2, 2), (3, 3)]
    previous = next_server_option(positions)
    for _ in range(10):
        current = next_server_option(positions)
        assert current != previous
        previous = current


def test_next_server_option_cycles_through_all_positions():
    # 连续调用len(positions)次, 应该覆盖到每一个选项至少一次(轮换, 不是随机漏选).
    positions = [(1, 1), (2, 2), (3, 3)]
    seen = {next_server_option(positions) for _ in range(len(positions))}
    assert seen == set(positions)


def test_next_server_option_defaults_to_desert_dropdown_positions():
    # 不传参数时用模块内置的三个沙漠服务器选项坐标, 不是空列表/报错.
    from utils import _SERVER_OPTION_POSITIONS
    result = next_server_option()
    assert result in _SERVER_OPTION_POSITIONS
