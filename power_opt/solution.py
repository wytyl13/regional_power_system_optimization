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
from datetime import datetime

import gurobipy as gp
import numpy as np


def _values(mvar: gp.MVar) -> np.ndarray:
    return np.asarray(mvar.X)


def _integers(mvar: gp.MVar) -> np.ndarray:
    return np.rint(_values(mvar)).astype(int)


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
            "v_th": _integers(thermal.v_th), "w_th": _integers(thermal.w_th),
            "R_th_hot": _values(thermal.R_th_hot), "R_th_cold": _values(thermal.R_th_cold),
            "F_th": _values(thermal.F_th), "CO2_th": _values(thermal.CO2_th),
            "P_gas": _values(gas.P_gas), "u_gas": _integers(gas.u_gas),
            "v_gas": _integers(gas.v_gas), "w_gas": _integers(gas.w_gas),
            "R_gas_hot": _values(gas.R_gas_hot), "R_gas_cold": _values(gas.R_gas_cold),
            "P_nuc": _values(nuc.P_nuc), "P_bio": _values(bio.P_bio),
            "P_ps_gen": _values(ps.P_ps_gen), "P_ps_pump": _values(ps.P_ps_pump),
            "u_ps_gen": _integers(ps.u_ps_gen), "u_ps_pump": _integers(ps.u_ps_pump),
            "R_ps_hot": _values(ps.R_ps_hot), "E_ps": _values(ps.E_ps),
            "P_es_charge": _values(es.P_es_charge),
            "P_es_discharge": _values(es.P_es_discharge),
            "u_es_ch": _integers(es.u_es_ch), "u_es_dis": _integers(es.u_es_dis),
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
            "u_fr_ch": _integers(fr.u_fr_ch), "u_fr_dis": _integers(fr.u_fr_dis),
            "E_fr": _values(fr.E_fr),
            # 光热（MATLAB 2.11，2026-10-16 接入；海南 N_csp=0 → 各数组 0 行）
            "P_csp_unit": _values(cspv.P_csp_unit),
            "u_csp": _integers(cspv.u_csp), "v_csp": _integers(cspv.v_csp),
            "w_csp": _integers(cspv.w_csp),
            "Q_csp_in": _values(cspv.Q_csp_in), "Q_csp_out": _values(cspv.Q_csp_out),
            "Q_csp_curt": _values(cspv.Q_csp_curt), "Q_csp_pg": _values(cspv.Q_csp_pg),
            "u_csp_in": _integers(cspv.u_csp_in), "u_csp_out": _integers(cspv.u_csp_out),
            "E_csp": _values(cspv.E_csp),
            "u_ps_area_gen": _integers(sv.u_ps_area_gen),
            "u_ps_area_pump": _integers(sv.u_ps_area_pump),
            "P_wind": _values(sv.P_wind), "P_pv": _values(sv.P_pv),
            "P_wind_curt": _values(sv.P_wind_curt),
            "P_pv_curt": _values(sv.P_pv_curt), "P_shed": _values(sv.P_shed),
            "P_line": _values(sv.P_line), "P_shift_out": _values(sv.P_shift_out),
            "P_shift_in": _values(sv.P_shift_in), "P_cut": _values(sv.P_cut),
        }

        coal, gg, nn, bb, pp, ee, hh, ff, cc = (i.inputs for i in insts)
        units = {
            "thermal": {"keys": coal.UnitKey_th, "names": coal.name_th,
                        "rated": coal.P_th_rated,
                        "minimum": coal.P_th_min, "maintenance": coal.m_th,
                        "area": coal.area_th},
            "gas": {"keys": gg.UnitKey_gas, "names": gg.name_gas,
                    "rated": gg.P_gas_rated,
                    "minimum": gg.P_gas_min, "maintenance": gg.m_gas,
                    "area": gg.area_gas},
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
    def nuclear_available(self) -> np.ndarray:
        """核电可运行状态（第四章：1 − 检修标记 m_nuc，逐台×T）。"""
        return 1 - np.asarray(self.units["nuclear"]["maintenance"]).astype(int)

    @property
    def friendly_output(self) -> np.ndarray:
        """友好站对外出力（第四章：风直送+光直送+放电，逐站×T）。"""
        V = self.values
        return (V["P_fr_wind_out"] + V["P_fr_pv_out"] + V["P_fr_discharge"])

    @property
    def area_line_flow(self) -> np.ndarray:
        """断面分区总送受（第四章：A_line @ P_line，受入正/送出负，分区×T）。"""
        F = self.inputs_fields
        A_line = np.zeros((F["Narea"], F["N_line"]))
        A_line[F["line_from"], np.arange(F["N_line"])] = -1
        A_line[F["line_to"], np.arange(F["N_line"])] = 1
        return A_line @ self.values["P_line"]

    @property
    def storage_equivalent_cycles(self) -> dict:
        """储能放电侧等效循环（第四章：放电量/额定能量，逐台，按储能三类分组）。"""
        dt = self.inputs_fields["Delta_t"]
        out = {}
        for group, dis_key in (("pumped_storage", "P_ps_gen"),
                               ("electrochemical", "P_es_discharge"),
                               ("friendly", "P_fr_discharge")):
            rated = np.asarray(self.units[group]["energy_capacity"])
            if len(rated):
                out[group] = (self.values[dis_key].sum(axis=1) * dt / rated).tolist()
        return out

    # ---- 对拍件（compare.py 硬依赖；列集=第四章输出展开，列序冻结勿动）----

    def to_timeseries_csv(self) -> None:
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
        "costs": solution.costs,                # 成本分项（六表取值用；恒等式见 test_algrithm）
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
