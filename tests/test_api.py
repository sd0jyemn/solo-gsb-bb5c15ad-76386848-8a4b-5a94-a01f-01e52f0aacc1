"""FastAPI 接口层测试。需要 fastapi 的 TestClient（自带 httpx/starlette）。

运行：python -m pytest tests/ -q
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SCRIPT = "\n".join(
    [
        "你好，世界！",
        "今天我们来聊聊字幕校对。",
        "这一句只出现在对白稿里。",
        "编辑距离会忽略标点和空格。",
        "再见！",
        "再见！",
    ]
)

SRT = "\n".join(
    [
        "1",
        "00:00:01,000 --> 00:00:02,500",
        "你好世界",
        "",
        "2",
        "00:00:03,000 --> 00:00:06,000",
        "今天我们来聊聊字幕校对",
        "",
        "3",
        "00:00:07,000 --> 00:00:10,000",
        "编辑距离会忽略标点和空格",
        "",
        "4",
        "00:00:11,200 --> 00:00:12,800",
        "这是一条多余的字幕",
        "",
        "5",
        "00:00:13,000 --> 00:00:14,000",
        "再见",
        "",
        "6",
        "00:00:14,500 --> 00:00:15,500",
        "再见",
        "",
    ]
)


def _align():
    resp = client.post("/api/align", json={"script": SCRIPT, "srt": SRT})
    assert resp.status_code == 200
    return resp.json()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_index_and_static():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_align_counts_and_order():
    data = _align()
    s = data["summary"]
    assert s["match"] == 5
    assert s["missing_script"] == 1
    assert s["missing_srt"] == 1
    assert s["total_rows"] == 7
    assert s["total_cost"] == 0.9

    statuses = [r["status"] for r in data["rows"]]
    # 缺项应落在正确的中间位置，整体顺序单调
    assert statuses.index("missing_srt") == 2
    assert statuses.index("missing_script") == 4

    # 两条“再见”按顺序分别对上 SRT 5、6
    bye = [r for r in data["rows"] if r["srt_text"] == "再见"]
    assert [r["script_index"] for r in bye] == [5, 6]
    assert [r["srt_index"] for r in bye] == [5, 6]

    # 时间码保留
    first = data["rows"][0]
    assert first["start_tc"] == "00:00:01,000"
    assert first["end_tc"] == "00:00:02,500"


def test_each_item_matched_at_most_once():
    data = _align()
    si = [r["script_index"] for r in data["rows"] if r["status"] == "match"]
    ti = [r["srt_index"] for r in data["rows"] if r["status"] == "match"]
    assert len(si) == len(set(si))
    assert len(ti) == len(set(ti))


def test_punctuation_ignored():
    resp = client.post(
        "/api/align",
        json={"script": "你好，世界！\n", "srt": "1\n00:00:01,000 --> 00:00:02,000\n你好世界\n"},
    )
    row = resp.json()["rows"][0]
    assert row["status"] == "match"
    assert row["cost"] == 0.0


def test_custom_gap_cost():
    resp = client.post(
        "/api/align",
        json={"script": "甲甲甲\n", "srt": "1\n00:00:01,000 --> 00:00:02,000\n乙乙乙\n", "gap_cost": 0.2},
    )
    data = resp.json()
    # 匹配代价 1.0 > 2*0.2=0.4，应各跳过
    assert {r["status"] for r in data["rows"]} == {"missing_script", "missing_srt"}


def test_bad_srt_returns_400():
    resp = client.post("/api/align", json={"script": "你好", "srt": "没有时间码"})
    assert resp.status_code == 400
    assert "SRT" in resp.json()["detail"]


def test_empty_both_returns_400():
    resp = client.post("/api/align", json={"script": "  \n", "srt": ""})
    assert resp.status_code == 400


def test_csv_export():
    resp = client.post("/api/export.csv", json={"script": SCRIPT, "srt": SRT})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    raw = resp.content
    assert raw[:3] == b"\xef\xbb\xbf"  # UTF-8 BOM
    text = raw.decode("utf-8-sig")
    assert "状态,代价,对白稿行号" in text
    assert "对位" in text and "缺字幕" in text and "缺对白稿" in text
    assert "00:00:01,000" in text


# ---------------- 人工锚点 ----------------


def _align_with_anchors(anchors):
    resp = client.post(
        "/api/align", json={"script": SCRIPT, "srt": SRT, "anchors": anchors}
    )
    return resp


def test_no_anchors_unchanged():
    # 未提供锚点时结果与原来一致，且所有行 anchor=False
    data = _align()
    assert data["summary"]["anchors"] == 0
    assert all(r["anchor"] is False for r in data["rows"])


def test_anchor_forces_pairing():
    # 对白稿第 3 行与字幕块第 4 块文本毫不相干，无锚点时各自缺项；
    # 锚定后必须强制对位，原先的对位被推成双侧缺项
    resp = _align_with_anchors([{"script_line": 3, "srt_block": 4}])
    assert resp.status_code == 200
    data = resp.json()
    s = data["summary"]
    assert s["anchors"] == 1

    anchor_rows = [r for r in data["rows"] if r["anchor"]]
    assert len(anchor_rows) == 1
    row = anchor_rows[0]
    assert row["status"] == "match"
    assert row["script_index"] == 3
    assert row["srt_index"] == 4
    assert row["script_text"] == "这一句只出现在对白稿里。"
    assert row["srt_text"] == "这是一条多余的字幕"
    assert row["cost"] > 0.5  # 文本差异很大，代价如实记录但仍强制对位

    # 被锚点“挤开”的原对位成为双侧缺项
    statuses = [r["status"] for r in data["rows"]]
    assert statuses == [
        "match", "match", "missing_script", "match",
        "missing_srt", "match", "match",
    ]
    assert s["match"] == 5
    assert s["missing_script"] == 1
    assert s["missing_srt"] == 1


def test_anchor_multiple_segments():
    # 两个锚点把序列切成三段，段间仍按最小代价对齐
    resp = _align_with_anchors(
        [
            {"script_line": 1, "srt_block": 1},
            {"script_line": 6, "srt_block": 6},
        ]
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["anchors"] == 2
    anchor_pairs = [
        (r["script_index"], r["srt_index"]) for r in data["rows"] if r["anchor"]
    ]
    assert anchor_pairs == [(1, 1), (6, 6)]
    # 中间段的缺项照常出现
    statuses = [r["status"] for r in data["rows"]]
    assert "missing_script" in statuses and "missing_srt" in statuses


def test_anchor_out_of_range():
    resp = _align_with_anchors([{"script_line": 99, "srt_block": 1}])
    assert resp.status_code == 400
    assert "超出范围" in resp.json()["detail"]
    resp = _align_with_anchors([{"script_line": 1, "srt_block": 0}])
    assert resp.status_code == 400
    assert "超出范围" in resp.json()["detail"]
    resp = _align_with_anchors([{"script_line": 1, "srt_block": 7}])
    assert resp.status_code == 400
    assert "字幕块" in resp.json()["detail"]


def test_anchor_duplicate_rejected():
    resp = _align_with_anchors(
        [
            {"script_line": 1, "srt_block": 1},
            {"script_line": 1, "srt_block": 1},
        ]
    )
    assert resp.status_code == 400
    assert "重复" in resp.json()["detail"]


def test_anchor_same_side_reused_rejected():
    # 同一行/同一块被两个锚点使用
    resp = _align_with_anchors(
        [{"script_line": 1, "srt_block": 1}, {"script_line": 1, "srt_block": 2}]
    )
    assert resp.status_code == 400
    assert "重复锚定" in resp.json()["detail"]
    resp = _align_with_anchors(
        [{"script_line": 1, "srt_block": 1}, {"script_line": 2, "srt_block": 1}]
    )
    assert resp.status_code == 400
    assert "重复锚定" in resp.json()["detail"]


def test_anchor_crossing_rejected():
    resp = _align_with_anchors(
        [{"script_line": 1, "srt_block": 2}, {"script_line": 2, "srt_block": 1}]
    )
    assert resp.status_code == 400
    assert "交叉" in resp.json()["detail"]


def test_anchor_with_empty_side_rejected():
    resp = client.post(
        "/api/align",
        json={"script": "", "srt": SRT, "anchors": [{"script_line": 1, "srt_block": 1}]},
    )
    assert resp.status_code == 400
    assert "锚点" in resp.json()["detail"]


def test_anchor_csv_export():
    resp = client.post(
        "/api/export.csv",
        json={"script": SCRIPT, "srt": SRT, "anchors": [{"script_line": 3, "srt_block": 4}]},
    )
    assert resp.status_code == 200
    text = resp.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header.endswith(",锚点")
    # 锚点行末列标“是”，其余行末列为空
    lines = text.splitlines()[1:]
    marked = [ln for ln in lines if ln.endswith(",是")]
    assert len(marked) == 1
    assert "这一句只出现在对白稿里。" in marked[0]
    assert "这是一条多余的字幕" in marked[0]


def test_anchor_csv_export_invalid_anchors():
    resp = client.post(
        "/api/export.csv",
        json={"script": SCRIPT, "srt": SRT, "anchors": [{"script_line": 2, "srt_block": 1}, {"script_line": 1, "srt_block": 2}]},
    )
    assert resp.status_code == 400
    assert "交叉" in resp.json()["detail"]
