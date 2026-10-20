#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/16 10:20
@Author  : weiyutao
@File    : solution.py
求解结果的唯一定义与取值出口（Solution 类）。

v2 重构（docs/solution重构设计-第四章口径.md，2026-10-16 用户裁决）：
  - 本文件只做取值：第四章『模型输入与输出变量说明』全量输出 + 成本 + 输入镜像
    + 四项核对。指标计算（compute_metrics）移 analysis_export.AnalysisExport；
    前端件（report/positionChart/series/labels）移 service 层，series/labels 舍弃。
  - 求解变量定义在上游 model 层（units.add_vars + context.add_vars）；
    本类只拿 Vars 句柄取 .X，不创建任何变量/约束。下游零 gurobipy 依赖。
  - 结果.json 瘦身为算法对拍件：meta + four_items + costs + metrics 标量
    （compare.py 读 objective 改自 four_items；前端字段移 service 组装）。
"""


from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import cached_property

import gurobipy as gp
import numpy as np
import pandas as pd


def _values(mvar: gp.MVar) -> np.ndarray:
    """取 MVar 解值（float 矩阵）。

    Args:
        mvar (gp.MVar): 已求解的决策变量矩阵。

    Returns:
        np.ndarray: 同形状解值数组。
    """
    return np.asarray(mvar.X)


def _integers(mvar: gp.MVar) -> np.ndarray:
    """取 MVar 解值并四舍五入成 int（0/1 状态变量）。

    Args:
        mvar (gp.MVar): 已求解的 0/1 决策变量矩阵。

    Returns:
        np.ndarray: 同形状 int 数组。
    """
    return np.rint(_values(mvar)).astype(int)


def _agg(narea, area):
    """机组 → 分区归属矩阵 (Narea,n)：§12.1 汇总口径用。

    Args:
        narea (int): 分区数。
        area (array): 各机组 0 基分区索引。

    Returns:
        np.ndarray: 归属矩阵。
    """
    matrix = np.zeros((narea, len(area)))
    matrix[area, np.arange(len(area))] = 1
    return matrix


def _page_frame(index, cols):
    """(名称, 口径, 数据) 列集 → 输出页 DataFrame（单行列头，index=日期）。
    口径行不入表（2026-10-20 用户裁决：只保留第一行表头，第二行描述去掉）。

    Args:
        index (list): 日期字符串列表（T 或 T+1 个）。
        cols (list): (名称, 口径, 数据 list) 三元组序列（口径仅构建留痕）。

    Returns:
        pd.DataFrame: index=日期、columns=名称 的输出页。
    """
    return pd.DataFrame({name: data for name, _, data in cols},
                        index=pd.Index(index, name="日期"),
                        columns=[name for name, _, _ in cols])


def _detail_frame(index, items):
    """明细项 → 输出页：每项展开为汇总列 + 逐机/逐区列。

    Args:
        index (list): 日期字符串列表（T 个）。
        items (list): (标签, 变量名, 矩阵, 键列表, 单位) 元组序列。

    Returns:
        pd.DataFrame: index=日期的明细页（单行列头）。
    """
    cols = []
    for label, var, ev, ek, unit in items:
        ev = np.asarray(ev, dtype=float)
        cols.append((f"{label}｜汇总", f"{var} 按行求和 / {unit}",
                     ev.sum(axis=0).tolist()))
        for j in range(ev.shape[0]):
            cols.append((f"{label}｜{ek[j]}", f"{var}({j + 1},:) / {unit}",
                         ev[j].tolist()))
    return _page_frame(index, cols)


class Solution:
    """第四章『模型输入与输出变量说明』各模型输出信息表的取值出口。

    求解变量定义在上游 model 层（units.add_vars + context.add_vars），
    本类只拿 Vars 句柄取 .X，不创建任何变量/约束。
    属性名沿用原 SolutionData（four_items/values/costs/units/inputs_fields/slacks），
    plot/analysis_export/run/service 按属性访问不变。
    """

    def __init__(self, m: gp.Model, inp, insts: list, sv, cost_exprs: dict):
        """从求解模型一次性取回结果；本方法只做取值，不做业务口径推导。

        Args:
            m (gp.Model): 已 optimize 的 Gurobi 模型。
            inp (Inputs): 系统公共输入。
            insts (list): UNITS 序单元实例（inst.inputs 输入、inst.vars 变量句柄）。
            sv (SystemVars): 系统层决策变量容器。
            cost_exprs (dict): add_objective 返回的成本表达式。

        Returns:
            None
        """
        self.inp = inp                   # to_timeseries_csv 用；下游勿再改
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

        # 建模辅助解不进取值集：lambda_th/segment_th、V/W_*_count、P_shift_pair。
        # u_ps_area_gen/pump 按第四章抽蓄输出表取值（2026-10-16 裁决：遵第四章）。
        thermal, gas, nuc, bio, ps, es, hyd, fr, cspv = (i.vars for i in insts)
        values = {
            "P_th": _values(thermal.P_th), "u_th": _integers(thermal.u_th),
            # 新版 v/w 为 [0,1] 连续（负成本才二值），取原值；启停次数 §9 由 u 差分计
            "v_th": _values(thermal.v_th), "w_th": _values(thermal.w_th),
            "R_th_hot": _values(thermal.R_th_hot), "R_th_cold": _values(thermal.R_th_cold),
            "F_th": _values(thermal.F_th), "CO2_th": _values(thermal.CO2_th),
            "P_gas": _values(gas.P_gas), "u_gas": _integers(gas.u_gas),
            "v_gas": _values(gas.v_gas), "w_gas": _values(gas.w_gas),
            "R_gas_hot": _values(gas.R_gas_hot), "R_gas_cold": _values(gas.R_gas_cold),
            "P_nuc": _values(nuc.P_nuc), "P_bio": _values(bio.P_bio),
            "P_ps_gen": _values(ps.P_ps_gen), "P_ps_pump": _values(ps.P_ps_pump),
            "u_ps_gen": _integers(ps.u_ps_gen), "u_ps_pump": _integers(ps.u_ps_pump),
            "R_ps_hot": _values(ps.R_ps_hot), "E_ps": _values(ps.E_ps),
            "P_es_charge": _values(es.P_es_charge),
            "P_es_discharge": _values(es.P_es_discharge),
            # 新版 938：u_es_dis 为表达式 1-u_es_ch，按定义式导出
            "u_es_ch": _integers(es.u_es_ch), "u_es_dis": 1 - _integers(es.u_es_ch),
            "R_es_hot": _values(es.R_es_hot), "E_es": _values(es.E_es),
            "P_hyd_ror": _values(hyd.P_hyd_ror), "P_hyd_res": _values(hyd.P_hyd_res),
            "R_hyd_hot": _values(hyd.R_hyd_hot), "E_hyd_curt": _values(hyd.E_hyd_curt),
            "P_fr_wind_out": _values(fr.P_fr_wind_out),
            "P_fr_wind_ch": _values(fr.P_fr_wind_ch),
            "P_fr_wind_curt": _values(fr.P_fr_wind_curt),
            "P_fr_pv_out": _values(fr.P_fr_pv_out),
            "P_fr_pv_ch": _values(fr.P_fr_pv_ch),
            "P_fr_pv_curt": _values(fr.P_fr_pv_curt),
            "P_fr_charge": _values(fr.P_fr_charge),
            "P_fr_discharge": _values(fr.P_fr_discharge),
            # 新版 744：u_fr_dis 为表达式 1-u_fr_ch
            "u_fr_ch": _integers(fr.u_fr_ch), "u_fr_dis": 1 - _integers(fr.u_fr_ch),
            "E_fr": _values(fr.E_fr),
            # 光热（MATLAB 2.11，2026-10-16 接入；海南 N_csp=0 → 各数组 0 行）
            "P_csp_unit": _values(cspv.P_csp_unit),
            "u_csp": _integers(cspv.u_csp), "v_csp": _integers(cspv.v_csp),
            "w_csp": _integers(cspv.w_csp),
            "Q_csp_in": _values(cspv.Q_csp_in), "Q_csp_out": _values(cspv.Q_csp_out),
            "Q_csp_curt": _values(cspv.Q_csp_curt), "Q_csp_pg": _values(cspv.Q_csp_pg),
            # 新版 760：u_csp_out 为表达式 1-u_csp_in
            "u_csp_in": _integers(cspv.u_csp_in), "u_csp_out": 1 - _integers(cspv.u_csp_in),
            "E_csp": _values(cspv.E_csp),
            # 新版 961：分区方向变量仅多台抽蓄分区才建（海南单台 → None）
            "u_ps_area_gen": None if sv.u_ps_area_gen is None else _integers(sv.u_ps_area_gen),
            "u_ps_area_pump": None if sv.u_ps_area_pump is None else _integers(sv.u_ps_area_pump),
            # 新版 3.7：风电拆陆上/海上；P_wind/curt 为表达式，按定义式求和取值
            "P_wind_on": _values(sv.P_wind_on), "P_wind_off": _values(sv.P_wind_off),
            "P_wind_on_curt": _values(sv.P_wind_on_curt),
            "P_wind_off_curt": _values(sv.P_wind_off_curt),
            "P_wind": _values(sv.P_wind_on) + _values(sv.P_wind_off),
            "P_pv": _values(sv.P_pv),
            "P_wind_curt": _values(sv.P_wind_on_curt) + _values(sv.P_wind_off_curt),
            "P_pv_curt": _values(sv.P_pv_curt), "P_shed": _values(sv.P_shed),
            "P_line": _values(sv.P_line), "P_shift_out": _values(sv.P_shift_out),
            "P_shift_in": _values(sv.P_shift_in), "P_cut": _values(sv.P_cut),
        }

        coal, gg, nn, bb, pp, ee, hh, ff, cc = (i.inputs for i in insts)
        units = {
            "thermal": {"keys": coal.UnitKey_th, "names": coal.name_th,
                        "rated": coal.P_th_rated,
                        "minimum": coal.P_th_min, "maintenance": coal.m_th,
                        "area": coal.area_th,
                        "initial_status": coal.u_th_0},   # 新版 §9：启停次数由 u 差分（含初态）计
            "gas": {"keys": gg.UnitKey_gas, "names": gg.name_gas,
                    "rated": gg.P_gas_rated,
                    "minimum": gg.P_gas_min, "maintenance": gg.m_gas,
                    "area": gg.area_gas,
                    "initial_status": gg.u_gas_0},
            "nuclear": {"keys": nn.UnitKey_nuc, "rated": nn.P_nuc_max,
                        "maintenance": nn.m_nuc, "area": nn.area_nuc},
            "biomass": {"keys": bb.UnitKey_bio, "rated": bb.P_bio_rated,
                        "minimum": bb.P_bio_min, "maintenance": bb.m_bio,
                        "area": bb.area_bio},
            "pumped_storage": {"keys": pp.UnitKey_ps, "names": pp.name_ps,
                               "area": pp.area_ps,
                               "energy_capacity": pp.E_ps_max,
                               "soc_min": pp.SOC_ps_min, "soc_max": pp.SOC_ps_max,
                               "soc_initial": pp.SOC_ps_initial,
                               "maintenance": pp.m_ps},
            "electrochemical": {"keys": ee.UnitKey_es, "names": ee.name_es,
                                "area": ee.area_es,
                                "energy_capacity": ee.E_es_max,
                                "energy_min": ee.E_es_min,
                                "energy_max": ee.E_es_upper,
                                "energy_initial": ee.E_es_initial,
                                "soc_min": ee.E_es_min / ee.E_es_max,   # =表列 SOC_MIN
                                "soc_max": ee.E_es_upper / ee.E_es_max},  # =表列 SOC_MAX
            "hydro_ror": {"keys": hh.UnitKey_ror, "area": hh.area_hyd_ror,
                          "capacity": hh.CAP_ror},
            "hydro_reservoir": {"keys": hh.UnitKey_res, "area": hh.area_hyd_res,
                                "capacity": hh.CAP_res,
                                "period_unit": hh.hydPeriodUnit,
                                "period_hours": hh.hydPeriodHours},
            "friendly": {"keys": ff.UnitKey_fr, "names": ff.name_fr,
                         "area": ff.area_fr,
                         "energy_capacity": ff.E_fr_max,
                         "energy_min": ff.E_fr_min,
                         "energy_max": ff.E_fr_upper,
                         "energy_initial": ff.E_fr_initial,
                         "soc_min": ff.E_fr_min / ff.E_fr_max,   # =表列 SOC_MIN
                         "soc_max": ff.E_fr_upper / ff.E_fr_max,  # =表列 SOC_MAX
                         "wind_available": ff.P_fr_wind_available,
                         "pv_available": ff.P_fr_pv_available},
            # 光热（2026-10-16 接入；海南 N_csp=0 → keys 空）
            "csp": {"keys": cc.UnitKey_csp, "area": cc.area_csp,
                    "capacity": cc.P_csp_max,
                    "heat_min": cc.HEAT_MIN, "heat_max": cc.HEAT_MAX,
                    "heat_initial": cc.HEAT_INITIAL},
        }

        inputs_fields = {
            "T": inp.time.T, "Delta_t": inp.time.Delta_t,
            "AreaID": inp.areas.AreaID, "AreaName": inp.areas.AreaName,
            "Narea": inp.areas.Narea, "Nprovince": inp.areas.Nprovince,
            "areaProvince": inp.areas.areaProvince, "P_load": inp.profiles.P_load,
            "P_wind_available": inp.profiles.P_wind_available,
            "P_pv_available": inp.profiles.P_pv_available,
            "N_line": inp.lines.N_line, "line_from": inp.lines.line_from,
            "line_to": inp.lines.line_to, "P_week_peak": inp.week_peak,
            "hotReserveRatio": inp.prov.hotReserveRatio,
            "coldReserveRatio": inp.prov.coldReserveRatio,
            "CO2_gas_factor": gg.CO2_gas_factor,
            "P_hyd_ror_min": hh.P_hyd_ror_min,
            "P_hyd_res_min": hh.P_hyd_res_min,
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

        self.four_items = four_items
        self.values = values
        self.costs = costs
        self.units = units
        self.inputs_fields = inputs_fields
        self.slacks = slacks

    # ---- 第四章派生输出（文档列为输出、模型无独立变量的，按定义式给出）----

    @property
    def friendly_output(self) -> np.ndarray:
        """友好站对外出力（第四章：风直送+光直送+放电，逐站×T）。

        Returns:
            np.ndarray: 逐站×T 对外出力，MW。
        """
        V = self.values
        return (V["P_fr_wind_out"] + V["P_fr_pv_out"] + V["P_fr_discharge"])

    # ---- §12.1 逐小时输出数据（2026-10-20 用户裁决：数据住 Solution，
    #      Excel 写盘/前端 JSON 均为本属性消费者，构建逻辑自 excel_export 移入）----

    @cached_property
    def sheets(self) -> dict:
        """§12.1 全部输出页：sheet 名 → DataFrame（12 页，插入序即页序）。
        index=日期（说明页除外），columns=中文列名（单行表头，口径行不入表），
        values 为数值列。Excel 写盘（excel_export.write_excel）与前端 JSON
        （to_dict orient="split"）同源取数，数据区与 MATLAB summarize
        终态逐格一致。

        Returns:
            dict: sheet 名 → pd.DataFrame。
        """
        inp = self.inp
        V = self.values
        U = self.units
        T = inp.time.T
        dates = [t.strftime("%Y-%m-%d %H:%M") for t in inp.time.Time]
        Narea = inp.areas.Narea
        area = lambda name: U[name]["area"]
        Agg = lambda name: _agg(Narea, area(name))

        # ---- 区域级系统量（MATLAB 1094-1097/1067、426：开关关闭 → 0 矩阵）----
        fr_net = V["P_fr_wind_out"] + V["P_fr_pv_out"] + V["P_fr_discharge"]
        P_G = (Agg("thermal") @ V["P_th"] + Agg("gas") @ V["P_gas"]
               + Agg("nuclear") @ V["P_nuc"] + Agg("biomass") @ V["P_bio"]
               + Agg("hydro_ror") @ V["P_hyd_ror"] + Agg("hydro_reservoir") @ V["P_hyd_res"]
               + V["P_wind"] + V["P_pv"] + Agg("csp") @ V["P_csp_unit"]
               + Agg("friendly") @ fr_net)
        P_sto = (V["P_es_discharge"] - V["P_es_charge"]
                 + Agg("pumped_storage") @ (V["P_ps_gen"] - V["P_ps_pump"]))
        P_D = inp.profiles.P_load - V["P_shift_out"] + V["P_shift_in"] - V["P_cut"]
        A_line = np.zeros((Narea, inp.lines.N_line))
        A_line[inp.lines.line_from, np.arange(inp.lines.N_line)] = -1
        A_line[inp.lines.line_to, np.arange(inp.lines.N_line)] = 1
        P_tr = A_line @ V["P_line"]
        P_inter = np.zeros((Narea, T))      # MATLAB 67 useInterprovincial=false
        P_trans = np.zeros((Narea, T))      # MATLAB 68 useFixedTransfer=false
        residual = P_G + P_sto + P_tr + P_inter + P_trans + V["P_shed"] - P_D
        A_prov = np.zeros((inp.areas.Nprovince, Narea))
        A_prov[inp.areas.areaProvince, np.arange(Narea)] = 1
        R_hot = A_prov @ (Agg("thermal") @ V["R_th_hot"] + Agg("gas") @ V["R_gas_hot"]
                          + Agg("hydro_reservoir") @ V["R_hyd_hot"]
                          + Agg("pumped_storage") @ V["R_ps_hot"]
                          + Agg("electrochemical") @ V["R_es_hot"])
        R_cold = A_prov @ (Agg("thermal") @ V["R_th_cold"] + Agg("gas") @ V["R_gas_cold"])

        area_id = [str(a) for a in inp.areas.AreaID]
        prov_id = ["省%d" % (i + 1) for i in range(inp.areas.Nprovince)]
        af = np.repeat((inp.prov.adequacyFactor - 1)[:, None], T, axis=1)
        hr = np.repeat(inp.prov.hotReserveRatio[:, None], T, axis=1)
        cr = np.repeat(inp.prov.coldReserveRatio[:, None], T, axis=1)

        device_items = [
            ("煤电", "P_th", V["P_th"], U["thermal"]["keys"], "MW"),
            ("气电", "P_gas", V["P_gas"], U["gas"]["keys"], "MW"),
            ("生物质", "P_bio", V["P_bio"], U["biomass"]["keys"], "MW"),
            ("核电", "P_nuc", V["P_nuc"], U["nuclear"]["keys"], "MW"),
            ("径流式水电", "P_hyd_ror", V["P_hyd_ror"], U["hydro_ror"]["keys"], "MW"),
            ("库容式水电", "P_hyd_res", V["P_hyd_res"], U["hydro_reservoir"]["keys"], "MW"),
            ("陆风", "P_wind_on", V["P_wind_on"], area_id, "MW"),
            ("海风", "P_wind_off", V["P_wind_off"], area_id, "MW"),
            ("光伏", "P_pv", V["P_pv"], area_id, "MW"),
            ("光热", "P_csp_unit", V["P_csp_unit"], U["csp"]["keys"], "MW"),
            ("友好站风电送出", "P_fr_wind_out", V["P_fr_wind_out"], U["friendly"]["keys"], "MW"),
            ("友好站光伏送出", "P_fr_pv_out", V["P_fr_pv_out"], U["friendly"]["keys"], "MW"),
            ("友好站总送出", "P_fr_unit", self.friendly_output, U["friendly"]["keys"], "MW"),
            ("陆风弃电", "P_wind_on_curt", V["P_wind_on_curt"], area_id, "MW"),
            ("海风弃电", "P_wind_off_curt", V["P_wind_off_curt"], area_id, "MW"),
            ("光伏弃电", "P_pv_curt", V["P_pv_curt"], area_id, "MW"),
            ("友好站风电充电", "P_fr_wind_ch", V["P_fr_wind_ch"], U["friendly"]["keys"], "MW"),
            ("友好站光伏充电", "P_fr_pv_ch", V["P_fr_pv_ch"], U["friendly"]["keys"], "MW"),
            ("友好站弃风", "P_fr_wind_curt", V["P_fr_wind_curt"], U["friendly"]["keys"], "MW"),
            ("友好站弃光", "P_fr_pv_curt", V["P_fr_pv_curt"], U["friendly"]["keys"], "MW"),
        ]
        load_items = [
            ("本地负荷", "P_load", inp.profiles.P_load, area_id, "MW"),
            ("转出负荷", "P_shift_out", V["P_shift_out"], area_id, "MW"),
            ("转入负荷", "P_shift_in", V["P_shift_in"], area_id, "MW"),
            ("削减负荷", "P_cut", V["P_cut"], area_id, "MW"),
            ("响应后负荷", "P_D", P_D, area_id, "MW"),
            ("失负荷", "P_shed", V["P_shed"], area_id, "MW"),
            ("省内净受入", "P_tr", P_tr, area_id, "MW"),
            ("省间净受入", "P_inter", P_inter, area_id, "MW"),
            ("既定跨区净受入", "P_trans", P_trans, area_id, "MW"),
            ("发电汇总", "P_G", P_G, area_id, "MW"),
            ("储能净出力", "P_sto", P_sto, area_id, "MW"),
            ("功率平衡残差", "BalanceResidual", residual, area_id, "MW"),
        ]
        reserve_items = [
            ("煤电热备用", "R_th_hot", V["R_th_hot"], U["thermal"]["keys"], "MW"),
            ("煤电冷备用", "R_th_cold", V["R_th_cold"], U["thermal"]["keys"], "MW"),
            ("气电热备用", "R_gas_hot", V["R_gas_hot"], U["gas"]["keys"], "MW"),
            ("气电冷备用", "R_gas_cold", V["R_gas_cold"], U["gas"]["keys"], "MW"),
            ("水电热备用", "R_hyd_hot", V["R_hyd_hot"], U["hydro_reservoir"]["keys"], "MW"),
            ("抽蓄热备用", "R_ps_hot", V["R_ps_hot"], U["pumped_storage"]["keys"], "MW"),
            ("独立储能热备用", "R_es_hot", V["R_es_hot"], U["electrochemical"]["keys"], "MW"),
            ("省级热备用", "R_hot_province", R_hot, prov_id, "MW"),
            ("省级冷备用", "R_cold_province", R_cold, prov_id, "MW"),
            ("周峰荷", "P_week_peak", inp.week_peak, prov_id, "MW"),
            ("总备用下限", "(adequacyFactor-1).*P_week_peak", af * inp.week_peak, prov_id, "MW"),
            ("热备用下限", "hotReserveRatio.*P_week_peak", hr * inp.week_peak, prov_id, "MW"),
            ("冷备用下限", "coldReserveRatio.*P_week_peak", cr * inp.week_peak, prov_id, "MW"),
        ]
        state_unit = "状态或台数"
        thermal_items = [
            ("煤电运行", "u_th", V["u_th"], U["thermal"]["keys"], state_unit),
            ("煤电启动", "v_th", V["v_th"], U["thermal"]["keys"], state_unit),
            ("煤电停机", "w_th", V["w_th"], U["thermal"]["keys"], state_unit),
            ("煤电检修", "m_th", U["thermal"]["maintenance"], U["thermal"]["keys"], state_unit),
            ("气电运行", "u_gas", V["u_gas"], U["gas"]["keys"], state_unit),
            ("气电启动", "v_gas", V["v_gas"], U["gas"]["keys"], state_unit),
            ("气电停机", "w_gas", V["w_gas"], U["gas"]["keys"], state_unit),
            ("气电检修", "m_gas", U["gas"]["maintenance"], U["gas"]["keys"], state_unit),
        ]
        storage_spec = [
            ("抽水蓄能", "pumped_storage", "P_ps_pump", "P_ps_gen", "E_ps"),
            ("独立电化学储能", "electrochemical", "P_es_charge", "P_es_discharge", "E_es"),
            ("友好电站储能", "friendly", "P_fr_charge", "P_fr_discharge", "E_fr"),
        ]

        # ---- 储能功率/能量SOC×3（2015-2033：功率 T 行、SOC 保留 T+1 边界点）----
        boundary_dates = dates + [(inp.time.Time[-1]
                                   + timedelta(hours=inp.time.Delta_t)
                                   ).strftime("%Y-%m-%d %H:%M")]
        charge_sum = {}
        discharge_sum = {}
        power_frames = {}
        energy_frames = {}
        for label, group, ch_key, dis_key, e_key in storage_spec:
            keys = [str(k) for k in U[group]["keys"]]
            rated = np.asarray(U[group]["energy_capacity"], dtype=float)
            E, ch, dis = V[e_key], V[ch_key], V[dis_key]
            charge_sum[label] = ch.sum(axis=0)
            discharge_sum[label] = dis.sum(axis=0)
            power_cols = [("充电或抽水｜汇总", "吸收功率为正 / MW", ch.sum(axis=0).tolist()),
                          ("放电或发电｜汇总", "输出功率为正 / MW", dis.sum(axis=0).tolist())]
            energy_cols = [("储存能量｜汇总", "MWh", E.sum(axis=0).tolist())]
            for i, k in enumerate(keys):
                power_cols.append((f"{k}｜充电或抽水", "MW", ch[i].tolist()))
                power_cols.append((f"{k}｜放电或发电", "MW", dis[i].tolist()))
                energy_cols.append((f"{k}｜能量", "MWh", E[i].tolist()))
                energy_cols.append((f"{k}｜SOC",
                                    "能量/额定能量，0至1；抽蓄为等效能量比例",
                                    (E[i] / rated[i]).tolist()))
            power_frames[label] = _page_frame(dates, power_cols)
            energy_frames[label] = _page_frame(boundary_dates, energy_cols)

        # ---- 逐小时结果首页 + 变量对应说明（20261021 summarize 终态口径：
        #      水电分径流式/库容式两列且始终列出（无装机列 0 并注明）；风电=陆风+
        #      海风、光伏=普通光伏，均不含友好站；友好一体化电站=站总送出独立
        #      列报；储能只列净出力两列（抽蓄/独立电化学，不含友好站储能））----
        dev_matrix = {label: np.asarray(ev, dtype=float)
                      for label, _, ev, _, _ in device_items}
        names = ["煤电", "气电", "生物质", "核电", "径流式水电", "库容式水电",
                 "风电", "光伏", "光热", "友好一体化电站"]
        parts = [["煤电"], ["气电"], ["生物质"], ["核电"], ["径流式水电"],
                 ["库容式水电"], ["陆风", "海风"], ["光伏"], ["光热"],
                 ["友好站总送出"]]
        always_listed = ["径流式水电", "库容式水电"]   # 两类水电始终分列，无装机列 0
        summary_cols = []                      # (名称, 数据 list)
        notes_rows = [["汇总指标", "来源及口径", "单位"]]
        for k, name in enumerate(names):
            total = np.zeros(T)
            present = False
            for label in parts[k]:
                present = present or dev_matrix[label].shape[0] > 0
                total = total + dev_matrix[label].sum(axis=0)
            if present or name in always_listed:
                summary_cols.append((name, total.tolist()))
                source = "+".join(parts[k])
                if not present:
                    source += "（本算例无该类装机，全为0）"
                notes_rows.append([name, source, "MW"])
        # 20261021：储能首页只列净出力（发电/放电−抽水/充电），充放电在明细页
        storage_labels = ["抽水蓄能净出力", "独立电化学储能净出力"]
        storage_scope = ["抽水蓄能", "独立电化学储能，不含友好站储能"]
        for k, label in enumerate(storage_labels):
            key = "抽水蓄能" if k == 0 else "独立电化学储能"
            net = discharge_sum[key] - charge_sum[key]
            summary_cols.append((label, net.tolist()))
            notes_rows.append([label, storage_scope[k]
                               + "，发电/放电减抽水/充电，正值供电、负值吸收", "MW"])
        load_matrix = {label: np.asarray(ev, dtype=float)
                       for label, _, ev, _, _ in load_items}
        reserve_matrix = {label: np.asarray(ev, dtype=float)
                          for label, _, ev, _, _ in reserve_items}
        item_note = lambda label, var, unit: f"{var} 按行求和 / {unit}"
        for matrix, notes, labels in (
                (load_matrix, {l: item_note(l, v, u) for l, v, _, _, u in load_items},
                 ["本地负荷", "转出负荷", "转入负荷", "削减负荷", "响应后负荷", "失负荷"]),
                (reserve_matrix, {l: item_note(l, v, u) for l, v, _, _, u in reserve_items},
                 ["省级热备用", "省级冷备用", "总备用下限", "热备用下限", "冷备用下限"])):
            for label in labels:
                summary_cols.append((label, matrix[label].sum(axis=0).tolist()))
                notes_rows.append([label, notes[label], "MW"])
        notes_rows.append(["汇总口径",
                           "友好一体化电站按对外总送出独立列报；普通风电、光伏和独立储能"
                           "不含站内设备。首页储能只列净出力，详细充放电保留于明细页。", ""])
        summary_frame = pd.DataFrame(
            {name: data for name, data in summary_cols},
            index=pd.Index(dates, name="日期"),
            columns=[name for name, _ in summary_cols])
        notes_frame = pd.DataFrame(notes_rows, columns=["汇总指标", "来源及口径", "单位"])

        sheets = {
            "逐小时结果": summary_frame,
            "变量对应说明": notes_frame,
            "设备出力": _detail_frame(dates, device_items),
            "负荷与交换": _detail_frame(dates, load_items),
            "备用": _detail_frame(dates, reserve_items),
            "火电状态": _detail_frame(dates, thermal_items),
        }
        for label, _, _, _, _ in storage_spec:
            sheets[label + "功率"] = power_frames[label]
            sheets[label + "能量SOC"] = energy_frames[label]
        return sheets

    # ---- 对拍件（compare.py 硬依赖；列集=第四章输出展开，列序冻结勿动）----

    def to_timeseries_csv(self) -> None:
        """导出对拍用时序 CSV（RESULT_DIR/时序结果_T{T}.csv；列集与列序冻结）。

        Returns:
            None
        """
        values = self.values
        units = self.units
        inp = self.inp
        cols = {"Time": [timestamp.strftime("%Y-%m-%d %H:%M")
                         for timestamp in inp.time.Time]}

        def area_columns(prefix, values_array):
            for index, area_id in enumerate(inp.areas.AreaID):
                cols[f"{prefix}_{area_id}"] = np.asarray(values_array)[index]

        def unit_columns(prefix, unit_name, values_array, skip=0):
            for key, row in zip(units[unit_name]["keys"], np.asarray(values_array)):
                cols[f"{prefix}_{key}"] = np.asarray(row)[skip:]

        area_columns("P_load", inp.profiles.P_load)
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
            month = inp.time.Time[period_hours[period_index][0]].strftime("%Y%m")
            cols[f"E_hyd_curt_{key}_{month}"] = np.full(
                inp.time.T, hydro_curt[period_index, 0])

        for prefix, key in (
                ("P_fr_wind_out", "P_fr_wind_out"),
                ("P_fr_pv_out", "P_fr_pv_out"),
                ("P_fr_charge", "P_fr_charge"),
                ("P_fr_discharge", "P_fr_discharge")):
            unit_columns(prefix, "friendly", values[key])
        unit_columns("E_fr", "friendly", values["E_fr"], skip=1)

        import config
        path = config.RESULT_DIR / f"时序结果_T{inp.time.T}.csv"
        import pandas as pd
        pd.DataFrame(cols).to_csv(path, index=False, encoding="utf-8")
        print("已导出: %s" % path)


def write_result_json(inp, solution: Solution, metrics: dict) -> None:
    """
    结果.json。
    只含 meta + four_items + costs + metrics 标量；前端 report/positionChart/
    series/labels 已移 service 组装（series/labels 舍弃）。compare.py 的 Python 侧
    objective 自 four_items 读、参考指标自 metrics 读（2026-10-16 裁决）。

    Args:
        inp (Inputs): 系统公共输入（meta 段用）。
        solution (Solution): 求解结果。
        metrics (dict): AnalysisExport.metrics() 的指标字典。

    Returns:
        None
    """
    import config
    doc = {
        "meta": {
            "T": inp.time.T, "delta_t_hours": inp.time.Delta_t,
            "startTime": str(inp.time.Time[0]), "endTime": str(inp.time.Time[-1]),
            "scenario": config.HYDROLOGY_SCENARIO,
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "four_items": solution.four_items,      # 含 scenario 无关的求解四项
        "costs": solution.costs,                # 成本分项（六表取值用；恒等式见 test_algorithm）
        "metrics": {                            # AnalysisExport.metrics 的标量（report 同名）
            "balanceErrorMW": metrics["balanceErrorMW"],
            "renewableUtilization": [float(x) for x in metrics["renewableUtilization"]],
            "curtailedEnergyMWh": [float(x) for x in metrics["curtailedEnergy"]],
            "energyNotServedMWh": metrics["energyNotServed"],
            "lossOfLoadHours": metrics["lossOfLoadHours"],
            "maxLoadShortfallMW": metrics["maxLoadShortfall"],
            "hotReserveMarginMW": metrics["hotMargin"],
            "coldReserveMarginMW": metrics["coldMargin"],
            "CO2_t": metrics["CO2_t"],
            "maxStorageClosureErrorMWh": metrics["storageClosure"],
        },
    }
    path = config.RESULT_DIR / "结果.json"
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(doc, stream, ensure_ascii=False, separators=(",", ":"))
    print("已导出: %s" % path)
