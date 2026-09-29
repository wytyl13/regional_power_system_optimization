#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File    :app.py
FastAPI 服务骨架（docs/服务接口设计方案.md）：
  POST /api/datasets          上传模版 → datasetId + dataTreeModels + warnings + configDefaults
  POST /api/jobs              提交计算（datasetId + config）→ jobId
  GET  /api/jobs/{jobId}      状态 queued/running/solved/failed（solved 含四项核对）
  GET  /api/jobs/{jobId}/results  取 solution.json
方案A：power_opt 零源码改动——调 solver 前直接设置其 config 模块属性
（reader.py 与 model/units 均在调用时才读 config；单队列单 worker 串行，无并发写）。
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

# 须在 import power_opt（连带 gurobipy/matplotlib）之前设好
os.environ.setdefault("GRB_LICENSE_FILE", r"C:\gurobi1300\win64\bin\gurobi.lic")
os.environ.setdefault("MPLBACKEND", "Agg")

import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from convert import _included, convert
from defaults import FIELDS_MAPPING, merge_config
from parse_template import parse_template
from tree import build_data_tree

HERE = Path(__file__).resolve().parent
DATASETS_DIR = HERE / "datasets"
JOBS_DIR = HERE / "results" / "jobs"

sys.path.insert(0, str(HERE.parent / "power_opt"))
import config as po_config      # power_opt/config.py（方案A：任务前设属性）
import reader as po_reader
from solve import solve as po_solve
import gurobipy as gp

app = FastAPI(title="区域电力系统优化")
app.add_middleware(   # 跨域放行（开发期全放；上线改 allow_origins 为前端域名列表）
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATASETS: dict[str, dict] = {}      # datasetId → {path, parsed, area, year, uploadedAt}
JOBS: dict[str, dict] = {}          # jobId → 状态（内存注册表，v1 不做重启持久化）
QUEUE: "queue.Queue[str]" = queue.Queue()
STATUS_NAMES = {gp.GRB.OPTIMAL: "OPTIMAL", gp.GRB.TIME_LIMIT: "TIME_LIMIT",
                gp.GRB.INFEASIBLE: "INFEASIBLE", gp.GRB.UNBOUNDED: "UNBOUNDED",
                gp.GRB.INTERRUPTED: "INTERRUPTED"}


def _now() -> str:
    return pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")


# ============ POST /api/datasets ============

@app.post("/api/datasets")
async def upload_dataset(file: UploadFile):
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 模版文件")
    ds_id = "ds_" + uuid.uuid4().hex[:12]
    ds_dir = DATASETS_DIR / ds_id
    ds_dir.mkdir(parents=True, exist_ok=True)
    path = ds_dir / "template.xlsx"
    with open(path, "wb") as f:
        f.write(await file.read())
    try:
        parsed = parse_template(str(path))      # 缺 sheet/列/曲线 → ValueError
        tree = build_data_tree(path)
    except ValueError as e:
        shutil.rmtree(ds_dir, ignore_errors=True)
        raise HTTPException(422, f"模版校验失败: {e}") from None

    year, p = parsed["year"], parsed
    warnings = []
    if p["nuclear"]:
        warnings.append({"code": "NUCLEAR_NO_MIN_RATIO",
                         "message": "收资未含核电最小出力率列，核电按基荷处理（最小出力=额定容量）"})
    if p["transferCurve288"] and not p["transferCurve8760"]:
        warnings.append({"code": "TEST_CHANNEL",
                         "message": "送电协议仅含测试通道（288点曲线），非真实供电能力；"
                                    "开省间互济/跨区送电的请求将被拒绝"})
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

    # 表单默认值 = 内置默认 + 收资值浮出（前端不写死任何默认值）
    defaults = merge_config(None)
    defaults["scenario"]["startTime"] = f"{year}-01-01 00:00"
    defaults["province"]["hotReserveRatio"] = p["hotReserveRatio"]
    defaults["province"]["coldReserveRatio"] = p["coldReserveRatio"]
    defaults["demandResponse"]["shiftRatio"] = p["drShiftRatio"]
    defaults["demandResponse"]["cutRatio"] = p["drCutRatio"]

    DATASETS[ds_id] = {"path": str(path), "parsed": parsed,
                       "area": parsed["area"], "year": year, "uploadedAt": _now()}
    return {"datasetId": ds_id, "area": parsed["area"], "year": year,
            "dataTreeModels": tree, "warnings": warnings, "configDefaults": defaults,
            "fieldsMapping": FIELDS_MAPPING, "excludedUnits": excluded}


# ============ POST /api/jobs ============

class JobRequest(BaseModel):
    datasetId: str
    config: dict = {}


def validate_config(ds: dict, user: dict) -> list[str]:
    """设计方案 §3 校验清单；返回问题列表（空=通过）。"""
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
        problems.append("已开省间互济/跨区送电，但模版送电协议无真实 8760 送电曲线（仅测试通道）")
    return problems


@app.post("/api/jobs")
def create_job(req: JobRequest):
    ds = DATASETS.get(req.datasetId)
    if ds is None:
        raise HTTPException(404, f"dataset 不存在: {req.datasetId}")
    problems = validate_config(ds, req.config)
    if problems:
        raise HTTPException(422, "；".join(problems))
    job_id = "job_" + pd.Timestamp.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    JOBS[job_id] = {"jobId": job_id, "datasetId": req.datasetId, "status": "queued",
                    "userConfig": req.config, "submittedAt": _now(), "jobDir": str(job_dir)}
    QUEUE.put(job_id)
    return {"jobId": job_id}


# ============ GET 状态 / 结果 ============

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"job 不存在: {job_id}")
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
    return out


