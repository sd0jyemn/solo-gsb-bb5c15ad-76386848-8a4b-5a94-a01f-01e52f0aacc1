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
