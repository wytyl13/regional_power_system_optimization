#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/29 13:41
@Author  : weiyutao
@File    : app.py

FastAPI 服务骨架（docs/服务接口设计方案.md）：
  POST /api/datasets                     上传模版 → datasetId + categories + config + fixedParams
                                         不传 file → 只返回 config + fixedParams（默认配置）
  GET  /api/datasets/{id}/curves/{sheet}  曲线懒加载（按 filterBy 键值筛，服务端规则收口）
  POST /api/jobs                         提交计算（datasetId + config）→ jobId
  GET  /api/jobs                         任务列表（内存实时态 + 落盘历史，新→旧）
  GET  /api/jobs/{jobId}                 状态 queued/running/solved/failed（solved 含四项核对）
  GET  /api/jobs/{jobId}/results         取 solution.json
算法调用走显式接口：power_opt/run.py（RunConfig + run_with_outputs），
经 service/assemble.py 转发（power_opt 路径注入收口在那里），服务不直接摸算法模块。
运行：service/ 目录下 `uvicorn app:app --port 8000`（勿用 --reload，会双开 worker 线程）。
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import traceback
import uuid
from collections import Counter
from pathlib import Path
from typing import Optional

# 须在 import power_opt（连带 gurobipy/matplotlib）之前设好
os.environ.setdefault("GRB_LICENSE_FILE", r"C:\gurobi1300\win64\bin\gurobi.lic")
os.environ.setdefault("MPLBACKEND", "Agg")

import pandas as pd
from fastapi import FastAPI, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from assemble import RunConfig, po_AnalysisExport, po_run
from convert import _included, _pick, convert
from defaults import FIXED_PARAMS, build_form, merge_config
from parse_template import parse_template
from position_chart import build_report
from sheet_views import build_sheet_views
from shared.errors import (BizError, ConflictError, NotFoundError, ValidationError,
                           install_handlers, make_response)
from tree import build_categories, read_curve

HERE = Path(__file__).resolve().parent
DATASETS_DIR = HERE / "datasets"
JOBS_DIR = HERE / "results" / "jobs"

