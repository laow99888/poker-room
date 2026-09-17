"""手牌代码与范围字符串解析。

支持写法（逗号分隔、大小写不敏感）：
  AA        对子，6 个具体组合
  AK        同点数全部 16 个组合（同花 + 不同花）
  AKs/AKo   同花 4 个 / 不同花 12 个组合
  TT+       TT 到 AA 全部对子
  A2s+      A2s 到 AKs 全部同花 A
  KJo+      KJo、KQo
"""


class InvalidHandError(ValueError):
    """手牌代码或范围字符串不合法。"""


RANKS = "AKQJT98765432"  # 点数，从高到低
SUITS = "cdhs"


def _rank_index(rank: str) -> int:
    r = rank.upper().replace("10", "T")
    if len(r) != 1 or r not in RANKS:
        raise InvalidHandError(f"无效的点数：{rank!r}")
    return RANKS.index(r)


def _normalize(code: str) -> str:
    # 20：点数统一大写——输出组合直接由原始字符拼成（如 'kk' 会产出
    # 'ks'），与牌堆/公共牌的大写口径不一致时会被误判成死牌冲突
    return code.strip().replace("10", "T").upper()


def expand_hand_code(code: str) -> list[tuple[str, str]]:
    """把手牌代码展开成具体组合，如 'AKs' -> [('As','Ks'), ('Ah','Kh'), ...]。"""
    code = _normalize(code)
    if not 2 <= len(code) <= 3:
        raise InvalidHandError(f"无法解析手牌代码：{code!r}")
    i1, i2 = _rank_index(code[0]), _rank_index(code[1])
    hi, lo = (code[0], code[1]) if i1 > i2 else (code[1], code[0])
    suffix = code[2].lower() if len(code) == 3 else ""

    if i1 == i2:  # 对子
        if suffix:
            raise InvalidHandError(f"对子不需要 s/o 后缀：{code!r}")
        return [(hi + a, hi + b) for a, b in combinations_suited()]

    if suffix == "s":
        return [(hi + s, lo + s) for s in SUITS]
    if suffix == "o":
        return [(hi + a, lo + b) for a in SUITS for b in SUITS if a != b]
    if suffix == "":
        return expand_hand_code(hi + lo + "s") + expand_hand_code(hi + lo + "o")
    raise InvalidHandError(f"无效的后缀（应为 s 或 o）：{code!r}")


def combinations_suited():
    """同点数两张牌的 4 选 2 花色组合。"""
    return [(SUITS[i], SUITS[j]) for i in range(4) for j in range(i + 1, 4)]


def _expand_plus(code: str) -> list[str]:
    """展开 '+' 记法：'TT+' -> [TT,JJ,QQ,KK,AA]，'A2s+' -> [A2s..AKs]。"""
    if not code.endswith("+"):
        return [code]
    base = _normalize(code[:-1])
    if len(base) == 2 and base[0] == base[1]:  # 对子区间
        top = _rank_index(base[0])
        return [RANKS[i] * 2 for i in range(top + 1)]
    if len(base) == 3 and base[0] != base[1]:
        hi_i, lo_i = _rank_index(base[0]), _rank_index(base[1])
        if hi_i >= lo_i:
            raise InvalidHandError(f"'+' 范围要求第一个点数更高：{code!r}")
        return [base[0].upper() + RANKS[i] + base[2].lower() for i in range(hi_i + 1, lo_i + 1)]
    raise InvalidHandError(f"无法解析范围代码：{code!r}")


def _expand_interval(code: str) -> list[str]:
    """展开区间写法：'22-99'（对子区间）、'A5s-A2s'（同高点数踢脚区间）。"""
    lo, hi = code.split("-", 1)
    lo, hi = _normalize(lo), _normalize(hi)
    if len(lo) != len(hi) or len(lo) not in (2, 3):
        raise InvalidHandError(f"区间两端格式须一致：{code!r}")
    i1, i2 = _rank_index(lo[0]), _rank_index(hi[0])

    if len(lo) == 2 and lo[0] == lo[1] and hi[0] == hi[1]:  # 对子区间
        a, b = min(i1, i2), max(i1, i2)
        return [RANKS[k] * 2 for k in range(a, b + 1)]
    if len(lo) == 3 and lo[0] == hi[0] and lo[2] == hi[2] \
            and lo[2].lower() in ("s", "o"):
        j1, j2 = _rank_index(lo[1]), _rank_index(hi[1])
        if i1 != i2 and j1 == j2:
            raise InvalidHandError(f"区间两端点数须不同：{code!r}")
        a, b = min(j1, j2), max(j1, j2)
        return [lo[0] + RANKS[k] + lo[2].lower() for k in range(a, b + 1)]
    raise InvalidHandError(f"无法解析区间：{code!r}")


def expand_range(text: str) -> list[tuple[str, str]]:
    """'TT+, A2s+, 22-99, A5s-A2s' -> 去重排序后的具体组合列表。空范围抛 InvalidHandError。"""
    combos: set[tuple[str, str]] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            for h in _expand_interval(part):
                combos.update(expand_hand_code(h))
            continue
        for code in _expand_plus(part):
            combos.update(expand_hand_code(code))
    if not combos:
        raise InvalidHandError(f"范围不能为空：{text!r}")
    return sorted(combos)
