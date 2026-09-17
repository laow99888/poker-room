"""范围解析测试。"""

import pytest

from backend.ranges import InvalidHandError, expand_hand_code, expand_range


def test_pair_six_combos():
    combos = expand_hand_code("AA")
    assert len(combos) == 6
    assert ("Ac", "Ah") in combos
    assert all(a[0] == "A" and b[0] == "A" for a, b in combos)


def test_suited_four_offsuit_twelve_both_sixteen():
    assert len(expand_hand_code("AKs")) == 4
    assert all(a[1] == b[1] for a, b in expand_hand_code("AKs"))
    assert len(expand_hand_code("AKo")) == 12
    assert all(a[1] != b[1] for a, b in expand_hand_code("AKo"))
    assert len(expand_hand_code("AK")) == 16


def test_order_normalized():
    assert expand_hand_code("QKs") == expand_hand_code("KQs")


def test_plus_pair_range():
    combos = expand_range("TT+")
    assert len(combos) == 30  # TT JJ QQ KK AA，各 6 组合
    cards = {c for pair in combos for c in pair}
    assert "Ad" in cards and "Kc" in cards


def test_plus_kicker_range():
    combos = expand_range("A2s+")
    assert len(combos) == 48  # A2s..AKs 共 12 种 × 4
    assert len(expand_range("KJo+")) == 24  # KQo、KJo 各 12


def test_mixed_and_dedup():
    combos = expand_range("AKs, AKs, QQ")
    assert len(combos) == 4 + 6


def test_invalid_inputs():
    with pytest.raises(InvalidHandError):
        expand_range("AAs")       # 对子不带 s/o
    with pytest.raises(InvalidHandError):
        expand_range("KKs+, ZZ")  # 非法点数
    with pytest.raises(InvalidHandError):
        expand_range("2A s")      # 格式错误
    with pytest.raises(InvalidHandError):
        expand_range("  ")        # 空


def test_case_insensitive_codes_produce_identical_combos():
    """20：大小写混合输入必须等价——'kk' 不得产出小写组合再被当成死牌。"""
    assert expand_range("kk") == expand_range("KK")
    assert expand_range("aks,Ako") == expand_range("AKs,AKo")
    assert expand_hand_code("kk")[0][0].startswith("K")   # 点数归一为大写