app = FastAPI(title="区域电力系统优化")
app.add_middleware(   # 跨域放行（开发期全放；上线改 allow_origins 为前端域名列表）
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
install_handlers(app)   # BizError/ValueError/请求体校验/兜底 → 统一壳，路由端点不写 try/except

DATASETS: dict[str, dict] = {}      # datasetId → {path, parsed, area, year, uploadedAt}
JOBS: dict[str, dict] = {}          # jobId → 状态（内存注册表，v1 不做重启持久化）
QUEUE: "queue.Queue[str]" = queue.Queue()


def _now() -> str:
    """当前时刻 → 'YYYY-MM-DD HH:MM:SS' 文本（任务时间戳用）。

    Returns:
        str: 当前时刻文本。
    """
    return pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")


# ============ POST /api/datasets ============

@app.post("/api/datasets")
async def upload_dataset(file: Optional[UploadFile] = None):
    """上传收资模版：解析校验并注册 dataset；不传 file 只回默认配置。

    Args:
        file (UploadFile | None): 模版 xlsx；None 时只返回 config + fixedParams。

    Returns:
        dict: datasetId/area/year/categories/config/fixedParams/warnings/excludedUnits
        （不传 file 时仅 config/fixedParams）。
    """
    if file is None or not file.filename:      # 不传 file → 只返回基础配置（无模版，无 datasetId）
        return make_response({"config": build_form(merge_config(None), ["平水年", "枯水年"]),
                              "fixedParams": FIXED_PARAMS})
    if not file.filename.endswith(".xlsx"):
        raise BizError("只接受 .xlsx 模版文件", 400)   # 参数错 400（skill §7），走统一壳
    ds_id = "ds_" + uuid.uuid4().hex[:12]
    ds_dir = DATASETS_DIR / ds_id
    ds_dir.mkdir(parents=True, exist_ok=True)
    path = ds_dir / "template.xlsx"
    with open(path, "wb") as f:
        f.write(await file.read())
    try:
        parsed = parse_template(str(path))      # 缺 sheet/列/曲线 → ValueError
        categories = build_categories(str(path))
    except ValueError as e:
        shutil.rmtree(ds_dir, ignore_errors=True)
        raise ValidationError(f"模版校验失败: {e}") from None

    year, p = parsed["year"], parsed
    warnings = []
    if p["nuclear"]:
        warnings.append({"code": "NUCLEAR_NO_MIN_RATIO",
                         "message": "收资未含核电最小出力率列，核电按基荷处理（最小出力=额定容量）"})
    if p["friendlyStation"]:
        warnings.append({"code": "FRIENDLY_STORAGE_ASSUMED",
                         "message": "友好电站未收资储能转换效率与时长，按配置假设"
                                    "（效率 0.9、时长 2h，可在 config 修改）"})
    excluded = []
    for label, rows in [("煤电", p["coal"]), ("气电", p["gas"]), ("核电", p["nuclear"]),
                        ("生物质", p["biomass"]), ("抽水蓄能", p["pumpedStorage"]),
                        ("常规水电", p["hydro"]), ("新型储能", p["storage"]),
                        ("风电", p["windStations"]), ("光伏", p["pvStations"])]:
        for r in rows:
            ok, why = _included(r, year)
            if not ok:
                excluded.append(f"{label} {r.get('电站名称')}：{why}")
    if excluded:
        cnt = Counter(e.split(" ", 1)[0] for e in excluded)   # 按设备类型汇总
        warnings.append({"code": "EXCLUDED_UNITS",
                         "message": f"{len(excluded)} 台机组未纳入目标年计算："
                                    + "；".join(f"{k}×{n}" for k, n in cnt.items())})

    # 表单默认值 = 内置默认 + 收资值浮出（前端不写死任何默认值/标签）
    defaults = merge_config(None)
    defaults["scenario"]["startTime"] = f"{year}-01-01 00:00"
    defaults["province"]["hotReserveRatio"] = p["hotReserveRatio"]
    defaults["province"]["coldReserveRatio"] = p["coldReserveRatio"]
    defaults["demandResponse"]["shiftRatio"] = p["drShiftRatio"]
    defaults["demandResponse"]["cutRatio"] = p["drCutRatio"]
    hydrology = sorted({k[1] for k in p["hydroCurves"]})   # 模版实际水文年 → 下拉枚举

    DATASETS[ds_id] = {"path": str(path), "parsed": parsed,
                       "area": parsed["area"], "year": year, "uploadedAt": _now()}
    return make_response({"datasetId": ds_id, "area": parsed["area"], "year": year,
                          "categories": categories,
                          "config": build_form(defaults, hydrology),
                          "fixedParams": FIXED_PARAMS,
                          "warnings": warnings, "excludedUnits": excluded})


# ============ GET /api/datasets/{id}/curves/{sheet}（懒加载）============

@app.get("/api/datasets/{ds_id}/curves/{sheet}")
def dataset_curve(ds_id: str, sheet: str, request: Request):
    """细分表格行点击 → 曲线懒加载。Query 参数 = curve.filterBy 各键的行值
    （前端拿 /api/datasets 下发的 curve 元数据拼 URL，零写死；匹配规则见 tree.py）。
    示例：http://127.0.0.1:8000/api/datasets/<id>/curves/负荷-8760?区域=海南"

    Args:
        ds_id (str): datasetId。
        sheet (str): 目标曲线 sheet 名（curve 元数据的 sheet）。
        request (Request): FastAPI 请求（query 参数即筛选键值）。

    Returns:
        dict: view 同款结构 {sheet_name, index, data}（无 series/lines 区分，
        首保留列=index、其余列=data 各一列，详见 tree.read_curve）。
    """
    ds = DATASETS.get(ds_id)
    if ds is None:
        raise NotFoundError(f"dataset 不存在: {ds_id}")
    return make_response(                    # 筛选失败 → ValueError → 422
        read_curve(ds["path"], sheet, dict(request.query_params)))


# ============ POST /api/jobs ============

class JobRequest(BaseModel):
    datasetId: str
    config: dict = {}


def validate_config(ds: dict, user: dict) -> list[str]:
    """设计方案 §3 校验清单；返回问题列表（空=通过）。

    Args:
        ds (dict): DATASETS 注册项（year/parsed）。
        user (dict): 用户 config（未合并的显式键）。

    Returns:
        list[str]: 问题清单（空 = 通过）。
    """
    problems: list[str] = []
    cfg = merge_config(user)
    year, p = ds["year"], ds["parsed"]
    sc = cfg["scenario"]
    try:
        start = pd.Timestamp(sc["startTime"])
    except (ValueError, TypeError):
        return [f"startTime 无法解析: {sc['startTime']!r}（应形如 2030-01-01 00:00）"]
    hours = sc["hours"]
    if not isinstance(hours, int) or isinstance(hours, bool) or hours < 1:
        problems.append(f"hours 须为正整数，收到 {hours!r}")
    elif start < pd.Timestamp(year, 1, 1) or \
            start + pd.Timedelta(hours=hours) > pd.Timestamp(year + 1, 1, 1):
        problems.append(f"求解窗口 {sc['startTime']} + {hours}h 越出 {year} 年曲线覆盖范围")
    avail = {k[1] for k in p["hydroCurves"]}
    if avail and sc["hydrology"] not in avail:
        problems.append(f"水文情景 {sc['hydrology']!r} 不在模版曲线中（可用: {sorted(avail)}）")
    prov_u = (user or {}).get("province", {})
    dr_u = (user or {}).get("demandResponse", {})
    for label, v in [("province.hotReserveRatio", prov_u.get("hotReserveRatio")),
                     ("province.coldReserveRatio", prov_u.get("coldReserveRatio")),
                     ("demandResponse.shiftRatio", dr_u.get("shiftRatio")),
                     ("demandResponse.cutRatio", dr_u.get("cutRatio"))]:
        if v is not None and not (isinstance(v, (int, float)) and 0 <= v <= 1):
            problems.append(f"{label} 须为 [0,1] 内数值，收到 {v!r}")
    for g in ("pumpedStorage", "storage", "friendlyStation"):
        u = cfg["units"][g]
        if not (u["socMin"] < u["socInitial"] < u["socMax"]):
            problems.append(f"units.{g}: 需 socMin < socInitial < socMax")
    for k, v in cfg["penalties"].items():
        if not (isinstance(v, (int, float)) and v > 0):
            problems.append(f"penalties.{k} 须 > 0")
    if (sc["interprovincial"] or sc["interRegionTransfer"]) and not p["transferCurve8760"]:
        problems.append("已开省间互济/跨区送电，但模版送电协议无 8760 送电曲线")
    return problems


@app.post("/api/jobs")
def create_job(req: JobRequest):
    """提交计算：校验通过后入队（串行 worker 逐个求解），返回 jobId。

    Args:
        req (JobRequest): {datasetId, config}。

    Returns:
        dict: {"jobId": 任务号}。
    """
    ds = DATASETS.get(req.datasetId)
    if ds is None:
        raise NotFoundError(f"dataset 不存在: {req.datasetId}")
    problems = validate_config(ds, req.config)
    if problems:
        raise ValidationError("；".join(problems))
    job_id = "job_" + pd.Timestamp.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    JOBS[job_id] = {"jobId": job_id, "datasetId": req.datasetId, "status": "queued",
                    "userConfig": req.config, "submittedAt": _now(), "jobDir": str(job_dir)}
    QUEUE.put(job_id)
    return make_response({"jobId": job_id})


# ============ GET 任务列表 / 状态 / 结果 ============

def _job_dir(job_id: str) -> Path | None:
    """任务号即目录名；防路径穿越，只认 job_ 前缀且不含分隔符。

    Args:
        job_id (str): 任务号。

    Returns:
        Path | None: 任务目录；非法或不存在 → None。
    """
    if not job_id.startswith("job_") or "/" in job_id or "\\" in job_id or ".." in job_id:
        return None
    d = JOBS_DIR / job_id
    return d if d.is_dir() else None


def _disk_summary(job_dir: Path, with_report: bool = False) -> dict:
    """从落盘文件还原任务摘要（服务重启后 JOBS 内存态丢失，以盘为准）。
    interrupted = 目录存在但既无 solution.json 也无 error.log（多为重启打断）。

    Args:
        job_dir (Path): 任务目录。
        with_report (bool): True 时附 result.report（历史任务状态页用）。

    Returns:
        dict: 任务摘要（status ∈ solved/failed/interrupted）。
    """
    job_id = job_dir.name
    try:
        submitted = pd.to_datetime(job_id[4:19], format="%Y%m%d_%H%M%S") \
                        .strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        submitted = None
    sol = job_dir / "solution.json"
    if sol.exists():
        d = json.loads(sol.read_text(encoding="utf-8"))
        # 2026-10-20 瘦身后 fourItems/scenario 不在 solution.json，改读 meta.json 侧车
        meta_path = job_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) \
            if meta_path.exists() else {}
        sc = meta.get("scenario", {})
        out = {"jobId": job_id, "datasetId": d.get("datasetId"), "status": "solved",
               "submittedAt": d.get("submittedAt") or submitted,
               "finishedAt": d.get("finishedAt"),
               "area": d.get("area"), "year": d.get("year"),
               "scenario": {k: sc.get(k) for k in ("hours", "startTime", "hydrology",
                                                   "interprovincial", "interRegionTransfer")},
               "fourItems": meta.get("fourItems")}
        if with_report:
            out["report"] = d.get("result", {}).get("report")
        return out
    log = job_dir / "error.log"
    if log.exists():
        lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        return {"jobId": job_id, "status": "failed", "submittedAt": submitted,
                "error": lines[-1] if lines else "unknown"}
    return {"jobId": job_id, "status": "interrupted", "submittedAt": submitted}


