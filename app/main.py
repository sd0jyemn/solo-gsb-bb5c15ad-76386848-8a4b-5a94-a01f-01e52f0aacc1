"""字幕校稿 Web 应用：FastAPI 后端。"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .alignment import GAP_COST, align, align_with_anchors
from .parsers import parse_script, parse_srt
from .textnorm import normalize_text

MAX_ITEMS = 2000          # 每侧条目数上限，防止 DP 规模过大导致请求过久
MAX_RAW_BYTES = 2 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="字幕校稿对齐", version="1.0.0")


class AnchorPair(BaseModel):
    """人工锚点：对白稿非空行序位 × SRT 字幕块序位（均从 1 开始）。"""

    script_line: int = Field(description="对白稿第几个非空行（从 1 开始）")
    srt_block: int = Field(description="第几个 SRT 字幕块（从 1 开始）")


class AlignRequest(BaseModel):
    script: str = Field(default="", description="逐行对白稿")
    srt: str = Field(default="", description="SRT 字幕")
    gap_cost: float = Field(default=GAP_COST, ge=0.0, le=1.0)
    anchors: list[AnchorPair] = Field(
        default_factory=list, description="人工锚点（行:块 强制配对）"
    )


def _validate_anchors(
    anchors: list[AnchorPair], n_script: int, n_srt: int
) -> list[tuple[int, int]]:
    """校验锚点并返回按行序位升序的 0 起始 (i, j) 列表。

    任一侧序位越界、重复配对、交叉配对（一列递增另一列不递增）
    均抛出 400，并给出明确的错误说明。
    """
    if not anchors:
        return []
    if n_script == 0 or n_srt == 0:
        raise HTTPException(
            400, "存在锚点时对白稿与 SRT 都不能为空，请补充文本或移除锚点"
        )
    for k, a in enumerate(anchors, 1):
        if not 1 <= a.script_line <= n_script:
            raise HTTPException(
                400,
                f"第 {k} 个锚点的对白稿行序位 {a.script_line} 超出范围"
                f"（有效范围 1~{n_script}）",
            )
        if not 1 <= a.srt_block <= n_srt:
            raise HTTPException(
                400,
                f"第 {k} 个锚点的字幕块序位 {a.srt_block} 超出范围"
                f"（有效范围 1~{n_srt}）",
            )
    pairs = sorted((a.script_line, a.srt_block) for a in anchors)
    for k in range(1, len(pairs)):
        (pl, pb), (cl, cb) = pairs[k - 1], pairs[k]
        if pl == cl and pb == cb:
            raise HTTPException(
                400, f"锚点重复：{cl}:{cb} 出现了多次，请去重后再试"
            )
        if pl == cl:
            raise HTTPException(
                400,
                f"锚点冲突：对白稿第 {cl} 行被重复锚定"
                f"（{pl}:{pb} 与 {cl}:{cb}），一行只能对应一个字幕块",
            )
        if pb == cb:
            raise HTTPException(
                400,
                f"锚点冲突：字幕块第 {cb} 块被重复锚定"
                f"（{pl}:{pb} 与 {cl}:{cb}），一块只能对应一行对白稿",
            )
        if cb < pb:
            raise HTTPException(
                400,
                f"锚点交叉：{pl}:{pb} 与 {cl}:{cb} 顺序矛盾，"
                "两列序位必须同时严格递增",
            )
    return [(line - 1, block - 1) for line, block in pairs]


def _build_rows(
    script_text: str,
    srt_text: str,
    gap_cost: float,
    anchors: list[AnchorPair] | None = None,
):
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

    anchor_pairs = _validate_anchors(anchors or [], len(scripts), len(subs))
    anchor_set = set(anchor_pairs)

    norms_s = [normalize_text(it.raw_text) for it in scripts]
    norms_t = [normalize_text(it.raw_text) for it in subs]
    if anchor_pairs:
        ops = align_with_anchors(norms_s, norms_t, anchor_pairs, gap_cost)
    else:
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
                "anchor": (i, j) in anchor_set,
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
                "anchor": False,
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
                "anchor": False,
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
        "anchors": len(anchor_pairs),
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
    rows, summary = _build_rows(req.script, req.srt, req.gap_cost, req.anchors)
    return {"summary": summary, "rows": rows}


@app.post("/api/export.csv")
def api_export_csv(req: AlignRequest):
    rows, _ = _build_rows(req.script, req.srt, req.gap_cost, req.anchors)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "状态", "代价", "对白稿行号", "对白稿文本",
            "SRT序号", "时间码入点", "时间码出点", "SRT文本", "锚点",
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
                "是" if r["anchor"] else "",
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
