#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/15 17:20
@Author  : weiyutao
@File    : solve.py
组装全模型 + 目标函数（MATLAB 4.1-4.14，1016-1094 行）+ 调 Gurobi。
唯一直接碰求解器的文件；四项核对（变量/约束/状态/目标值）打印进 stdout。
海南 v2：FOM 基数改额定容量（4.1/4.2/4.3）、新增 4.9 友好电站成本、
求解参数镜像 1164-1168、新指标镜像 1177-1190。
八电源已全部类化：UNITS 列表 = 建序合同（变量/约束/成本三处共用，冻结）。
2026-10-16 光热（csp）尾插接入：对照 MATLAB 2.11/3.11/4.11/981，
海南 N_csp=0 → 变量/约束/成本增量全 0，T24 对拍应逐位不变。
"""

from __future__ import annotations

import numpy as np
import gurobipy as gp
from datetime import datetime

import config
import power_opt.reader as reader
from solution import Solution, write_result_json
from model.units import thermal as TH
from model.units import gas as GA
from model.units import nuclear as NU
from model.units import biomass as BI
from model.units import pumped_storage as PS
from model.units import electrochemical as ES
from model.units import hydro as HY
from model.units import friendly as FR
from model.units import csp as CS
from model.units.base import _wsum
from model import context as CT

# 电源建序合同（冻结）：add_vars / add_constraints / add_cost 三处共用此顺序
UNITS = [TH.Thermal, GA.Gas, NU.Nuclear, BI.Biomass,
         PS.PumpedStorage, ES.Electrochemical, HY.Hydro, FR.Friendly]
# 2026-10-16 光热尾插（对照 MATLAB 2.11/4.11 物理位置=全部电源之后），海南 N_csp=0
UNITS = UNITS + [CS.Csp]


def build(m: gp.Model, inp: reader.Inputs):
    """变量 + 约束全组装（八电源 UNITS 建序 → 系统层），返回句柄。

    Args:
        m (gp.Model): Gurobi 模型。
        inp (Inputs): 系统公共输入。

    Returns:
        tuple: (insts, V, Sv) —— 单元实例列表、各单元 Vars 列表（均按 UNITS 序）、
        系统层决策变量容器。
    """
    insts = []
    for cls in UNITS:                    # load 无依赖，顺序即建序
        inst = cls()
        inst.load(inp)
        insts.append(inst)
    V = [inst.add_vars(m) for inst in insts]
    Sv = CT.add_vars(m, inp)
    reg = CT.RegisteredUnits(thermal=V[0], gas=V[1], nuclear=V[2], biomass=V[3],
                             pumped_storage=V[4], electrochemical=V[5],
                             hydro=V[6], friendly=V[7], csp=V[8])
    for inst in insts:                   # 抽蓄借 sv.u_ps_area；其余单元忽略 sv
        inst.add_constraints(m, inp, Sv)
    CT.add_constraints(m, inp, Sv, reg, *[i.inputs for i in insts])
    return insts, V, Sv


def add_objective(m: gp.Model, inp: reader.Inputs, insts: list, Sv: CT.SystemVars) -> dict:
    """MATLAB 4.1-4.14（1016-1094）一比一对照；返回成本表达式 dict 供取值
    （六大项/十项分解对齐 MATLAB 1290-1312 行；海南无光热/互济，C_csp/C_inter=0）。

    Args:
        m (gp.Model): Gurobi 模型。
        inp (Inputs): 系统公共输入。
        insts (list): UNITS 序单元实例列表（build 产出）。
        Sv (SystemVars): 系统层决策变量。

    Returns:
        dict: 成本表达式与分项（键名是对外合同，冻结）。
    """
    T, Delta_t = inp.time.T, inp.time.Delta_t

    # ---- 4.1-4.10 各单元成本（键名合同冻结）：insts 序 = 节号序 4.1→4.2→4.10→4.3→4.5→4.6→4.4→4.9 ----
    costs = {}
    for inst in insts:
        costs.update(inst.add_cost(Delta_t, inp.econ))
    C_th = costs["C_th"]; C_gas = costs["C_gas"]; C_nuc = costs["C_nuc"]
    C_bio = costs["C_bio"]; C_ps = costs["C_ps"]; C_es = costs["C_es"]
    C_hyd = costs["C_hyd"]; C_fr = costs["C_fr"]
    C_th_fuel = costs["C_th_fuel"]; C_th_start = costs["C_th_start"]
    C_th_stop = costs["C_th_stop"]; C_th_FOM = costs["C_th_FOM"]
    C_th_CO2 = costs["C_th_CO2"]; C_th_SO2 = costs["C_th_SO2"]
    C_th_NOX = costs["C_th_NOX"]; C_th_dust = costs["C_th_dust"]
    C_gas_gen = costs["C_gas_gen"]; C_gas_start = costs["C_gas_start"]
    C_gas_stop = costs["C_gas_stop"]; C_gas_FOM = costs["C_gas_FOM"]
    C_gas_CO2 = costs["C_gas_CO2"]
    C_nuc_gen = costs["C_nuc_gen"]; C_nuc_FOM = costs["C_nuc_FOM"]
    C_bio_gen = costs["C_bio_gen"]; C_bio_FOM = costs["C_bio_FOM"]
    C_es_degrade = costs["C_es_degrade"]; C_es_FOM = costs["C_es_FOM"]
    C_hyd_curt = costs["C_hyd_curt"]
    C_fr_degrade = costs["C_fr_degrade"]; C_fr_FOM = costs["C_fr_FOM"]
    C_fr_curt = costs["C_fr_curt"]
    # 2026-10-16 光热（4.11，1075-1077）；海南 N_csp=0 → 三项恒 0，对拍不受影响
    C_csp = costs["C_csp"]; C_csp_gen = costs["C_csp_gen"]; C_csp_FOM = costs["C_csp_FOM"]

    # ---- 4.12 需求响应（1079-1082）----
    C_DR = Delta_t * _wsum(inp.dr.ShiftCost, Sv.P_shift_in, T) \
         + Delta_t * _wsum(inp.dr.CutCost, Sv.P_cut, T) \
         + inp.econ.fixedCostPeriodRatio * float(np.sum(inp.dr.DRFixedCost))

    # ---- 4.7/4.8/4.9 弃电、失负荷（1056/1060/1068、1091）----
    C_curt = inp.econ.c_wind_curt * Delta_t * Sv.P_wind_curt.sum() \
           + inp.econ.c_pv_curt * Delta_t * Sv.P_pv_curt.sum() \
           + C_hyd_curt + C_fr_curt                                 # 1090：四项弃电汇总
    C_shed = inp.econ.c_shed * Delta_t * Sv.P_shed.sum()                 # 1091

    # ---- FOM 常数（1055/1059）----
    C_windFOM = float(np.sum(inp.wind_pv.c_wind_FOM * inp.wind_pv.W_capacity))       # 1055
    C_pvFOM   = float(np.sum(inp.wind_pv.c_pv_FOM * inp.wind_pv.PV_capacity))        # 1059

    consts = (C_th_FOM + C_gas_FOM + C_nuc_FOM + C_bio_FOM + C_ps + C_es_FOM
              + C_hyd + C_fr_FOM + C_windFOM + C_pvFOM
              + inp.econ.fixedCostPeriodRatio * float(np.sum(inp.dr.DRFixedCost))
              + C_csp_FOM)                   # 1303：光热 FOM 进固定常数（海南 0）
    m.setObjective(C_th + C_gas + C_nuc + C_bio + C_ps + C_es + C_hyd + C_csp + C_fr
                   + C_DR + C_curt + C_shed + C_windFOM + C_pvFOM,
                   gp.GRB.MINIMIZE)          # 1088-1093（C_gen 含 C_hyd/C_csp/C_fr，C_curt 含四项）

    # ---- 成本分项（供 CostSummary/CostDetail 取值，MATLAB 1084-1093 + 1290-1312）----
    # 六大项：C_gen=C_th+C_gas+C_nuc+C_bio+C_windFOM+C_pvFOM+C_hyd+C_fr（1088，
    #   排放已分别计入 C_th/C_gas，不重复计）；C_sto=C_ps+C_es（1089）；
    #   C_curt 已含四项弃电（1090）；C_inter=0（海南无省间互济量）
    # 十项性质分解（1302-1309）：固定运维/变动发电/启停/碳排/污染物/老化
    #   与六大项是同一总成本的两种视角，不能相加
    C_CO2 = C_th_CO2 + C_gas_CO2                                        # 1084
    C_pollution = C_th_SO2 + C_th_NOX + C_th_dust                       # 1085
    return {
        "constants": consts,
        "C_gen": C_th + C_gas + C_nuc + C_bio + C_windFOM + C_pvFOM
                 + C_hyd + C_csp + C_fr,
        "C_sto": C_ps + C_es,
        "C_DR": C_DR, "C_curt": C_curt, "C_shed": C_shed, "C_inter": 0.0,
        "C_th": C_th, "C_gas": C_gas, "C_nuc": C_nuc, "C_bio": C_bio,
        "C_fr": C_fr, "C_hyd": C_hyd, "C_es": C_es, "C_ps": C_ps,
        "C_windFOM": C_windFOM, "C_pvFOM": C_pvFOM,
        "C_th_fuel": C_th_fuel, "C_th_start": C_th_start, "C_th_stop": C_th_stop,
        "C_th_FOM": C_th_FOM, "C_th_CO2": C_th_CO2,
        "C_th_SO2": C_th_SO2, "C_th_NOX": C_th_NOX, "C_th_dust": C_th_dust,
        "C_gas_gen": C_gas_gen, "C_gas_start": C_gas_start,
        "C_gas_stop": C_gas_stop, "C_gas_FOM": C_gas_FOM, "C_gas_CO2": C_gas_CO2,
        "C_nuc_gen": C_nuc_gen, "C_nuc_FOM": C_nuc_FOM,
        "C_bio_gen": C_bio_gen, "C_bio_FOM": C_bio_FOM,
        "C_es_degrade": C_es_degrade, "C_es_FOM": C_es_FOM,
        "C_fr_degrade": C_fr_degrade, "C_fr_FOM": C_fr_FOM,
        "C_hyd_curt": C_hyd_curt, "C_CO2": C_CO2, "C_pollution": C_pollution,
        "C_csp": C_csp, "C_csp_gen": C_csp_gen, "C_csp_FOM": C_csp_FOM,  # 光热 4.11（海南 0）
        "C_wind_curt": inp.econ.c_wind_curt * Delta_t * Sv.P_wind_curt.sum(),
        "C_pv_curt": inp.econ.c_pv_curt * Delta_t * Sv.P_pv_curt.sum(),
        "C_fr_curt": C_fr_curt,
    }


def solve(inp: reader.Inputs) -> Solution:
    """组装 + 求解 + 取值；返回 Solution（纯数据），不做前端口径组装。

    Args:
        inp (Inputs): 系统公共输入。

    Returns:
        Solution: 求解结果（无可行解时抛 RuntimeError）。
    """
    m = gp.Model("coal_gas_nuc_bio_sto_hyd_fr_T%d" % inp.time.T)
    insts, V, Sv = build(m, inp)
    cost_exprs = add_objective(m, inp, insts, Sv)
    m.update()

    # 本次运行子目录：results/ 下按 参数+时间戳 命名，日志/CSV/图 全落这里
    run_dir = config.RESULT_DIR / ("T%d_gap%g_%s" % (
        inp.time.T, config.MIGAP, datetime.now().strftime("%Y%m%d_%H%M%S")))
    run_dir.mkdir(parents=True, exist_ok=True)
    config.RESULT_DIR = run_dir

    # 设置求解参数镜像 MATLAB 1164-1168
    m.Params.MIPGap = config.MIGAP
    m.Params.TimeLimit = config.TIME_LIMIT
    m.Params.MIPFocus = config.MIP_FOCUS
    m.Params.Heuristics = config.HEURISTICS
    m.Params.DualReductions = config.DUAL_REDUCTIONS
    m.Params.Threads = 1        # 单线程锁定多最优解
    m.Params.Seed = 0           # 显式固定随机种子（默认即 0，防御性声明）
    m.Params.LogFile = str(config.RESULT_DIR / ("gurobi_py_T%d.log" % inp.time.T))
    m.optimize()

    # ---- 四项核对 ----
    print("---- 四项核对 ----")
    print("变量规模:  列 %d  二值 %d" % (m.NumVars, m.NumBinVars))
    print("约束规模:  行 %d  非零元 %d" % (m.NumConstrs, m.getAttr("NumNZs")))
    names = {gp.GRB.OPTIMAL: "OPTIMAL", gp.GRB.TIME_LIMIT: "TIME_LIMIT",
             gp.GRB.INFEASIBLE: "INFEASIBLE", gp.GRB.UNBOUNDED: "UNBOUNDED",
             gp.GRB.INTERRUPTED: "INTERRUPTED"}
    print("求解状态:  %s" % names.get(m.Status, str(m.Status)))
    if m.SolCount > 0:
        gap = m.getAttr("MIPGap")
        print("目标函数值: %.6f 元（常数部分 %.6f，MIPGap %.4f%%，耗时 %.1fs）"
              % (m.ObjVal, cost_exprs["constants"], 100 * gap, m.Runtime))
        solution = Solution(m, inp, insts, Sv, cost_exprs)
        solution.to_timeseries_csv()
        return solution
    raise RuntimeError(f"求解无可行解（{names.get(m.Status, m.Status)}），"
                       f"日志: {config.RESULT_DIR / ('gurobi_py_T%d.log' % inp.time.T)}")


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    inp = reader.load_inputs()
    solution = solve(inp)
    if solution.four_items["objective"]["value"] is not None:
        from analysis_export import AnalysisExport
        ae = AnalysisExport(inp, solution)
        metrics = ae.metrics()
        write_result_json(inp, solution, metrics)
        import plot as PL
        PL.plt.rcParams["font.family"] = ["Microsoft YaHei"]
        PL.plt.rcParams["axes.unicode_minus"] = False
        plot = PL.Plot(inp, solution)
        plot.operation_png(save_png=True)
        plot.chart_csv()
        ae.export(metrics)
    print("---- 本次配置 ----")
    print("时段数 T=%d  起始 %s  步长 %g h  水文情景 %s"
          % (config.T, config.START_TIME, config.DELTA_T, config.HYDROLOGY_SCENARIO))
    print("MIPGap=%g  时限 %gs  MIPFocus=%g  Heuristics=%g  DualReductions=%g"
          % (config.MIGAP, config.TIME_LIMIT, config.MIP_FOCUS,
             config.HEURISTICS, config.DUAL_REDUCTIONS))
    print("省间互济=%s  既定跨区送电=%s"
          % (config.USE_INTERPROVINCIAL, config.USE_FIXED_TRANSFER))
    print("总耗时: %.1fs（读数+建模+求解+落盘+出图）" % (time.perf_counter() - t0))