@app.get("/api/jobs")
def list_jobs():
    """任务列表：内存实时态优先、落盘历史补齐，新→旧取前 5 条。

    Returns:
        dict: {"jobs": [任务摘要, ...]}。
    """
    jobs = []
    if JOBS_DIR.exists():
        for job_dir in JOBS_DIR.iterdir():
            if not job_dir.is_dir():
                continue
            live = JOBS.get(job_dir.name)
            if live and live["status"] in ("queued", "running"):
                jobs.append({"jobId": live["jobId"], "datasetId": live["datasetId"],
                             "status": live["status"], "submittedAt": live["submittedAt"]})
            else:
                s = _disk_summary(job_dir)
                if s:
                    jobs.append(s)
    jobs.sort(key=lambda j: j.get("submittedAt") or "", reverse=True)   # 新→旧
    return make_response({"jobs": jobs[:5]})


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    """单任务状态：内存态优先，重启后的历史任务从盘上还原（含 report）。

    Args:
        job_id (str): 任务号。

    Returns:
        dict: 状态体（solved 含 fourItems/report，failed 含 error/userConfig）。
    """
    job = JOBS.get(job_id)
    if job is None:
        d = _job_dir(job_id)     # 历史任务（重启后内存态丢失），从盘上还原
        if d is not None:
            return make_response(_disk_summary(d, with_report=True))
        raise NotFoundError(f"job 不存在: {job_id}")
    out = {k: job[k] for k in ("jobId", "datasetId", "status", "submittedAt")
           if k in job}
    if "startedAt" in job:
        out["startedAt"] = job["startedAt"]
    if job["status"] == "solved":
        out["fourItems"] = job["fourItems"]
        out["report"] = job["report"]
    if job["status"] == "failed":
        out["error"] = job["error"]
        out["userConfig"] = job["userConfig"]   # 失败时把请求参数带回，前端可自查
        out["startedAt"] = job.get("startedAt")
    return make_response(out)


