#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""求解结果的唯一定义与取值出口。"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

import gurobipy as gp
import numpy as np

from model.units import biomass as BI
from model.units import electrochemical as ES
from model.units import friendly as FR
from model.units import gas as GA
from model.units import hydro as HY
from model.units import nuclear as NU
from model.units import pumped_storage as PS
from model.units import thermal as TH


def _values(mvar: gp.MVar) -> np.ndarray:
    return np.asarray(mvar.X)


def _integers(mvar: gp.MVar) -> np.ndarray:
    return np.rint(_values(mvar)).astype(int)


def _series(keys, values):
    """一条 series 条目 {keys: 行标签, data: N×T 数值}；不写 time = 挂全局小时轴。"""
    return {"keys": [str(key) for key in keys],
            "data": np.asarray(values).tolist()}


def _aggregation(total_rows, areas):
    """机组 → 分区归属矩阵 (Narea,n)：第 i 台机组在第 area_i 行置 1。"""
    matrix = np.zeros((total_rows, len(areas)))
    matrix[areas, np.arange(len(areas))] = 1
    return matrix


def _hyd_curt_series(inp, U, V):
    """E_hyd_curt 非 hour 网格条目：keys 即周期标签（机组·月），一行一值。"""
    return {
        "keys": [f"{U['hydro_reservoir']['keys'][int(i)]}·"
                 f"{inp.Time[hours[0]].strftime('%Y-%m')}"
                 for i, hours in zip(U["hydro_reservoir"]["period_unit"],
                                    U["hydro_reservoir"]["period_hours"])],
        "data": np.asarray(V["E_hyd_curt"]).tolist(),
    }


