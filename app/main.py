"""字幕校稿 Web 应用：FastAPI 后端。"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .alignment import GAP_COST, align
from .parsers import parse_script, parse_srt
from .textnorm import normalize_text

MAX_ITEMS = 2000          # 每侧条目数上限，防止 DP 规模过大导致请求过久
MAX_RAW_BYTES = 2 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="字幕校稿对齐", version="1.0.0")


class AlignRequest(BaseModel):
    script: str = Field(default="", description="逐行对白稿")
    srt: str = Field(default="", description="SRT 字幕")
    gap_cost: float = Field(default=GAP_COST, ge=0.0, le=1.0)


def _build_rows(script_text: str, srt_text: str, gap_cost: float):
    if len(script_text.encode("utf-8", errors="ignore")) > MAX_RAW_BYTES:
        raise HTTPException(400, "对白稿超过 2 MB 大小限制")
    if len(srt_text.encode("utf-8", errors="ignore")) > MAX_RAW_BYTES:
        raise HTTPException(400, "SRT 超过 2 MB 大小限制")

    try:
        scripts = parse_script(script_text)
    except Exception as exc:  # 纯文本解析基本不会失败，兜底
        raise HTTPException(400, f"对白稿解析失败：{exc}") from exc
    try:
        subs = parse_srt(srt_text)
    except ValueError as exc:
        raise HTTPException(400, f"SRT 解析失败：{exc}") from exc

    if not scripts and not subs:
        raise HTTPException(400, "两侧内容均为空，请粘贴对白稿与 SRT")
    if len(scripts) > MAX_ITEMS or len(subs) > MAX_ITEMS:
        raise HTTPException(
            400, f"条目数超过每侧 {MAX_ITEMS} 条的上限，请拆分后再对齐"
        )

    norms_s = [normalize_text(it.raw_text) for it in scripts]
    norms_t = [normalize_text(it.raw_text) for it in subs]
    ops = align(norms_s, norms_t, gap_cost=gap_cost)

    rows = []
    totals = {"match": 0, "missing_script": 0, "missing_srt": 0}
    match_cost_sum = 0.0
    for op, cost, i, j in ops:
        if op == "match":
            s, t = scripts[i], subs[j]
            row = {
                "status": "match",
                "cost": round(cost, 6),
                "script_index": s.index,
                "script_text": s.raw_text,
                "srt_index": t.index,
                "srt_text": t.raw_text,
                "start_tc": t.start_tc,
                "end_tc": t.end_tc,
            }
            totals["match"] += 1
            match_cost_sum += cost
        elif op == "gap_a":
            # 对白稿有条目，SRT 无对应字幕
            s = scripts[i]
            row = {
                "status": "missing_srt",
                "cost": round(cost, 6),
                "script_index": s.index,
                "script_text": s.raw_text,
                "srt_index": None,
                "srt_text": "",
                "start_tc": "",
                "end_tc": "",
            }
            totals["missing_srt"] += 1
        else:
            # SRT 有条目，对白稿缺失
            t = subs[j]
            row = {
                "status": "missing_script",
                "cost": round(cost, 6),
                "script_index": None,
                "script_text": "",
                "srt_index": t.index,
                "srt_text": t.raw_text,
                "start_tc": t.start_tc,
                "end_tc": t.end_tc,
            }
            totals["missing_script"] += 1
        rows.append(row)

    total_cost = sum(r["cost"] for r in rows)
    summary = {
        **totals,
        "total_rows": len(rows),
        "total_cost": round(total_cost, 6),
        "gap_cost": gap_cost,
        "avg_match_cost": round(match_cost_sum / totals["match"], 6)
        if totals["match"]
        else None,
    }
    return rows, summary


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/align")
def api_align(req: AlignRequest):
    rows, summary = _build_rows(req.script, req.srt, req.gap_cost)
    return {"summary": summary, "rows": rows}


@app.post("/api/export.csv")
def api_export_csv(req: AlignRequest):
    rows, _ = _build_rows(req.script, req.srt, req.gap_cost)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "状态", "代价", "对白稿行号", "对白稿文本",
            "SRT序号", "时间码入点", "时间码出点", "SRT文本",
        ]
    )
    status_label = {
        "match": "对位",
        "missing_script": "缺对白稿",
        "missing_srt": "缺字幕",
    }
    for r in rows:
        writer.writerow(
            [
                status_label[r["status"]],
                f"{r['cost']:.4f}",
                r["script_index"] if r["script_index"] is not None else "",
                r["script_text"],
                r["srt_index"] if r["srt_index"] is not None else "",
                r["start_tc"],
                r["end_tc"],
                r["srt_text"],
            ]
        )
    # UTF-8 BOM，保证 Excel 直接打开中文不乱码
    data = "﻿".encode("utf-8") + buf.getvalue().encode("utf-8")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="alignment.csv"'
        },
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
