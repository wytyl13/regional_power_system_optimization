#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/28 11:00
@Author  : weiyutao
@File    : solve.py
组装全模型 + 目标函数（MATLAB 4.1-4.14，1016-1094 行）+ 调 Gurobi。
唯一直接碰求解器的文件；四项核对（变量/约束/状态/目标值）打印进 stdout。
海南 v2：FOM 基数改额定容量（4.1/4.2/4.3）、新增 4.9 友好电站成本、
求解参数镜像 1164-1168、新指标镜像 1177-1190。
"""

from __future__ import annotations

import numpy as np
import gurobipy as gp
from datetime import datetime

import config
import power_opt.reader as reader
from solution import (SolutionData, compute_metrics, extract_all,
                      write_result_json, write_timeseries_csv)
from model.units import thermal as TH
from model.units import gas as GA
from model.units import nuclear as NU
from model.units import biomass as BI
from model.units import pumped_storage as PS
from model.units import electrochemical as ES
from model.units import hydro as HY
from model.units import friendly as FR
from model import context as CT


def _wsum(w: np.ndarray, X: gp.MVar, T: int):
    """Σ_{i,t} w_i·X(i,t)，即 MATLAB sum(w'*X)。
    w 按 np.repeat 铺 (N,T) 后逐元素乘（沙盒已实测的广播原语）再全求和。"""
    W = np.repeat(np.asarray(w)[:, None], T, axis=1)
    return (W * X).sum()


def build(m: gp.Model, inp: reader.Inputs):
    """变量 + 约束全组装（煤电 → 气 → 核/生 → 储 → 水 → 友好 → 系统层），返回句柄。"""
    th = TH.Thermal()                  # 煤电已类化（四步生命周期）；其余电源仍为函数模块
    th.load(inp)
    coal = th.inputs
    ga = GA.Gas()                       # 气电已类化
    ga.load(inp)
    gas = ga.inputs
    nu = NU.Nuclear()                   # 核电已类化
    nu.load(inp)
    nuc = nu.inputs
    bi = BI.Biomass()                   # 生物质已类化
    bi.load(inp)
    bio = bi.inputs
    ps = PS.load_ps(inp)
    es = ES.load_es(inp)
    hyd = HY.load_hyd(inp)
    fr = FR.load_fr(inp)
    
    Tv = th.add_vars(m)
    Gv = ga.add_vars(m)
    Nv = nu.add_vars(m)
    Bv = bi.add_vars(m)
    Pv = PS.add_vars(m, ps)
    Ev = ES.add_vars(m, es, inp.time.T)
    Hv = HY.add_vars(m, hyd, inp.time.T)
    Fv = FR.add_vars(m, fr, inp.time.T)
    Sv = CT.add_vars(m, inp)
    
    units = CT.RegisteredUnits(thermal=Tv, gas=Gv, nuclear=Nv, biomass=Bv,
                               pumped_storage=Pv, electrochemical=Ev, hydro=Hv,
                               friendly=Fv)
    th.add_constraints(m, inp)         # 煤电类化四步之三；建序不变
    ga.add_constraints(m, inp)         # 气电类化；建序不变
    nu.add_constraints(m, inp)         # 核电类化；建序不变
    bi.add_constraints(m, inp)         # 生物质类化；建序不变
    PS.add_constraints(m, ps, Pv, inp, Sv)
    ES.add_constraints(m, es, Ev, inp, inp.time.T)
    HY.add_constraints(m, hyd, Hv, inp, inp.time.T)
    FR.add_constraints(m, fr, Fv, inp, inp.time.T)
    CT.add_constraints(m, inp, Sv, units, coal, gas, nuc, bio, ps, es, hyd, fr)
    return coal, gas, nuc, bio, ps, es, hyd, fr, th, ga, nu, bi, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv, units


def add_objective(m: gp.Model, inp: reader.Inputs, th: TH.Thermal, ga: GA.Gas,
                  nu: NU.Nuclear, bi: BI.Biomass,
                  coal: TH.CoalInputs,
                  gas: GA.GasInputs, nuc: NU.NuclearInputs, bio: BI.BiomassInputs,
                  ps: PS.PSInputs, es: ES.ESInputs, hyd: HY.HydInputs, fr: FR.FrInputs,
                  Tv: TH.ThermalVars, Gv: GA.GasVars, Nv: NU.NuclearVars, Bv: BI.BiomassVars,
                  Pv: PS.PSVars, Ev: ES.ESVars, Hv: HY.HydVars, Fv: FR.FrVars,
                  Sv: CT.SystemVars) -> dict:
    """MATLAB 4.1-4.14（1016-1094）一比一对照；返回成本表达式 dict 供取值
    （六大项/十项分解对齐 MATLAB 1290-1312 行；海南无光热/互济，C_csp/C_inter=0）。
    """
    T, Delta_t = inp.time.T, inp.time.Delta_t

    # ---- 4.1 煤电（1016-1025）：类化后由单元 add_cost 产出，键名合同不变 ----
    th_cost = th.add_cost(Delta_t, inp.econ)
    C_th = th_cost["C_th"]
    C_th_fuel = th_cost["C_th_fuel"]; C_th_start = th_cost["C_th_start"]
    C_th_stop = th_cost["C_th_stop"]; C_th_FOM = th_cost["C_th_FOM"]
    C_th_CO2 = th_cost["C_th_CO2"]; C_th_SO2 = th_cost["C_th_SO2"]
    C_th_NOX = th_cost["C_th_NOX"]; C_th_dust = th_cost["C_th_dust"]

    # ---- 4.2 气电（1027-1033）：类化后由单元 add_cost 产出 ----
    ga_cost = ga.add_cost(Delta_t, inp.econ)
    C_gas = ga_cost["C_gas"]
    C_gas_gen = ga_cost["C_gas_gen"]; C_gas_start = ga_cost["C_gas_start"]
    C_gas_stop = ga_cost["C_gas_stop"]; C_gas_FOM = ga_cost["C_gas_FOM"]
    C_gas_CO2 = ga_cost["C_gas_CO2"]

    # ---- 4.10 核电（1070-1073）：类化后由单元 add_cost 产出 ----
    nu_cost = nu.add_cost(Delta_t, inp.econ)
    C_nuc = nu_cost["C_nuc"]; C_nuc_gen = nu_cost["C_nuc_gen"]; C_nuc_FOM = nu_cost["C_nuc_FOM"]

    # ---- 4.3 生物质（1035-1038）：类化后由单元 add_cost 产出 ----
    bi_cost = bi.add_cost(Delta_t, inp.econ)
    C_bio = bi_cost["C_bio"]; C_bio_gen = bi_cost["C_bio_gen"]; C_bio_FOM = bi_cost["C_bio_FOM"]

    # ---- 4.5 电化学储能（1046-1049）----
    # 1047: Delta_t*sum(c'*(P_ch+P_dis))，拆两次 _wsum 数值等价（MLinExpr 不进 _wsum）
    C_es_degrade = Delta_t * (_wsum(es.c_es_degrade, Ev.P_es_charge, T)
                              + _wsum(es.c_es_degrade, Ev.P_es_discharge, T))   # 1047
    C_es_FOM = float(np.sum(es.c_es_FOM * es.P_es_max
                            + es.c_es_energy_FOM * es.E_es_max))          # 1048 常数
    C_es = C_es_degrade + C_es_FOM                                         # 1049

    # ---- 4.6 抽水蓄能（1051-1052）----
    C_ps = float(np.sum(ps.c_ps_FOM * ps.P_ps_max))               # 1052 仅 FOM，无变动成本

    # ---- 4.4 水电（1040-1044）----
    C_hyd = float(inp.econ.fixedCostScale
                  * (np.sum(hyd.FOM_ror * hyd.CAP_ror)
                     + np.sum(hyd.FOM_res * hyd.CAP_res)))          # 1042-1043 仅 FOM 常数
    C_hyd_curt = inp.econ.c_hyd_curt * Hv.E_hyd_curt.sum()               # 1044 弃水惩罚

    # ---- 4.9 新能源友好电站（1062-1068）----
    C_fr_degrade = Delta_t * (_wsum(fr.c_fr_degrade, Fv.P_fr_charge, T)
                              + _wsum(fr.c_fr_degrade, Fv.P_fr_discharge, T))   # 1063
    C_fr_FOM = float(inp.econ.fixedCostScale
                     * (np.sum(fr.fom_wind * fr.wind_cap)
                        + np.sum(fr.fom_pv * fr.pv_cap)
                        + np.sum(fr.fom_power * fr.P_fr_es_max)
                        + np.sum(fr.fom_energy * fr.E_fr_max)))     # 1064-1066 常数
    C_fr = C_fr_degrade + C_fr_FOM                                  # 1067
    C_fr_curt = (inp.econ.c_wind_curt * Delta_t * Fv.P_fr_wind_curt.sum()
                 + inp.econ.c_pv_curt * Delta_t * Fv.P_fr_pv_curt.sum())  # 1068

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
              + inp.econ.fixedCostPeriodRatio * float(np.sum(inp.dr.DRFixedCost)))
    m.setObjective(C_th + C_gas + C_nuc + C_bio + C_ps + C_es + C_hyd + C_fr
                   + C_DR + C_curt + C_shed + C_windFOM + C_pvFOM,
                   gp.GRB.MINIMIZE)          # 1088-1093（C_gen 含 C_hyd/C_fr，C_curt 含四项）

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
                 + C_hyd + C_fr,
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
        "C_wind_curt": inp.econ.c_wind_curt * Delta_t * Sv.P_wind_curt.sum(),
        "C_pv_curt": inp.econ.c_pv_curt * Delta_t * Sv.P_pv_curt.sum(),
        "C_fr_curt": C_fr_curt,
    }


def solve(inp: reader.Inputs):
    """组装 + 求解 + 取值；返回纯数据 SolutionData，不做前端口径组装。"""
    m = gp.Model("coal_gas_nuc_bio_sto_hyd_fr_T%d" % inp.time.T)
    coal, gas, nuc, bio, ps, es, hyd, fr, th, ga, nu, bi, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv, units = build(m, inp)
    cost_exprs = add_objective(m, inp, th, ga, nu, bi, coal, gas, nuc, bio, ps, es, hyd, fr,
                               Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv)
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
        solution = extract_all(m, inp, coal, gas, nuc, bio, ps, es, hyd, fr,
                               Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv, cost_exprs)
        write_timeseries_csv(inp, solution)
        return solution
    raise RuntimeError(f"求解无可行解（{names.get(m.Status, m.Status)}），"
                       f"日志: {config.RESULT_DIR / ('gurobi_py_T%d.log' % inp.time.T)}")


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    inp = reader.load_inputs()
    solution = solve(inp)
    if solution.four_items["objective"]["value"] is not None:
        metrics = compute_metrics(inp, solution)
        write_result_json(inp, solution, config, metrics)
        import plot as PL
        PL.plt.rcParams["font.family"] = ["Microsoft YaHei"]
        PL.plt.rcParams["axes.unicode_minus"] = False
        PL.plot_operation(inp, solution, save_png=True)
        PL.write_chart_csv(inp, solution)
        import analysis_export as AE
        AE.export_analysis(inp, solution, metrics)
    print("---- 本次配置 ----")
    print("时段数 T=%d  起始 %s  步长 %g h  水文情景 %s"
          % (config.T, config.START_TIME, config.DELTA_T, config.HYDROLOGY_SCENARIO))
    print("MIPGap=%g  时限 %gs  MIPFocus=%g  Heuristics=%g  DualReductions=%g"
          % (config.MIGAP, config.TIME_LIMIT, config.MIP_FOCUS,
             config.HEURISTICS, config.DUAL_REDUCTIONS))
    print("省间互济=%s  既定跨区送电=%s"
          % (config.USE_INTERPROVINCIAL, config.USE_FIXED_TRANSFER))
    print("总耗时: %.1fs（读数+建模+求解+落盘+出图）" % (time.perf_counter() - t0))
