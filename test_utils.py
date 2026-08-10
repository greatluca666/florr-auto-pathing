from utils import if_in_area


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