@app.get("/api/jobs/{job_id}/results")
def job_results(job_id: str):
    """取 solution.json：未解完 → 409，无此任务 → 404。

    Args:
        job_id (str): 任务号。

    Returns:
        dict: solution.json 全文。
    """
    job = JOBS.get(job_id)
    if job is not None:
        path = Path(job["jobDir"]) / "solution.json"
        if job["status"] != "solved" or not path.exists():
            raise ConflictError(f"任务状态 {job['status']}，尚无结果")
    else:
        d = _job_dir(job_id)     # 历史任务：有结果文件即可取
        if d is None or not (d / "solution.json").exists():
            raise NotFoundError(f"job 不存在: {job_id}")
        path = d / "solution.json"
    return make_response(json.loads(path.read_text(encoding="utf-8")))


# ============ 串行队列 worker（单线程，同时只解一个）============

def _run_job(job_id: str):
    """执行一个任务：convert 出三份表 → RunConfig 显式接口求解 → 组装 solution.json。
    任何异常由 _worker 兜底落 failed + error.log。

    Args:
        job_id (str): 任务号。

    Returns:
        None
    """
    job = JOBS[job_id]
    ds = DATASETS[job["datasetId"]]
    job["status"], job["startedAt"] = "running", _now()
    job_dir = Path(job["jobDir"])
    convert(ds["parsed"], job["userConfig"], job_dir)   # 三份中间表（配置注入；不进返回值）

    cfg = merge_config(job["userConfig"])
    # 4 个模板浮出键不在 DEFAULT_CONFIG，按 convert 同语义补（用户未给 → 模板收资值），
    # 与 /api/datasets 上传端点的 config 字段集对齐
    p, u = ds["parsed"], job["userConfig"] or {}
    cfg["province"]["hotReserveRatio"] = _pick(u.get("province", {}).get("hotReserveRatio"), p["hotReserveRatio"])
    cfg["province"]["coldReserveRatio"] = _pick(u.get("province", {}).get("coldReserveRatio"), p["coldReserveRatio"])
    cfg["demandResponse"]["shiftRatio"] = _pick(u.get("demandResponse", {}).get("shiftRatio"), p["drShiftRatio"])
    cfg["demandResponse"]["cutRatio"] = _pick(u.get("demandResponse", {}).get("cutRatio"), p["drCutRatio"])
    sc = cfg["scenario"]
    # 显式接口：RunConfig 传参，算法包内自己落到 config（不再服务侧改写模块属性）
    run_cfg = RunConfig(
        unit_file=job_dir / "分区机组信息表.xlsx",
        system_file=job_dir / "算例系统参数.xlsx",
        profile_file=job_dir / "运行数据.xlsx",
        result_dir=job_dir,
        T=sc["hours"], start_time=sc["startTime"], delta_t=1.0,
        hydrology=sc["hydrology"],
        migap=cfg["solver"]["mipGap"], time_limit=cfg["solver"]["timeLimit_s"],
        use_interprovincial=bool(sc["interprovincial"]),
        use_fixed_transfer=bool(sc["interRegionTransfer"]),
    )
    solution, inp = po_run(run_cfg)
    four = solution.four_items
    job["fourItems"] = four
    if four["objective"]["value"] is None:
        raise RuntimeError(f"求解无可行解（{four['status']}），"
                           f"日志: 见 {job_dir} 下 gurobi_py_T{inp.time.T}.log")
    # 黄金规则四项核对（solution.json 已瘦身不带四项，落日志 + meta.json 侧车）
    print("四项核对: 变量 %d（binary %d）/ 约束 %d 行 / %s / obj %.2f 元" % (
        four["variables"]["count"], four["variables"]["binary"],
        four["constraints"]["rows"], four["status"],
        four["objective"]["value"]))
    # v3（2026-10-20 用户裁决瘦身）：solution.json 只留 任务标识 + report/config/views；
    # fourItems/effectiveConfig/convertSummary/costs/time/analysis 已删（重复数据）。
    ae = po_AnalysisExport(inp, solution)
    metrics = ae.metrics()
    result = {
        "report": build_report(inp, solution, metrics, sc["hydrology"]),
        # config：与 /api/datasets 下发的 config 同格式（build_form 表单结构），值为本次生效配置
        "config": build_form(cfg, sorted({k[1] for k in ds["parsed"]["hydroCurves"]})),
        # views：前端唯一渲染入口（list：series/lines 各带 sheet_name/index/data）
        "views": build_sheet_views(solution),
    }
    job["report"] = result["report"]
    payload = {"jobId": job_id, "datasetId": job["datasetId"],
               "area": ds["area"], "year": ds["year"],
               "submittedAt": job["submittedAt"], "finishedAt": _now(),
               "result": result}
    (job_dir / "solution.json").write_text(
        json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    # 服务内部状态还原侧车（重启后任务列表/状态页用，不进返回值）
    (job_dir / "meta.json").write_text(
        json.dumps({"fourItems": four, "scenario": sc}, ensure_ascii=False),
        encoding="utf-8")
    job["status"], job["finishedAt"] = "solved", _now()


def _worker():
    """串行队列消费线程：逐个取任务执行，异常不崩线程。

    Returns:
        None
    """
    while True:
        job_id = QUEUE.get()
        job = JOBS.get(job_id)
        if job is None:
            continue
        try:
            _run_job(job_id)
        except Exception as e:      # 服务边界：任何异常都落 failed + error.log，不崩 worker
            import traceback
            job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"
            try:
                (Path(job["jobDir"]) / "error.log").write_text(
                    "userConfig: " + json.dumps(job["userConfig"], ensure_ascii=False) + "\n\n"
                    + traceback.format_exc(), encoding="utf-8")
            except OSError:
                pass


threading.Thread(target=_worker, daemon=True, name="solver-worker").start()
