#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/22 14:05
@Author  : weiyutao
@File    : solve.py
组装全模型 + 目标函数（MATLAB 4.1-4.14，1001-1081 行）+ 调 Gurobi。
唯一直接碰求解器的文件；四项核对（变量/约束/状态/目标值）打印进 stdout。
煤电单测存活项：C_th 八项 + C_DR + 弃风弃光 + C_shed + 风光 FOM 常数；
空表电源项全为 0 常数（变量仍在模型里，不进目标，MATLAB 同行为）。
"""

from __future__ import annotations

import numpy as np
import gurobipy as gp
from datetime import datetime

import config
import reader
from model.units import thermal as TH
from model.units import gas as GA
from model.units import nuclear as NU
from model.units import biomass as BI
from model.units import pumped_storage as PS
from model.units import electrochemical as ES
from model.units import hydro as HY
from model import context as CT


def _wsum(w: np.ndarray, X: gp.MVar, T: int):
    """Σ_{i,t} w_i·X(i,t)，即 MATLAB sum(w'*X)。
    w 按 np.repeat 铺 (N,T) 后逐元素乘（沙盒已实测的广播原语）再全求和。"""
    W = np.repeat(np.asarray(w)[:, None], T, axis=1)
    return (W * X).sum()


def build(m: gp.Model, inp: reader.Inputs):
    """变量 + 约束全组装（煤电 → 气 → 核/生 → 储 → 系统层），返回句柄。"""
    coal = TH.load_coal(inp)
    gas = GA.load_gas(inp)
    nuc = NU.load_nuclear(inp)
    bio = BI.load_biomass(inp)
    ps = PS.load_ps(inp)
    es = ES.load_es(inp)
    hyd = HY.load_hyd(inp)
    Tv = TH.add_vars(m, coal)
    Gv = GA.add_vars(m, gas)
    Nv = NU.add_vars(m, nuc)
    Bv = BI.add_vars(m, bio)
    Pv = PS.add_vars(m, ps)
    Ev = ES.add_vars(m, es, inp.T)
    Hv = HY.add_vars(m, hyd, inp.T)
    Sv = CT.add_vars(m, inp)
    units = CT.RegisteredUnits(thermal=Tv, gas=Gv, nuclear=Nv, biomass=Bv,
                               pumped_storage=Pv, electrochemical=Ev, hydro=Hv)
    TH.add_constraints(m, coal, Tv)
    GA.add_constraints(m, gas, Gv)
    NU.add_constraints(m, nuc, Nv)
    BI.add_constraints(m, bio, Bv)
    PS.add_constraints(m, ps, Pv, inp, Sv)
    ES.add_constraints(m, es, Ev, inp, inp.T)
    HY.add_constraints(m, hyd, Hv, inp, inp.T)
    CT.add_constraints(m, inp, Sv, units, coal, gas, nuc, bio, ps, es, hyd)
    return coal, gas, nuc, bio, ps, es, hyd, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv, units


def add_objective(m: gp.Model, inp: reader.Inputs, coal: TH.CoalInputs,
                  gas: GA.GasInputs, nuc: NU.NuclearInputs, bio: BI.BiomassInputs,
                  ps: PS.PSInputs, es: ES.ESInputs, hyd: HY.HydInputs,
                  Tv: TH.ThermalVars, Gv: GA.GasVars, Nv: NU.NuclearVars, Bv: BI.BiomassVars,
                  Pv: PS.PSVars, Ev: ES.ESVars, Hv: HY.HydVars,
                  Sv: CT.SystemVars) -> float:
    """MATLAB 4.1-4.14 煤电+气电+核生存活项，一比一对照；返回常数部分供对账。"""
    T, Delta_t = inp.T, inp.Delta_t

    # ---- 4.1 煤电（1004-1013）----
    C_th_fuel  = Delta_t * Tv.F_th.sum()                              # 1004
    C_th_start = _wsum(coal.c_th_start, Tv.v_th, T)                    # 1005
    C_th_stop  = _wsum(coal.c_th_stop, Tv.w_th, T)                     # 1006
    C_th_FOM   = float(np.sum(coal.c_th_FOM * coal.P_th_max))          # 1007 常数
    C_th_CO2   = inp.c_CO2 * Delta_t * Tv.CO2_th.sum()                # 1008
    C_th_SO2   = inp.c_SO2 * Delta_t * _wsum(coal.SO2_th_factor, Tv.P_th, T)    # 1010
    C_th_NOX   = inp.c_NOX * Delta_t * _wsum(coal.NOX_th_factor, Tv.P_th, T)    # 1011
    C_th_dust  = inp.c_dust * Delta_t * _wsum(coal.dust_th_factor, Tv.P_th, T)  # 1012
    C_th = C_th_fuel + C_th_start + C_th_stop + C_th_FOM + \
        C_th_CO2 + C_th_SO2 + C_th_NOX + C_th_dust                    # 1013

    # ---- 4.2 气电（1017-1023）----
    C_gas_gen   = Delta_t * _wsum(gas.c_gas_gen, Gv.P_gas, T)          # 1018
    C_gas_start = _wsum(gas.c_gas_start, Gv.v_gas, T)                   # 1019
    C_gas_stop  = _wsum(gas.c_gas_stop, Gv.w_gas, T)                    # 1020
    C_gas_FOM   = float(np.sum(gas.c_gas_FOM * gas.P_gas_max))          # 1021 常数
    C_gas_CO2   = inp.c_CO2 * Delta_t * _wsum(gas.CO2_gas_factor, Gv.P_gas, T)  # 991+1022
    C_gas = C_gas_gen + C_gas_start + C_gas_stop + C_gas_FOM + C_gas_CO2  # 1023（无 SO2/NOX/烟尘）

    # ---- 4.10 核电（1060-1063）----
    C_nuc_gen = Delta_t * _wsum(nuc.c_nuc_gen, Nv.P_nuc, T)              # 1061
    C_nuc_FOM = float(np.sum(nuc.c_nuc_FOM * nuc.P_nuc_max))              # 1062 常数
    C_nuc = C_nuc_gen + C_nuc_FOM                                          # 1063

    # ---- 4.3 生物质（1025-1028）----
    C_bio_gen = Delta_t * _wsum(bio.c_bio_gen, Bv.P_bio, T)               # 1026
    C_bio_FOM = float(np.sum(bio.c_bio_FOM * bio.P_bio_max))              # 1027 常数
    C_bio = C_bio_gen + C_bio_FOM                                          # 1028

    # ---- 4.5 电化学储能（1036-1039）----
    # 1038: Delta_t*sum(c'*(P_ch+P_dis))，拆两次 _wsum 数值等价（MLinExpr 不进 _wsum）
    C_es_degrade = Delta_t * (_wsum(es.c_es_degrade, Ev.P_es_charge, T)
                              + _wsum(es.c_es_degrade, Ev.P_es_discharge, T))   # 1038
    C_es_FOM = float(np.sum(es.c_es_FOM * es.P_es_max
                            + es.c_es_energy_FOM * es.E_es_max))          # 1039 常数
    C_es = C_es_degrade + C_es_FOM

    # ---- 4.6 抽水蓄能（1041-1042）----
    C_ps = float(np.sum(ps.c_ps_FOM * ps.P_ps_max))               # 1043 仅 FOM，无变动成本

    # ---- 4.4 水电（1036-1038）----
    C_hyd = float(inp.fixedCostScale
                  * (np.sum(hyd.FOM_ror * hyd.CAP_ror)
                     + np.sum(hyd.FOM_res * hyd.CAP_res)))          # 1036-1037 仅 FOM 常数
    C_hyd_curt = inp.c_hyd_curt * Hv.E_hyd_curt.sum()               # 1038 弃水惩罚

    # ---- 4.12 需求响应（1071-1073）----
    C_DR = Delta_t * _wsum(inp.ShiftCost, Sv.P_shift_in, T) \
         + Delta_t * _wsum(inp.CutCost, Sv.P_cut, T) \
         + inp.fixedCostPeriodRatio * float(np.sum(inp.DRFixedCost))

    # ---- 4.7/4.8 弃电、失负荷（1052-1056、1079-1080）----
    C_curt = inp.c_wind_curt * Delta_t * Sv.P_wind_curt.sum() \
           + inp.c_pv_curt * Delta_t * Sv.P_pv_curt.sum() \
           + C_hyd_curt                                        # C_fr_curt：空表=0
    C_shed = inp.c_shed * Delta_t * Sv.P_shed.sum()

    # ---- FOM 常数（1050-1056）：煤电单测数据容量清 0 → 风光项恰为 0，仍照 MATLAB 建入 ----
    C_windFOM = float(np.sum(inp.c_wind_FOM * inp.W_capacity))
    C_pvFOM   = float(np.sum(inp.c_pv_FOM * inp.PV_capacity))

    consts = (C_th_FOM + C_gas_FOM + C_nuc_FOM + C_bio_FOM + C_ps + C_es_FOM
              + C_hyd + C_windFOM + C_pvFOM
              + inp.fixedCostPeriodRatio * float(np.sum(inp.DRFixedCost)))
    m.setObjective(C_th + C_gas + C_nuc + C_bio + C_ps + C_es + C_hyd + C_DR
                   + C_curt + C_shed + C_windFOM + C_pvFOM, gp.GRB.MINIMIZE)   # 1080/1083（C_gen 含 C_hyd）
    return consts


def solve(inp: reader.Inputs):
    """组装 + 目标 + 求解；返回 (m, coal, gas, nuc, bio, ps, es, hyd, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv)。"""
    m = gp.Model("coal_gas_nuc_bio_sto_hyd_T%d" % inp.T)
    coal, gas, nuc, bio, ps, es, hyd, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv, units = build(m, inp)
    consts = add_objective(m, inp, coal, gas, nuc, bio, ps, es, hyd,
                           Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv)
    m.update()

    # 本次运行子目录：results/ 下按 参数+时间戳 命名，日志/CSV/图 全落这里
    run_dir = config.RESULT_DIR / ("T%d_gap%g_%s" % (
        inp.T, config.MIGAP, datetime.now().strftime("%Y%m%d_%H%M%S")))
    run_dir.mkdir(parents=True, exist_ok=True)
    config.RESULT_DIR = run_dir
    m.Params.MIPGap = config.MIGAP
    m.Params.TimeLimit = config.TIME_LIMIT
    m.Params.LogFile = str(config.RESULT_DIR / ("gurobi_py_T%d.log" % inp.T))
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
              % (m.ObjVal, consts, 100 * gap, m.Runtime))
        # ---- 结果落盘 + 平衡残差（对齐 MATLAB 1154-1182 的自检，2026-09-22 用户定）----
        import numpy as _np
        P = _np.asarray(Tv.P_th.X); u = _np.round(Tv.u_th.X).astype(int)
        _np.savetxt(config.RESULT_DIR / f"P_th_T{inp.T}.csv", P, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"u_th_T{inp.T}.csv", u, delimiter=",", fmt="%d")
        Pg = _np.asarray(Gv.P_gas.X); ug = _np.round(Gv.u_gas.X).astype(int)
        _np.savetxt(config.RESULT_DIR / f"P_gas_T{inp.T}.csv", Pg, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"u_gas_T{inp.T}.csv", ug, delimiter=",", fmt="%d")
        Pn = _np.asarray(Nv.P_nuc.X)
        _np.savetxt(config.RESULT_DIR / f"P_nuc_T{inp.T}.csv", Pn, delimiter=",", fmt="%.6f")
        Pb = _np.asarray(Bv.P_bio.X)
        _np.savetxt(config.RESULT_DIR / f"P_bio_T{inp.T}.csv", Pb, delimiter=",", fmt="%.6f")
        for nm in ["P_wind", "P_pv", "P_wind_curt", "P_pv_curt"]:
            _np.savetxt(config.RESULT_DIR / f"{nm}_T{inp.T}.csv",
                        _np.asarray(getattr(Sv, nm).X), delimiter=",", fmt="%.6f")
        Pps_gen = _np.asarray(Pv.P_ps_gen.X); Pps_pump = _np.asarray(Pv.P_ps_pump.X)
        Eps = _np.asarray(Pv.E_ps.X)
        _np.savetxt(config.RESULT_DIR / f"P_ps_gen_T{inp.T}.csv", Pps_gen, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"P_ps_pump_T{inp.T}.csv", Pps_pump, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"E_ps_T{inp.T}.csv", Eps, delimiter=",", fmt="%.6f")
        Pes_ch = _np.asarray(Ev.P_es_charge.X); Pes_dis = _np.asarray(Ev.P_es_discharge.X)
        Ees = _np.asarray(Ev.E_es.X)
        _np.savetxt(config.RESULT_DIR / f"P_es_charge_T{inp.T}.csv", Pes_ch, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"P_es_discharge_T{inp.T}.csv", Pes_dis, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"E_es_T{inp.T}.csv", Ees, delimiter=",", fmt="%.6f")
        Phr = _np.asarray(Hv.P_hyd_ror.X); Phs = _np.asarray(Hv.P_hyd_res.X)
        Rhy = _np.asarray(Hv.R_hyd_hot.X); Ehc = _np.asarray(Hv.E_hyd_curt.X)
        _np.savetxt(config.RESULT_DIR / f"P_hyd_ror_T{inp.T}.csv", Phr, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"P_hyd_res_T{inp.T}.csv", Phs, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"R_hyd_hot_T{inp.T}.csv", Rhy, delimiter=",", fmt="%.6f")
        _np.savetxt(config.RESULT_DIR / f"E_hyd_curt_T{inp.T}.csv", Ehc, delimiter=",", fmt="%.6f")
        # 平衡残差 = 发电 + 储能净出力 + 净受入 + 失负荷 − 响应后负荷（MATLAB 1182）
        A_th = _np.zeros((inp.Narea, coal.N_th)); A_th[coal.area_th, _np.arange(coal.N_th)] = 1
        A_gas = _np.zeros((inp.Narea, gas.N_gas)); A_gas[gas.area_gas, _np.arange(gas.N_gas)] = 1
        A_nuc = _np.zeros((inp.Narea, nuc.N_nuc)); A_nuc[nuc.area_nuc, _np.arange(nuc.N_nuc)] = 1
        A_bio = _np.zeros((inp.Narea, bio.N_bio)); A_bio[bio.area_bio, _np.arange(bio.N_bio)] = 1
        A_ps = _np.zeros((inp.Narea, ps.N_ps)); A_ps[ps.area_ps, _np.arange(ps.N_ps)] = 1
        A_es = _np.zeros((inp.Narea, es.N_es)); A_es[es.area_es, _np.arange(es.N_es)] = 1
        A_hyd_ror = _np.zeros((inp.Narea, hyd.N_hyd_ror)); A_hyd_ror[hyd.area_hyd_ror, _np.arange(hyd.N_hyd_ror)] = 1
        A_hyd_res = _np.zeros((inp.Narea, hyd.N_hyd_res)); A_hyd_res[hyd.area_hyd_res, _np.arange(hyd.N_hyd_res)] = 1
        A_line = _np.zeros((inp.Narea, inp.N_line))
        A_line[inp.line_from, _np.arange(inp.N_line)] = -1
        A_line[inp.line_to, _np.arange(inp.N_line)] = 1
        P_G = A_th @ P + A_gas @ Pg + A_nuc @ Pn + A_bio @ Pb \
            + A_hyd_ror @ Phr + A_hyd_res @ Phs \
            + _np.asarray(Sv.P_wind.X) + _np.asarray(Sv.P_pv.X)
        P_sto = (A_es @ Pes_dis - A_es @ Pes_ch + A_ps @ (Pps_gen - Pps_pump))  # MATLAB 973
        P_tr = A_line @ _np.asarray(Sv.P_line.X)
        P_D = inp.P_load - _np.asarray(Sv.P_shift_out.X) + _np.asarray(Sv.P_shift_in.X) \
            - _np.asarray(Sv.P_cut.X)
        resid = P_G + P_sto + P_tr + _np.asarray(Sv.P_shed.X) - P_D
        print("功率平衡残差 max: %.3g MW（MATLAB 1182 行同款自检，应≈0）"
              % _np.abs(resid).max())
    return m, coal, gas, nuc, bio, ps, es, hyd, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    inp = reader.load_inputs()
    m, coal, _gas, _nuc, _bio, _ps, _es, _hyd, Tv, \
        _Gv, _Nv, _Bv, _Pv, _Ev, _Hv, Sv = solve(inp)
    if m.SolCount > 0:                                   # 无可行解不出图（对齐 plot.main 的判断）
        import plot as PL                                 # 放 __main__ 里延迟导入，避免和 plot.py 顶部的 import solve 循环
        PL.plt.rcParams["font.family"] = ["Microsoft YaHei"]   # 汉字字体，plot.main 原有设置
        PL.plt.rcParams["axes.unicode_minus"] = False
        PL.plot_operation(inp, coal, Tv, Sv, save_png=True)
    print("---- 本次配置 ----")
    print("时段数 T=%d  起始 %s  步长 %g h" % (config.T, config.START_TIME, config.DELTA_T))
    print("MIPGap=%g  时限 %gs  省间互济=%s  既定跨区送电=%s"
          % (config.MIGAP, config.TIME_LIMIT,
             config.USE_INTERPROVINCIAL, config.USE_FIXED_TRANSFER))
    print("总耗时: %.1fs（读数+建模+求解+落盘+出图）" % (time.perf_counter() - t0))