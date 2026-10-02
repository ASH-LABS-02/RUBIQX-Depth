from types import SimpleNamespace as C

from depthwizard.io import _rgb_band_order


def test_panchromatic_and_rgb():
    assert _rgb_band_order(1) == [1, 1, 1]
    assert _rgb_band_order(3) == [1, 2, 3]


def test_multispectral_bgrn_is_reordered():
    assert _rgb_band_order(4) == [3, 2, 1]
    assert _rgb_band_order(4, [C(name="gray")] * 4) == [3, 2, 1]


def test_tags_and_alpha_win():
    tagged = [C(name=n) for n in ("blue", "green", "red", "undefined")]
    assert _rgb_band_order(4, tagged) == [3, 2, 1]
    rgba = [C(name=n) for n in ("red", "green", "blue", "alpha")]
    assert _rgb_band_order(4, rgba) == [1, 2, 3]