@app.get("/api/jobs/{job_id}/results")
def job_results(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"job 不存在: {job_id}")
    path = Path(job["jobDir"]) / "solution.json"
    if job["status"] != "solved" or not path.exists():
        raise HTTPException(409, f"任务状态 {job['status']}，尚无结果")
    return json.loads(path.read_text(encoding="utf-8"))


# ============ 串行队列 worker（单线程，同时只解一个）============

def _run_job(job_id: str):
    job = JOBS[job_id]
    ds = DATASETS[job["datasetId"]]
    job["status"], job["startedAt"] = "running", _now()
    job_dir = Path(job["jobDir"])
    summary = convert(ds["parsed"], job["userConfig"], job_dir)   # 三份中间表（配置注入）

    cfg = merge_config(job["userConfig"])
    sc = cfg["scenario"]
    # 方案A：直接设 power_opt config 模块属性（reader/units 调用时才读）
    po_config.T = sc["hours"]
    po_config.START_TIME = sc["startTime"]
    po_config.DELTA_T = 1.0
    po_config.HYDROLOGY_SCENARIO = sc["hydrology"]
    po_config.MIGAP = cfg["solver"]["mipGap"]
    po_config.TIME_LIMIT = cfg["solver"]["timeLimit_s"]
    po_config.USE_INTERPROVINCIAL = bool(sc["interprovincial"])
    po_config.USE_FIXED_TRANSFER = bool(sc["interRegionTransfer"])
    po_config.UNIT_FILE = job_dir / "分区机组信息表.xlsx"
    po_config.SYSTEM_FILE = job_dir / "算例系统参数.xlsx"
    po_config.PROFILE_FILE = job_dir / "运行数据.xlsx"
    po_config.RESULT_DIR = job_dir

    inp = po_reader.load_inputs()
    m, *_ = po_solve(inp)
    four = {   # 四项核对（solve 已打印进 stdout，此处从模型对象直取进服务响应）
        "variables": {"count": m.NumVars, "binary": m.NumBinVars},
        "constraints": {"rows": m.NumConstrs, "nonzeros": m.getAttr("NumNZs")},
        "status": STATUS_NAMES.get(m.Status, str(m.Status)),
        "objective": {"value": float(m.ObjVal) if m.SolCount else None,
                      "mipGapPct": 100 * float(m.getAttr("MIPGap")) if m.SolCount else None,
                      "runtime_s": m.Runtime},
    }
    job["fourItems"] = four
    if m.SolCount == 0:
        raise RuntimeError(f"求解无可行解（{four['status']}），"
                           f"日志: {po_config.RESULT_DIR / ('gurobi_py_T%d.log' % inp.T)}")
    result = json.loads((po_config.RESULT_DIR / "结果.json").read_text(encoding="utf-8"))
    job["report"] = result["report"]
    solution = {"jobId": job_id, "datasetId": job["datasetId"],
                "submittedAt": job["submittedAt"], "finishedAt": _now(),
                "fourItems": four, "effectiveConfig": cfg,
                "convertSummary": summary, "result": result}
    (job_dir / "solution.json").write_text(
        json.dumps(solution, ensure_ascii=False, default=str), encoding="utf-8")
    job["status"], job["finishedAt"] = "solved", _now()


def _worker():
    while True:
        job_id = QUEUE.get()
        job = JOBS.get(job_id)
        if job is None:
            continue
        try:
            _run_job(job_id)
        except Exception as e:      # 服务边界：任何异常都落 failed + error.log，不崩 worker
            job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"
            try:
                (Path(job["jobDir"]) / "error.log").write_text(
                    "userConfig: " + json.dumps(job["userConfig"], ensure_ascii=False) + "\n\n"
                    + traceback.format_exc(), encoding="utf-8")
            except OSError:
                pass


threading.Thread(target=_worker, daemon=True, name="solver-worker").start()