def compute_metrics(inp, solution) -> dict:
    """组装期派生指标（MATLAB 1177-1190 自检 + report 专用量）。
    power_opt 侧唯一算口径的地方；write_result_json 与五张分析表从这里取数。"""
    V = solution.values
    U = solution.units
    F = solution.inputs_fields
    Delta_t = inp.Delta_t

    A_th = _aggregation(inp.Narea, U["thermal"]["area"])
    A_gas = _aggregation(inp.Narea, U["gas"]["area"])
    A_hyd_ror = _aggregation(inp.Narea, U["hydro_ror"]["area"])
    A_hyd_res = _aggregation(inp.Narea, U["hydro_reservoir"]["area"])
    A_ps = _aggregation(inp.Narea, U["pumped_storage"]["area"])
    A_es = _aggregation(inp.Narea, U["electrochemical"]["area"])
    A_fr = _aggregation(inp.Narea, U["friendly"]["area"])
    A_line = np.zeros((inp.Narea, inp.N_line))
    A_line[inp.line_from, np.arange(inp.N_line)] = -1
    A_line[inp.line_to, np.arange(inp.N_line)] = 1

    # 平衡残差 = 发电 + 储能净出力 + 净受入 + 失负荷 − 响应后负荷（MATLAB 1178）
    P_G = (A_th @ V["P_th"] + A_gas @ V["P_gas"]
           + _aggregation(inp.Narea, U["nuclear"]["area"]) @ V["P_nuc"]
           + _aggregation(inp.Narea, U["biomass"]["area"]) @ V["P_bio"]
           + V["P_wind"] + V["P_pv"]
           + A_hyd_ror @ V["P_hyd_ror"] + A_hyd_res @ V["P_hyd_res"]
           + A_fr @ (V["P_fr_wind_out"] + V["P_fr_pv_out"] + V["P_fr_discharge"]))
    P_sto = (A_ps @ V["P_ps_gen"] - A_ps @ V["P_ps_pump"]
             + A_es @ V["P_es_discharge"] - A_es @ V["P_es_charge"])
    P_tr = A_line @ V["P_line"]
    P_D = (inp.P_load - V["P_shift_out"] + V["P_shift_in"] - V["P_cut"])
    resid = P_G + P_sto + P_tr + V["P_shed"] - P_D
    print("功率平衡残差 max: %.3g MW（MATLAB 1178 行同款自检，应≈0）"
          % np.abs(resid).max())

    # 新能源利用率（省级聚合含友好站，MATLAB 1181-1186）
    A_prov = np.zeros((inp.Nprovince, inp.Narea))
    A_prov[inp.areaProvince, np.arange(inp.Narea)] = 1
    RE_avail = A_prov @ (inp.P_wind_available + inp.P_pv_available
                         + A_fr @ (U["friendly"]["wind_available"]
                                   + U["friendly"]["pv_available"]))
    RE_curt = A_prov @ (V["P_wind_curt"] + V["P_pv_curt"]
                        + A_fr @ (V["P_fr_wind_curt"] + V["P_fr_pv_curt"]))
    available_energy = RE_avail.sum(axis=1) * Delta_t
    curtailed_energy = RE_curt.sum(axis=1) * Delta_t
    utilization = np.full(inp.Nprovince, np.nan)
    has_re = available_energy > 0
    utilization[has_re] = 1 - curtailed_energy[has_re] / available_energy[has_re]
    energy_not_served = float(V["P_shed"].sum() * Delta_t)       # 1187
    loss_of_load_hours = float((V["P_shed"] > 1e-4).any(axis=0).sum()) * Delta_t  # 1188
    max_load_shortfall = float(V["P_shed"].sum(axis=0).max())    # 1189
    print("新能源利用率：%.2f%%，弃风光电量：%.3f MWh，失负荷：%.3f MWh"
          % (100 * utilization[0], curtailed_energy.sum(), energy_not_served))
    print("缺电小时：%.0f h，最大缺口：%.3f MW"
          % (loss_of_load_hours, max_load_shortfall))

    # 热/冷备用裕度（1002/1003 同式对自身比例约束的最小富余）
    Rhot = A_prov @ (A_th @ V["R_th_hot"] + A_gas @ V["R_gas_hot"]
                     + A_hyd_res @ V["R_hyd_hot"] + A_ps @ V["R_ps_hot"]
                     + A_es @ V["R_es_hot"])
    Rcold = A_prov @ (A_th @ V["R_th_cold"] + A_gas @ V["R_gas_cold"])
    hot_margin = float((Rhot - np.repeat(
        inp.hotReserveRatio[:, None], inp.T, 1) * inp.P_week_peak).min())
    cold_margin = float((Rcold - np.repeat(
        inp.coldReserveRatio[:, None], inp.T, 1) * inp.P_week_peak).min())

    # CO2 总量（1001 同式）/ 储能首末能量闭合
    co2_t = float(Delta_t * (V["CO2_th"].sum()
                  + (np.asarray(F["CO2_gas_factor"])[:, None] * V["P_gas"]).sum()))
    closure = float(max(np.abs(data[:, -1] - data[:, 0]).max(initial=0.0)
                        for data in (V["E_ps"], V["E_es"], V["E_fr"])))
    return {
        "P_D": P_D, "Rhot": Rhot,
        "balanceErrorMW": float(np.abs(resid).max()),
        "renewableUtilization": utilization,
        "curtailedEnergy": curtailed_energy,
        "energyNotServed": energy_not_served,
        "lossOfLoadHours": loss_of_load_hours,
        "maxLoadShortfall": max_load_shortfall,
        "hotMargin": hot_margin, "coldMargin": cold_margin,
        "CO2_t": co2_t, "storageClosure": closure,
    }


@dataclass(frozen=True)
class SolutionData:
    """纯数据容器；服务层不再触碰 Gurobi 模型对象。"""

    four_items: dict
    values: dict[str, np.ndarray]
    costs: dict[str, float]
    units: dict
    inputs_fields: dict
    slacks: dict


