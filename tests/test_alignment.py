"""算法与解析层的直接单元测试，不依赖网络框架：python -m tests.test_alignment"""
import sys

from app.alignment import GAP_COST, align
from app.parsers import parse_script, parse_srt
from app.textnorm import normalize_text, normalized_distance


def test_normalize():
    assert normalize_text("你好， 世界！") == "你好世界"
    assert normalize_text("Hello, World!") == normalize_text("hello world")
    assert normalize_text("ＡＢＣ１２３") == "abc123"  # 全角 NFKC
    assert normalize_text("line\nbreak\there") == "linebreakhere"


def test_distance():
    d = normalized_distance(normalize_text("你好世界"), normalize_text("你好，世界！"))
    assert d == 0.0
    d = normalized_distance(normalize_text("abc"), normalize_text("abd"))
    assert abs(d - 1 / 3) < 1e-9
    assert normalized_distance("", "") == 0.0
    assert normalized_distance("", "x") == 1.0


def test_parse_srt_basic():
    srt = "1\n00:00:01,000 --> 00:00:02,500\n你好世界\n\n2\n00:00:03,000 --> 00:00:04,000\n第二行\n"
    items = parse_srt(srt)
    assert len(items) == 2
    assert items[0].index == 1
    assert items[0].start_tc == "00:00:01,000"
    assert items[0].end_tc == "00:00:02,500"
    assert items[0].raw_text == "你好世界"
    assert items[1].index == 2


def test_parse_srt_bad():
    try:
        parse_srt("随便一段\n没有时间码")
    except ValueError:
        pass
    else:
        raise AssertionError("缺少时间码应当报错")


def test_align_perfect():
    a = [normalize_text(x) for x in ["你好世界", "再见"]]
    b = [normalize_text(x) for x in ["你好，世界！", "再见！"]]
    ops = align(a, b)
    assert len(ops) == 2
    assert all(op[0] == "match" for op in ops)
    assert all(op[1] == 0.0 for op in ops)
    # 顺序单调
    ai = [op[2] for op in ops]
    bj = [op[3] for op in ops]
    assert ai == sorted(ai) and bj == sorted(bj)


def test_align_with_gaps():
    # b 缺少第二条，且 b 多一条
    a = [normalize_text(x) for x in ["第一句", "第二句", "第三句"]]
    b = [normalize_text(x) for x in ["第一句", "第三句", "多余的字幕"]]
    ops = align(a, b)
    kinds = [op[0] for op in ops]
    assert kinds.count("match") == 2
    assert kinds.count("gap_a") == 1   # 多余字幕：b 侧无对应 a
    assert kinds.count("gap_b") == 1   # 第二句：a 侧无对应 b
    # 每条最多匹配一次
    used_a = [op[2] for op in ops if op[0] == "match"]
    used_b = [op[3] for op in ops if op[0] == "match"]
    assert len(used_a) == len(set(used_a))
    assert len(used_b) == len(set(used_b))


def test_align_repeated_short_lines_order():
    # 重复短句必须按整段顺序对位，而不是都吸到同一条上
    a = [normalize_text(x) for x in ["好", "好", "好", "好"]]
    b = [normalize_text(x) for x in ["好", "好", "好"]]
    ops = align(a, b)
    matches = [(i, j) for op, _, i, j in ops if op == "match"]
    # 3 条对位、两侧索引各自严格递增（单调、最多一次）
    assert len(matches) == 3
    ai = [m[0] for m in matches]
    bj = [m[1] for m in matches]
    assert ai == sorted(ai) and len(set(ai)) == 3
    assert bj == sorted(bj) and len(set(bj)) == 3
    assert all(ai[k] >= bj[k] for k in range(3))  # 单调不错位
    assert len([op for op in ops if op[0] != "match"]) == 1


def test_align_empty_side():
    ops = align([normalize_text("只有对白稿")], [])
    assert ops == [("gap_a", GAP_COST, 0, None)]
    ops = align([], [normalize_text("只有字幕")])
    assert ops == [("gap_b", GAP_COST, None, 0)]


def test_gap_cost_optimal():
    # 完全不相关的两条：匹配代价 1.0 > 两次跳过 0.9，应选择各自缺项
    a = [normalize_text("aaaaaaaaaa")]
    b = [normalize_text("bbbbbbbbbb")]
    ops = align(a, b)
    assert {op[0] for op in ops} == {"gap_a", "gap_b"}


def test_script_parse():
    items = parse_script(" 第一行 \n\n   \n第二行\n")
    assert [it.raw_text for it in items] == ["第一行", "第二行"]
    assert [it.index for it in items] == [1, 2]


def run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个测试通过")


if __name__ == "__main__":
    sys.exit(run())
