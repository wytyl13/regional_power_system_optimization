#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/16 10:40
@Author  : weiyutao
@File    : position_chart.py
前端独有口径组装（v2 重构裁决：算法文档第四章/MATLAB 无此口径，故落 service 层）：
  build_position_chart  工作位置图 views tab（27 列老版 Excel 列序；
                        stacked 只堆 14 个发电列，其余 13 列为参照线）
  build_report          solution.json 的 report 19 字段（字段集冻结）
数据全部来自 Solution 与 AnalysisExport.metrics，不触碰求解器。
调用方需先把 power_opt 目录加入 sys.path（assemble.py 已做）或仅传纯数据。
"""
from __future__ import annotations

import numpy as np


def build_position_chart(inp, solution) -> dict:
    """工作位置图 tab（views.positionChart，原 analysis_export.build_views tab1 平移）。"""
    V = solution.values
    U = solution.units
    T = inp.time.T
    plot_end = min(168, T)
    P_D = inp.profiles.P_load - V["P_shift_out"] + V["P_shift_in"] - V["P_cut"]

    def ts(a):                              # 时序 → PNG 窗口 list
        a = np.asarray(a) + 0.0             # +0.0：负零归一，-0.0 不进 views
        return (a[:, :plot_end] if a.ndim > 1 else a[:plot_end]).tolist()

    # minimum 已是 机组×T 时变矩阵（2026-10-14 MATLAB 供暖日历切换），直接点乘
    th_min = (np.asarray(U["thermal"]["minimum"]) * V["u_th"]).sum(axis=0)
    gas_min = (np.asarray(U["gas"]["minimum"]) * V["u_gas"]).sum(axis=0)
    bio_min = (np.asarray(U["biomass"]["minimum"])
               * (1 - np.asarray(U["biomass"]["maintenance"]))).sum(axis=0)
    P_D_row = P_D.sum(axis=0)
    ZT = np.zeros(T)
    pc = [
        ("核电", V["P_nuc"].sum(axis=0), True),
        ("煤电最小技术出力", th_min, True),
        ("生物质最小技术出力", bio_min, True),
        ("气电最小技术出力", gas_min, True),
        ("水电强迫", V["P_hyd_ror"].sum(axis=0), True),
        ("气电可调", V["P_gas"].sum(axis=0) - gas_min, True),
        ("煤电可调", V["P_th"].sum(axis=0) - th_min, True),
        ("生物质可调", V["P_bio"].sum(axis=0) - bio_min, True),
        ("启停调峰气电", ZT, True), ("水电可调", V["P_hyd_res"].sum(axis=0), True),
        ("光热", V["P_csp_unit"].sum(axis=0), True),   # 2026-10-16 接入；N_csp=0 → 0 列，自检不受影响
        ("风电发电", V["P_wind"].sum(axis=0) + V["P_fr_wind_out"].sum(axis=0), True),
        ("光伏发电", V["P_pv"].sum(axis=0) + V["P_fr_pv_out"].sum(axis=0), True),
        ("电量不足", V["P_shed"].sum(axis=0), True),
        ("抽水蓄能", (V["P_ps_gen"] - V["P_ps_pump"]).sum(axis=0), True),
        ("新型储能", (V["P_es_discharge"] - V["P_es_charge"]
                      + V["P_fr_discharge"]).sum(axis=0), True),
        ("发电曲线", P_D_row + V["P_ps_pump"].sum(axis=0)
                     + V["P_es_charge"].sum(axis=0), False),
        ("修正后负荷曲线", P_D_row, False),
        ("需求响应", inp.profiles.P_load.sum(axis=0) - P_D_row, False),
        ("送电曲线", ZT, False), ("本地负荷", inp.profiles.P_load.sum(axis=0), False),
        ("水火备用", V["R_th_hot"].sum(axis=0) + V["R_hyd_hot"].sum(axis=0), False),
        ("旋转备用", ZT, False),   # 省级备用列老版常量近似，数值见 report.hotReserveMarginMW
        ("核电压出力", ZT, False), ("弃水", ZT, False), ("调峰不足", ZT, False),
        ("弃风", V["P_wind_curt"].sum(axis=0), False),
        ("弃光", V["P_pv_curt"].sum(axis=0), False),
    ]
    # 自检同老版口径：14 列堆叠合计 ≈ 修正后负荷
    assert np.abs(sum(p for _, p, s in pc if s) - P_D_row).max() < 1e-3, \
        "工作位置图堆叠合计≠修正后负荷：有列漏算"
    return {
        "title": "工作位置图",
        "panels": [{
            "type": "stacked", "title": "工作位置图",
            "series": {n: ts(p) for n, p, s in pc if s},
            "lines": {n: ts(p) for n, p, s in pc if not s},
            "xLabel": "时间（月/日 时:分）", "yLabel": "功率 / MW",
        }],
    }


def build_report(inp, solution, metrics: dict, scenario: str) -> dict:
    """report 19 字段（原 solution.write_result_json 的 report 块平移，字段集冻结）。"""
    return {
        "hours": inp.time.T,
        "startTime": str(inp.time.Time[0]), "endTime": str(inp.time.Time[-1]),
        "scenario": scenario,
        "problem": 0 if solution.four_items["status"] == "OPTIMAL"
                   else int(solution.four_items["objective"]["value"] or 0),
        "solverInfo": "Successfully solved (GUROBI)"
                      if solution.four_items["status"] == "OPTIMAL"
                      else solution.four_items["status"],
        "solverSeconds": solution.four_items["objective"]["runtime_s"],
        "objectiveYuan": solution.four_items["objective"]["value"],
        "balanceErrorMW": metrics["balanceErrorMW"],
        "minimumConstraintResidual": solution.slacks["minimumConstraintResidual"],
        "energyNotServedMWh": metrics["energyNotServed"],
        "lossOfLoadHours": metrics["lossOfLoadHours"],
        "maxLoadShortfallMW": metrics["maxLoadShortfall"],
        "renewableUtilization": metrics["renewableUtilization"][0],
        "CO2_t": metrics["CO2_t"],
        "hotReserveMarginMW": metrics["hotMargin"],
        "coldReserveMarginMW": metrics["coldMargin"],
        "maxStorageClosureErrorMWh": metrics["storageClosure"],
        "mipGap": solution.four_items["objective"]["mipGapPct"] / 100,
    }