def extract_all(m: gp.Model, inp, coal: TH.CoalInputs, gas: GA.GasInputs,
                nuc: NU.NuclearInputs, bio: BI.BiomassInputs, ps: PS.PSInputs,
                es: ES.ESInputs, hyd: HY.HydInputs, fr: FR.FrInputs,
                Tv: TH.ThermalVars, Gv: GA.GasVars, Nv: NU.NuclearVars,
                Bv: BI.BiomassVars, Pv: PS.PSVars, Ev: ES.ESVars, Hv: HY.HydVars,
                Fv: FR.FrVars, Sv, cost_exprs: dict) -> SolutionData:
    """从求解模型一次性取回结果；本函数只做取值，不做业务口径推导。"""
    four_items = {
        "variables": {"count": int(m.NumVars), "binary": int(m.NumBinVars)},
        "constraints": {"rows": int(m.NumConstrs), "nonzeros": int(m.getAttr("NumNZs"))},
        "status": {gp.GRB.OPTIMAL: "OPTIMAL", gp.GRB.TIME_LIMIT: "TIME_LIMIT",
                   gp.GRB.INFEASIBLE: "INFEASIBLE", gp.GRB.UNBOUNDED: "UNBOUNDED",
                   gp.GRB.INTERRUPTED: "INTERRUPTED"}.get(m.Status, str(m.Status)),
        "objective": {
            "value": float(m.ObjVal) if m.SolCount else None,
            "mipGapPct": 100 * float(m.getAttr("MIPGap")) if m.SolCount else None,
            "runtime_s": float(m.Runtime),
        },
    }

    # TODO 交接文档约定：lambda_th/segment_th、V/W_*_count、
    # u_ps_area_*、P_shift_pair 是建模辅助解，不进入服务层取值集。
    values = {
        "P_th": _values(Tv.P_th), "u_th": _integers(Tv.u_th),
        "v_th": _integers(Tv.v_th), "w_th": _integers(Tv.w_th),
        "R_th_hot": _values(Tv.R_th_hot), "R_th_cold": _values(Tv.R_th_cold),
        "F_th": _values(Tv.F_th), "CO2_th": _values(Tv.CO2_th),
        "P_gas": _values(Gv.P_gas), "u_gas": _integers(Gv.u_gas),
        "v_gas": _integers(Gv.v_gas), "w_gas": _integers(Gv.w_gas),
        "R_gas_hot": _values(Gv.R_gas_hot), "R_gas_cold": _values(Gv.R_gas_cold),
        "P_nuc": _values(Nv.P_nuc), "P_bio": _values(Bv.P_bio),
        "P_ps_gen": _values(Pv.P_ps_gen), "P_ps_pump": _values(Pv.P_ps_pump),
        "u_ps_gen": _integers(Pv.u_ps_gen), "u_ps_pump": _integers(Pv.u_ps_pump),
        "R_ps_hot": _values(Pv.R_ps_hot), "E_ps": _values(Pv.E_ps),
        "P_es_charge": _values(Ev.P_es_charge),
        "P_es_discharge": _values(Ev.P_es_discharge),
        "u_es_ch": _integers(Ev.u_es_ch), "u_es_dis": _integers(Ev.u_es_dis),
        "R_es_hot": _values(Ev.R_es_hot), "E_es": _values(Ev.E_es),
        "P_hyd_ror": _values(Hv.P_hyd_ror), "P_hyd_res": _values(Hv.P_hyd_res),
        "R_hyd_hot": _values(Hv.R_hyd_hot), "E_hyd_curt": _values(Hv.E_hyd_curt),
        "P_fr_wind_out": _values(Fv.P_fr_wind_out),
        "P_fr_wind_ch": _values(Fv.P_fr_wind_ch),
        "P_fr_wind_curt": _values(Fv.P_fr_wind_curt),
        "P_fr_pv_out": _values(Fv.P_fr_pv_out),
        "P_fr_pv_ch": _values(Fv.P_fr_pv_ch),
        "P_fr_pv_curt": _values(Fv.P_fr_pv_curt),
        "P_fr_charge": _values(Fv.P_fr_charge),
        "P_fr_discharge": _values(Fv.P_fr_discharge),
        "u_fr_ch": _integers(Fv.u_fr_ch), "u_fr_dis": _integers(Fv.u_fr_dis),
        "E_fr": _values(Fv.E_fr),
        "P_wind": _values(Sv.P_wind), "P_pv": _values(Sv.P_pv),
        "P_wind_curt": _values(Sv.P_wind_curt),
        "P_pv_curt": _values(Sv.P_pv_curt), "P_shed": _values(Sv.P_shed),
        "P_line": _values(Sv.P_line), "P_shift_out": _values(Sv.P_shift_out),
        "P_shift_in": _values(Sv.P_shift_in), "P_cut": _values(Sv.P_cut),
    }

    units = {
        "thermal": {"keys": coal.UnitKey_th, "names": coal.name_th,
                    "rated": coal.P_th_rated,
                    "minimum": coal.P_th_min, "maintenance": coal.m_th,
                    "area": coal.area_th},
        "gas": {"keys": gas.UnitKey_gas, "names": gas.name_gas,
                "rated": gas.P_gas_rated,
                "minimum": gas.P_gas_min, "maintenance": gas.m_gas,
                "area": gas.area_gas},
        "nuclear": {"keys": nuc.UnitKey_nuc, "rated": nuc.P_nuc_max,
                    "maintenance": nuc.m_nuc, "area": nuc.area_nuc},
        "biomass": {"keys": bio.UnitKey_bio, "rated": bio.P_bio_rated,
                    "minimum": bio.P_bio_min, "maintenance": bio.m_bio,
                    "area": bio.area_bio},
        "pumped_storage": {"keys": ps.UnitKey_ps, "names": ps.name_ps,
                           "area": ps.area_ps,
                           "energy_capacity": ps.E_ps_max,
                           "soc_min": ps.SOC_ps_min, "soc_max": ps.SOC_ps_max,
                           "soc_initial": ps.SOC_ps_initial,
                           "maintenance": ps.m_ps},
        "electrochemical": {"keys": es.UnitKey_es, "names": es.name_es,
                            "area": es.area_es,
                            "energy_capacity": es.E_es_max,
                            "energy_min": es.E_es_min,
                            "energy_max": es.E_es_upper,
                            "energy_initial": es.E_es_initial,
                            "soc_min": es.E_es_min / es.E_es_max,   # =表列 SOC_MIN
                            "soc_max": es.E_es_upper / es.E_es_max},  # =表列 SOC_MAX
        "hydro_ror": {"keys": hyd.UnitKey_ror, "area": hyd.area_hyd_ror,
                      "capacity": hyd.CAP_ror},
        "hydro_reservoir": {"keys": hyd.UnitKey_res, "area": hyd.area_hyd_res,
                            "capacity": hyd.CAP_res,
                            "period_unit": hyd.hydPeriodUnit,
                            "period_hours": hyd.hydPeriodHours},
        "friendly": {"keys": fr.UnitKey_fr, "names": fr.name_fr,
                     "area": fr.area_fr,
                     "energy_capacity": fr.E_fr_max,
                     "energy_min": fr.E_fr_min,
                     "energy_max": fr.E_fr_upper,
                     "energy_initial": fr.E_fr_initial,
                     "soc_min": fr.E_fr_min / fr.E_fr_max,   # =表列 SOC_MIN
                     "soc_max": fr.E_fr_upper / fr.E_fr_max,  # =表列 SOC_MAX
                     "wind_available": fr.P_fr_wind_available,
                     "pv_available": fr.P_fr_pv_available},
    }

    inputs_fields = {
        "T": inp.T, "Delta_t": inp.Delta_t,
        "AreaID": inp.AreaID, "AreaName": inp.AreaName,
        "Narea": inp.Narea, "Nprovince": inp.Nprovince,
        "areaProvince": inp.areaProvince, "P_load": inp.P_load,
        "P_wind_available": inp.P_wind_available,
        "P_pv_available": inp.P_pv_available,
        "N_line": inp.N_line, "line_from": inp.line_from,
        "line_to": inp.line_to, "P_week_peak": inp.P_week_peak,
        "hotReserveRatio": inp.hotReserveRatio,
        "coldReserveRatio": inp.coldReserveRatio,
        "CO2_gas_factor": gas.CO2_gas_factor,
        "P_hyd_ror_min": hyd.P_hyd_ror_min,
        "P_hyd_res_min": hyd.P_hyd_res_min,
    }

    # 成本分项取值（表达式在 optimize 之后才可求值；float 直接 .getValue()）
    costs = {key: float(expr.getValue() if hasattr(expr, "getValue") else expr)
             for key, expr in cost_exprs.items()}

    try:
        slacks = {"minimumConstraintResidual": float(
            np.where(
                [c.Sense == ">" for c in m.getConstrs()],
                -np.asarray(m.getAttr("Slack", m.getConstrs()), dtype=float),
                np.where(
                    [c.Sense == "=" for c in m.getConstrs()],
                    -np.abs(np.asarray(m.getAttr("Slack", m.getConstrs()), dtype=float)),
                    np.asarray(m.getAttr("Slack", m.getConstrs()), dtype=float),
                ),
            ).min()
        )}
    except gp.GurobiError:
        slacks = {"minimumConstraintResidual": None}

    return SolutionData(
        four_items=four_items,
        values=values,
        costs=costs,
        units=units,
        inputs_fields=inputs_fields,
        slacks=slacks,
    )


