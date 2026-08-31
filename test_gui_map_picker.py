import pytest

from gui_map_picker import View, image_to_widget, widget_to_image, clamp_image_point


def test_image_to_widget_applies_scale_and_offset():
    v = View(s=2.0, offset_x=10.0, offset_y=20.0, img_w=300, img_h=300)
    assert image_to_widget(0, 0, v) == (10.0, 20.0)
    assert image_to_widget(5, 7, v) == (20.0, 34.0)


def test_widget_to_image_is_inverse_of_image_to_widget():
    v = View(s=1.5, offset_x=12.0, offset_y=8.0, img_w=300, img_h=300)
    for ix, iy in [(0, 0), (150, 150), (299, 299), (33, 210)]:
        wx, wy = image_to_widget(ix, iy, v)
        assert widget_to_image(wx, wy, v) == (ix, iy)


def test_widget_to_image_rounds_to_nearest_pixel():
    v = View(s=4.0, offset_x=0.0, offset_y=0.0, img_w=300, img_h=300)
    assert widget_to_image(9.0, 9.0, v) == (2, 2)    # 2.25 -> 2
    assert widget_to_image(13.0, 13.0, v) == (3, 3)  # 3.25 -> 3


def test_widget_to_image_clamps_outside_the_image():
    v = View(s=2.0, offset_x=0.0, offset_y=0.0, img_w=300, img_h=300)
    assert widget_to_image(-50.0, -50.0, v) == (0, 0)
    assert widget_to_image(99999.0, 99999.0, v) == (299, 299)


def test_clamp_image_point():
    assert clamp_image_point(-1, 500, 300, 300) == (0, 299)
    assert clamp_image_point(10, 20, 300, 300) == (10, 20)
