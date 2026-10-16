#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/28 17:40
@Author  : weiyutao
@File    : compare.py
读两边最新的合并时序 CSV 逐列比对（海南 v2 口径，不再重新求解）：
  MATLAB: {MATLAB_DIR}/results/T{T}_gap*_{时间戳}/ 时序结果_T{T}.csv + 海南{T}小时计算结果.mat(report)
  Python: {RESULT_DIR}/T{T}_gap*_{时间戳}/      时序结果_T{T}.csv + 结果.json(four_items/metrics，
          v2 瘦身后 objective 自 four_items 读、参考指标自 metrics 读)
判定两段式（2026-09-22 用户定）：
  第一段（硬门槛，不达标即 FAIL）：目标值相对偏差 ≤1e-6；各变量组逐小时总出力
    （u_* 组即逐小时开机台数和）最大差 ≤1e-6。
  第二段（同参数机组互换，只报不判）：孪生机组逐台/逐格差异是数学等价解的正常互换。
用法：python compare.py 168 [matlab目标值]（不给则从 .mat 的 report.objectiveYuan 读）
"""
from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd
import scipy.io as sio

import config

# 已知变量名（列名 = 变量 + "_" + 机组编号/分区号），与 solve.py / m 导出段一致；
# 按长度降序匹配，避免 P_wind 抢先匹配 P_wind_curt_* 列
VARS = ["P_load", "P_th", "u_th", "P_gas", "u_gas", "P_nuc", "P_bio",
        "P_wind", "P_pv", "P_wind_curt", "P_pv_curt", "P_shed",
        "P_shift_out", "P_shift_in", "P_cut", "P_ps_gen", "P_ps_pump", "E_ps",
        "P_es_charge", "P_es_discharge", "E_es", "P_hyd_ror", "P_hyd_res",
        "R_hyd_hot", "E_hyd_curt", "P_fr_wind_out", "P_fr_pv_out",
        "P_fr_charge", "P_fr_discharge", "E_fr"]
VARS_SORTED = sorted(VARS, key=len, reverse=True)


def _var_of(col: str) -> str:
    """列名 → 变量名（剥掉 _机组编号/_分区号 后缀）。"""
    for v in VARS_SORTED:
        if col == v or col.startswith(v + "_"):
            return v
    raise KeyError(f"列 {col} 不属于任何已知变量（检查两边导出代码的列名）")


def _latest(pattern) -> str:
    hits = sorted(glob.glob(str(pattern)))
    if not hits:
        raise FileNotFoundError(f"找不到 {pattern}（先跑对应侧求解）")
    return hits[-1]


def run(T: int, obj_ref: float | None = None):
    mat_dir = _latest(config.MATLAB_DIR / "results" / f"T{T}_gap*")
    py_dir = _latest(config.RESULT_DIR / f"T{T}_gap*")
    print("MATLAB 目录:", mat_dir)
    print("Python 目录:", py_dir)

    mat_csv = pd.read_csv(f"{mat_dir}/时序结果_T{T}.csv")
    py_csv = pd.read_csv(f"{py_dir}/时序结果_T{T}.csv")

    # ---- 0) 结构一致：列名顺序 + 行数（不一致即 FAIL，无法逐列比）----
    if list(mat_csv.columns) != list(py_csv.columns):
        only_m = [c for c in mat_csv.columns if c not in py_csv.columns]
        only_p = [c for c in py_csv.columns if c not in mat_csv.columns]
        print("FAIL 列名不一致：仅MATLAB有 %s；仅Python有 %s" % (only_m[:8], only_p[:8]))
        print("逐时段对照结论: FAIL（两段式判定，2026-09-22）")
        return
    assert len(mat_csv) == len(py_csv) == T, f"行数应等于 T={T}"
    print("结构一致：列数 %d（含 Time），行数 %d" % (len(mat_csv.columns), len(mat_csv)))

    # ---- 1) 目标值（硬门槛）+ 关键指标参考差 ----
    # Python 侧结果.json 已 v2 瘦身：objective 在 four_items、参考指标在 metrics
    doc_py = json.load(open(f"{py_dir}/结果.json", encoding="utf-8"))
    mets_py = doc_py.get("metrics", {})
    mets_py["mipGap"] = doc_py["four_items"]["objective"]["mipGapPct"] / 100
    rep_mat = sio.loadmat(f"{mat_dir}/海南{T}小时计算结果.mat",
                          struct_as_record=False, squeeze_me=True)["report"]
    obj_mat = obj_ref if obj_ref is not None else float(rep_mat.objectiveYuan)
    obj_py = float(doc_py["four_items"]["objective"]["value"])
    rel_obj = (obj_py - obj_mat) / obj_mat
    ok_obj = abs(rel_obj) <= 1e-6
    print("目标值 python=%.6f  matlab=%.6f  相对偏差=%.3e" % (obj_py, obj_mat, rel_obj))
    for k in ["renewableUtilization", "CO2_t", "energyNotServedMWh", "mipGap"]:
        print("  [参考] %s: py=%s  mat=%s" % (k, mets_py.get(k), getattr(rep_mat, k)))

    # ---- 2) 逐变量组：硬门槛逐小时总和；软口径逐格（孪生机组互换只报不判）----
    groups: dict[str, list[str]] = {}
    for c in py_csv.columns:
        if c != "Time":
            groups.setdefault(_var_of(c), []).append(c)
    fail = []
    for v in VARS:                          # 按 VARS 固定顺序输出
        if v not in groups:
            continue
        cols = groups[v]
        A = py_csv[cols].to_numpy(float)
        B = mat_csv[cols].to_numpy(float)
        d_hour = np.abs(A.sum(axis=1) - B.sum(axis=1)).max()
        d_cell = np.abs(A - B).max()
        if d_hour > 1e-6:
            fail.append(v)
        print("[%s] %-14s 逐小时总和最大差 %.3e；[软] 逐格最大差 %.3e（%d 列）"
              % ("硬√" if d_hour <= 1e-6 else "硬×", v, d_hour, d_cell, len(cols)))

    # ---- 自查摘要（Python 侧口径）----
    dt = float(config.DELTA_T)
    sh = py_csv[[c for c in py_csv.columns if c != "Time" and _var_of(c) == "P_shed"]].to_numpy(float)
    print("[自查] 未供电量: %.2f MWh（海南枯水年 48h 参考值 4725.20 MWh）" % (sh.sum() * dt))

    ok = ok_obj and not fail
    print("逐时段对照结论:", "PASS" if ok else "FAIL", "（两段式判定，2026-09-22）")
    if not ok:
        print("FAIL 原因:", "目标值" if not ok_obj else "", "、".join(fail))


if __name__ == "__main__":
    import sys
    T = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    obj_ref = float(sys.argv[2]) if len(sys.argv) > 2 else None
    run(T, obj_ref)