def write_result_json(inp, solution, config, metrics) -> None:
    """落 结果.json（服务/前端口径；compare.py 的 Python 侧输入）。
    report 19 字段、positionChart 27 列、series/labels 均保持 solve.py 重构前口径。
    metrics = 残差/利用率等组装期派生量（与 report 一并落盘，函数内不重复算两遍）。"""
    V = solution.values
    U = solution.units
    Delta_t = inp.Delta_t
    A = lambda name, n=None: _aggregation(inp.Narea, U[name]["area"]) \
        if n is None else _aggregation(inp.Narea, U[name]["area"])

    A_line = np.zeros((inp.Narea, inp.N_line))
    A_line[inp.line_from, np.arange(inp.N_line)] = -1
    A_line[inp.line_to, np.arange(inp.N_line)] = 1

    series = {
        "P_th": _series(U["thermal"]["keys"], V["P_th"]),
        "u_th": _series(U["thermal"]["keys"], V["u_th"]),
        "P_gas": _series(U["gas"]["keys"], V["P_gas"]),
        "u_gas": _series(U["gas"]["keys"], V["u_gas"]),
        "P_nuc": _series(U["nuclear"]["keys"], V["P_nuc"]),
        "P_bio": _series(U["biomass"]["keys"], V["P_bio"]),
        "P_wind": _series(inp.AreaName, V["P_wind"]),
        "P_pv": _series(inp.AreaName, V["P_pv"]),
        "P_wind_curt": _series(inp.AreaName, V["P_wind_curt"]),
        "P_pv_curt": _series(inp.AreaName, V["P_pv_curt"]),
        "P_shed": _series(inp.AreaName, V["P_shed"]),
        "P_shift_out": _series(inp.AreaName, V["P_shift_out"]),
        "P_shift_in": _series(inp.AreaName, V["P_shift_in"]),
        "P_cut": _series(inp.AreaName, V["P_cut"]),
        "P_ps_gen": _series(U["pumped_storage"]["keys"], V["P_ps_gen"]),
        "P_ps_pump": _series(U["pumped_storage"]["keys"], V["P_ps_pump"]),
        "E_ps": _series(U["pumped_storage"]["keys"], V["E_ps"]),
        "P_es_charge": _series(U["electrochemical"]["keys"], V["P_es_charge"]),
        "P_es_discharge": _series(U["electrochemical"]["keys"], V["P_es_discharge"]),
        "E_es": _series(U["electrochemical"]["keys"], V["E_es"]),
        "P_hyd_ror": _series(U["hydro_ror"]["keys"], V["P_hyd_ror"]),
        "P_hyd_res": _series(U["hydro_reservoir"]["keys"], V["P_hyd_res"]),
        "R_hyd_hot": _series(U["hydro_reservoir"]["keys"], V["R_hyd_hot"]),
        "E_hyd_curt": _hyd_curt_series(inp, U, V),
        "P_fr_wind_out": _series(U["friendly"]["keys"], V["P_fr_wind_out"]),
        "P_fr_pv_out": _series(U["friendly"]["keys"], V["P_fr_pv_out"]),
        "P_fr_charge": _series(U["friendly"]["keys"], V["P_fr_charge"]),
        "P_fr_discharge": _series(U["friendly"]["keys"], V["P_fr_discharge"]),
        "E_fr": _series(U["friendly"]["keys"], V["E_fr"]),
    }

    # ---- 工作位置图（老版 27 列口径，列名/列序同老版 Excel；缺项补 0）----
    u = V["u_th"]; ug = V["u_gas"]
    P_th_sum = V["P_th"].sum(axis=0); P_gas_sum = V["P_gas"].sum(axis=0)
    th_min = (np.asarray(U["thermal"]["minimum"])[:, None] * u).sum(axis=0)
    gas_min = (np.asarray(U["gas"]["minimum"])[:, None] * ug).sum(axis=0)
    bio_min = (np.asarray(U["biomass"]["minimum"])[:, None]
               * (1 - np.asarray(U["biomass"]["maintenance"]))).sum(axis=0)
    ps_net = (V["P_ps_gen"] - V["P_ps_pump"]).sum(axis=0)
    es_net = (V["P_es_discharge"] - V["P_es_charge"]
              + V["P_fr_discharge"]).sum(axis=0)
    PD_tot = metrics["P_D"].sum(axis=0)
    ZT = np.zeros(inp.T)
    pc = OrderedDict((   # 27 列：老版顺序（列序即老 Excel 列序，勿动）
        ("核电", V["P_nuc"].sum(axis=0)),
        ("煤电最小技术出力", th_min),
        ("生物质最小技术出力", bio_min),
        ("气电最小技术出力", gas_min),
        ("水电强迫", V["P_hyd_ror"].sum(axis=0)),
        ("气电可调", P_gas_sum - gas_min),
        ("煤电可调", P_th_sum - th_min),
        ("生物质可调", V["P_bio"].sum(axis=0) - bio_min),
        ("启停调峰气电", ZT),                    # 老版列，当前模型无对应量 → 0
        ("水电可调", V["P_hyd_res"].sum(axis=0)),
        ("风电发电", V["P_wind"].sum(axis=0) + V["P_fr_wind_out"].sum(axis=0)),
        ("光伏发电", V["P_pv"].sum(axis=0) + V["P_fr_pv_out"].sum(axis=0)),
        ("电量不足", V["P_shed"].sum(axis=0)),
        ("抽水蓄能", ps_net),
        ("新型储能", es_net),
        ("发电曲线", PD_tot + V["P_ps_pump"].sum(axis=0) + V["P_es_charge"].sum(axis=0)),
        ("修正后负荷曲线", PD_tot),
        ("需求响应", inp.P_load.sum(axis=0) - PD_tot),
        ("送电曲线", ZT),                        # 当前模型无对应量 → 0
        ("本地负荷", inp.P_load.sum(axis=0)),
        ("水火备用", V["R_th_hot"].sum(axis=0) + V["R_hyd_hot"].sum(axis=0)),
        ("旋转备用", metrics["Rhot"].sum(axis=0)),
        ("核电压出力", ZT),                      # 当前模型无对应量 → 0
        ("弃水", ZT),                            # 月度电量在 series.E_hyd_curt，不造小时值
        ("调峰不足", ZT),                        # 当前模型无对应量 → 0
        ("弃风", V["P_wind_curt"].sum(axis=0)),
        ("弃光", V["P_pv_curt"].sum(axis=0)),
    ))
    stack = (pc["核电"] + pc["煤电最小技术出力"] + pc["煤电可调"]
             + pc["生物质最小技术出力"] + pc["生物质可调"]
             + pc["气电最小技术出力"] + pc["气电可调"] + pc["水电强迫"]
             + pc["水电可调"] + pc["风电发电"] + pc["光伏发电"]
             + pc["抽水蓄能"] + pc["新型储能"] + pc["电量不足"])
    assert np.abs(stack - PD_tot).max() < 1e-3, \
        "工作位置图堆叠合计≠修正后负荷：有列漏算"
    position_chart = {key: np.asarray(value).tolist()
                      for key, value in pc.items()}

    report = {
        "hours": inp.T,
        "startTime": str(inp.Time[0]), "endTime": str(inp.Time[-1]),
        "scenario": config.HYDROLOGY_SCENARIO,
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

    labels = {
        "P_th": "煤电出力(MW)", "u_th": "煤电开机(1/0)",
        "P_gas": "气电出力(MW)", "u_gas": "气电开机(1/0)",
        "P_nuc": "核电出力(MW)", "P_bio": "生物质出力(MW)",
        "P_wind": "风电出力(MW)", "P_pv": "光伏出力(MW)",
        "P_wind_curt": "风电弃电(MW)", "P_pv_curt": "光伏弃电(MW)",
        "P_shed": "未供电负荷(MW)",
        "P_shift_out": "需求响应转出(MW)", "P_shift_in": "需求响应转入(MW)",
        "P_cut": "需求响应削减(MW)",
        "P_ps_gen": "抽蓄发电(MW)", "P_ps_pump": "抽蓄抽水(MW)",
        "E_ps": "抽蓄蓄电量(MWh)，T+1列：第0列=初值，第k列=第k小时末",
        "P_es_charge": "电储能充电(MW)", "P_es_discharge": "电储能放电(MW)",
        "E_es": "电储能荷电量(MWh)，T+1列（同E_ps）",
        "P_hyd_ror": "径流式水电出力(MW)", "P_hyd_res": "库容式水电出力(MW)",
        "R_hyd_hot": "水电热备用(MW)",
        "E_hyd_curt": "水电弃水量(MWh)，按机组×月周期一行",
        "P_fr_wind_out": "友好电站风电直送(MW)", "P_fr_pv_out": "友好电站光伏直送(MW)",
        "P_fr_charge": "友好电站充电(MW)", "P_fr_discharge": "友好电站放电(MW)",
        "E_fr": "友好电站荷电量(MWh)，T+1列（同E_ps）",
    }
    doc = {
        "meta": {
            "T": inp.T, "delta_t_hours": inp.Delta_t,
            "startTime": str(inp.Time[0]), "endTime": str(inp.Time[-1]),
            "scenario": config.HYDROLOGY_SCENARIO,
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "report": report,
        "positionChart": position_chart,
        "time": [timestamp.strftime("%Y-%m-%d %H:%M:%S")
                 for timestamp in inp.Time],
        "labels": labels,
        "series": series,
        "costs": solution.costs,          # 成本分项（六表取值用；恒等式见 test_algrithm）
    }
    path = config.RESULT_DIR / "结果.json"
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(doc, stream, ensure_ascii=False, separators=(",", ":"))
    print("已导出: %s" % path)


def write_timeseries_csv(inp, solution) -> None:
    values = solution.values
    units = solution.units
    cols = {"Time": [timestamp.strftime("%Y-%m-%d %H:%M")
                     for timestamp in inp.Time]}

    def area_columns(prefix, values_array):
        for index, area_id in enumerate(inp.AreaID):
            cols[f"{prefix}_{area_id}"] = np.asarray(values_array)[index]

    def unit_columns(prefix, unit_name, values_array, skip=0):
        for key, row in zip(units[unit_name]["keys"], np.asarray(values_array)):
            cols[f"{prefix}_{key}"] = np.asarray(row)[skip:]

    area_columns("P_load", inp.P_load)
    unit_columns("P_th", "thermal", values["P_th"])
    unit_columns("u_th", "thermal", values["u_th"])
    unit_columns("P_gas", "gas", values["P_gas"])
    unit_columns("u_gas", "gas", values["u_gas"])
    unit_columns("P_nuc", "nuclear", values["P_nuc"])
    unit_columns("P_bio", "biomass", values["P_bio"])
    for prefix, key in (
            ("P_wind", "P_wind"), ("P_pv", "P_pv"),
            ("P_wind_curt", "P_wind_curt"), ("P_pv_curt", "P_pv_curt"),
            ("P_shed", "P_shed"), ("P_shift_out", "P_shift_out"),
            ("P_shift_in", "P_shift_in"), ("P_cut", "P_cut")):
        area_columns(prefix, values[key])
    unit_columns("P_ps_gen", "pumped_storage", values["P_ps_gen"])
    unit_columns("P_ps_pump", "pumped_storage", values["P_ps_pump"])
    unit_columns("E_ps", "pumped_storage", values["E_ps"], skip=1)
    unit_columns("P_es_charge", "electrochemical", values["P_es_charge"])
    unit_columns("P_es_discharge", "electrochemical", values["P_es_discharge"])
    unit_columns("E_es", "electrochemical", values["E_es"], skip=1)
    unit_columns("P_hyd_ror", "hydro_ror", values["P_hyd_ror"])
    unit_columns("P_hyd_res", "hydro_reservoir", values["P_hyd_res"])
    unit_columns("R_hyd_hot", "hydro_reservoir", values["R_hyd_hot"])

    reservoir_keys = units["hydro_reservoir"]["keys"]
    period_unit = np.asarray(units["hydro_reservoir"]["period_unit"])
    period_hours = units["hydro_reservoir"]["period_hours"]
    hydro_curt = values["E_hyd_curt"]
    for period_index, unit_index in enumerate(period_unit):
        key = reservoir_keys[int(unit_index)]
        month = inp.Time[period_hours[period_index][0]].strftime("%Y%m")
        cols[f"E_hyd_curt_{key}_{month}"] = np.full(
            inp.T, hydro_curt[period_index, 0])

    for prefix, key in (
            ("P_fr_wind_out", "P_fr_wind_out"),
            ("P_fr_pv_out", "P_fr_pv_out"),
            ("P_fr_charge", "P_fr_charge"),
            ("P_fr_discharge", "P_fr_discharge")):
        unit_columns(prefix, "friendly", values[key])
    unit_columns("E_fr", "friendly", values["E_fr"], skip=1)

    import config
    path = config.RESULT_DIR / f"时序结果_T{inp.T}.csv"
    import pandas as pd
    pd.DataFrame(cols).to_csv(path, index=False, encoding="utf-8")
    print("已导出: %s" % path)
