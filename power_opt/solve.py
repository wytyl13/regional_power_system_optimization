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

import json
import numpy as np
import pandas as pd
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
from model.units import friendly as FR
from model import context as CT


def _wsum(w: np.ndarray, X: gp.MVar, T: int):
    """Σ_{i,t} w_i·X(i,t)，即 MATLAB sum(w'*X)。
    w 按 np.repeat 铺 (N,T) 后逐元素乘（沙盒已实测的广播原语）再全求和。"""
    W = np.repeat(np.asarray(w)[:, None], T, axis=1)
    return (W * X).sum()


def _json_series(keys, X):
    """一条 series 条目 {keys: 行标签, data: N×T 数值}；不写 time = 挂全局小时轴。"""
    return {"keys": [str(k) for k in keys], "data": np.asarray(X).tolist()}


def _write_result_json(path, inp, report, series):
    """落 结果.json（服务/前端口径）：meta + report + time + labels + series。
    report 字段名对齐上游 海南168小时验证结果.json（m 文件无此段，5 项指标为本侧按名定义）；
    网格非全局小时轴的条目在 series 里自带 time/标签覆盖（如 E_hyd_curt 按机组×月周期）。"""
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
        "time": [ts.strftime("%Y-%m-%dT%H:%M") for ts in inp.Time],
        "labels": labels,
        "series": series,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))


def build(m: gp.Model, inp: reader.Inputs):
    """变量 + 约束全组装（煤电 → 气 → 核/生 → 储 → 水 → 友好 → 系统层），返回句柄。"""
    coal = TH.load_coal(inp)
    gas = GA.load_gas(inp)
    nuc = NU.load_nuclear(inp)
    bio = BI.load_biomass(inp)
    ps = PS.load_ps(inp)
    es = ES.load_es(inp)
    hyd = HY.load_hyd(inp)
    fr = FR.load_fr(inp)
    Tv = TH.add_vars(m, coal)
    Gv = GA.add_vars(m, gas)
    Nv = NU.add_vars(m, nuc)
    Bv = BI.add_vars(m, bio)
    Pv = PS.add_vars(m, ps)
    Ev = ES.add_vars(m, es, inp.T)
    Hv = HY.add_vars(m, hyd, inp.T)
    Fv = FR.add_vars(m, fr, inp.T)
    Sv = CT.add_vars(m, inp)
    units = CT.RegisteredUnits(thermal=Tv, gas=Gv, nuclear=Nv, biomass=Bv,
                               pumped_storage=Pv, electrochemical=Ev, hydro=Hv,
                               friendly=Fv)
    TH.add_constraints(m, coal, Tv)
    GA.add_constraints(m, gas, Gv)
    NU.add_constraints(m, nuc, Nv)
    BI.add_constraints(m, bio, Bv)
    PS.add_constraints(m, ps, Pv, inp, Sv)
    ES.add_constraints(m, es, Ev, inp, inp.T)
    HY.add_constraints(m, hyd, Hv, inp, inp.T)
    FR.add_constraints(m, fr, Fv, inp, inp.T)
    CT.add_constraints(m, inp, Sv, units, coal, gas, nuc, bio, ps, es, hyd, fr)
    return coal, gas, nuc, bio, ps, es, hyd, fr, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv, units


def add_objective(m: gp.Model, inp: reader.Inputs, coal: TH.CoalInputs,
                  gas: GA.GasInputs, nuc: NU.NuclearInputs, bio: BI.BiomassInputs,
                  ps: PS.PSInputs, es: ES.ESInputs, hyd: HY.HydInputs, fr: FR.FrInputs,
                  Tv: TH.ThermalVars, Gv: GA.GasVars, Nv: NU.NuclearVars, Bv: BI.BiomassVars,
                  Pv: PS.PSVars, Ev: ES.ESVars, Hv: HY.HydVars, Fv: FR.FrVars,
                  Sv: CT.SystemVars) -> float:
    """MATLAB 4.1-4.14（1016-1094）一比一对照；返回常数部分供对账。"""
    T, Delta_t = inp.T, inp.Delta_t

    # ---- 4.1 煤电（1016-1025）----
    C_th_fuel  = Delta_t * Tv.F_th.sum()                              # 1017
    C_th_start = _wsum(coal.c_th_start, Tv.v_th, T)                    # 1018
    C_th_stop  = _wsum(coal.c_th_stop, Tv.w_th, T)                     # 1019
    C_th_FOM   = float(np.sum(coal.c_th_FOM * coal.P_th_rated))        # 1020 常数，v2 基数=额定
    C_th_CO2   = inp.c_CO2 * Delta_t * Tv.CO2_th.sum()                # 1021
    C_th_SO2   = inp.c_SO2 * Delta_t * _wsum(coal.SO2_th_factor, Tv.P_th, T)    # 1022
    C_th_NOX   = inp.c_NOX * Delta_t * _wsum(coal.NOX_th_factor, Tv.P_th, T)    # 1023
    C_th_dust  = inp.c_dust * Delta_t * _wsum(coal.dust_th_factor, Tv.P_th, T)  # 1024
    C_th = C_th_fuel + C_th_start + C_th_stop + C_th_FOM + \
        C_th_CO2 + C_th_SO2 + C_th_NOX + C_th_dust                    # 1025

    # ---- 4.2 气电（1027-1033）----
    C_gas_gen   = Delta_t * _wsum(gas.c_gas_gen, Gv.P_gas, T)          # 1028
    C_gas_start = _wsum(gas.c_gas_start, Gv.v_gas, T)                   # 1029
    C_gas_stop  = _wsum(gas.c_gas_stop, Gv.w_gas, T)                    # 1030
    C_gas_FOM   = float(np.sum(gas.c_gas_FOM * gas.P_gas_rated))        # 1031 常数，v2 基数=额定
    C_gas_CO2   = inp.c_CO2 * Delta_t * _wsum(gas.CO2_gas_factor, Gv.P_gas, T)  # 991+1032
    C_gas = C_gas_gen + C_gas_start + C_gas_stop + C_gas_FOM + C_gas_CO2  # 1033（无 SO2/NOX/烟尘）

    # ---- 4.10 核电（1070-1073）----
    C_nuc_gen = Delta_t * _wsum(nuc.c_nuc_gen, Nv.P_nuc, T)              # 1071
    C_nuc_FOM = float(np.sum(nuc.c_nuc_FOM * nuc.P_nuc_max))              # 1072 常数
    C_nuc = C_nuc_gen + C_nuc_FOM                                          # 1073

    # ---- 4.3 生物质（1035-1038）----
    C_bio_gen = Delta_t * _wsum(bio.c_bio_gen, Bv.P_bio, T)               # 1036
    C_bio_FOM = float(np.sum(bio.c_bio_FOM * bio.P_bio_rated))            # 1037 常数，v2 基数=额定
    C_bio = C_bio_gen + C_bio_FOM                                          # 1038

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
    C_hyd = float(inp.fixedCostScale
                  * (np.sum(hyd.FOM_ror * hyd.CAP_ror)
                     + np.sum(hyd.FOM_res * hyd.CAP_res)))          # 1042-1043 仅 FOM 常数
    C_hyd_curt = inp.c_hyd_curt * Hv.E_hyd_curt.sum()               # 1044 弃水惩罚

    # ---- 4.9 新能源友好电站（1062-1068）----
    C_fr_degrade = Delta_t * (_wsum(fr.c_fr_degrade, Fv.P_fr_charge, T)
                              + _wsum(fr.c_fr_degrade, Fv.P_fr_discharge, T))   # 1063
    C_fr_FOM = float(inp.fixedCostScale
                     * (np.sum(fr.fom_wind * fr.wind_cap)
                        + np.sum(fr.fom_pv * fr.pv_cap)
                        + np.sum(fr.fom_power * fr.P_fr_es_max)
                        + np.sum(fr.fom_energy * fr.E_fr_max)))     # 1064-1066 常数
    C_fr = C_fr_degrade + C_fr_FOM                                  # 1067
    C_fr_curt = (inp.c_wind_curt * Delta_t * Fv.P_fr_wind_curt.sum()
                 + inp.c_pv_curt * Delta_t * Fv.P_fr_pv_curt.sum())  # 1068

    # ---- 4.12 需求响应（1079-1082）----
    C_DR = Delta_t * _wsum(inp.ShiftCost, Sv.P_shift_in, T) \
         + Delta_t * _wsum(inp.CutCost, Sv.P_cut, T) \
         + inp.fixedCostPeriodRatio * float(np.sum(inp.DRFixedCost))

    # ---- 4.7/4.8/4.9 弃电、失负荷（1056/1060/1068、1091）----
    C_curt = inp.c_wind_curt * Delta_t * Sv.P_wind_curt.sum() \
           + inp.c_pv_curt * Delta_t * Sv.P_pv_curt.sum() \
           + C_hyd_curt + C_fr_curt                                 # 1090：四项弃电汇总
    C_shed = inp.c_shed * Delta_t * Sv.P_shed.sum()                 # 1091

    # ---- FOM 常数（1055/1059）----
    C_windFOM = float(np.sum(inp.c_wind_FOM * inp.W_capacity))       # 1055
    C_pvFOM   = float(np.sum(inp.c_pv_FOM * inp.PV_capacity))        # 1059

    consts = (C_th_FOM + C_gas_FOM + C_nuc_FOM + C_bio_FOM + C_ps + C_es_FOM
              + C_hyd + C_fr_FOM + C_windFOM + C_pvFOM
              + inp.fixedCostPeriodRatio * float(np.sum(inp.DRFixedCost)))
    m.setObjective(C_th + C_gas + C_nuc + C_bio + C_ps + C_es + C_hyd + C_fr
                   + C_DR + C_curt + C_shed + C_windFOM + C_pvFOM,
                   gp.GRB.MINIMIZE)          # 1088-1093（C_gen 含 C_hyd/C_fr，C_curt 含四项）
    return consts


def solve(inp: reader.Inputs):
    """组装 + 目标 + 求解；返回 (m, coal, gas, nuc, bio, ps, es, hyd, fr, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv)。"""
    m = gp.Model("coal_gas_nuc_bio_sto_hyd_fr_T%d" % inp.T)
    coal, gas, nuc, bio, ps, es, hyd, fr, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv, units = build(m, inp)
    consts = add_objective(m, inp, coal, gas, nuc, bio, ps, es, hyd, fr,
                           Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv)
    m.update()

    # 本次运行子目录：results/ 下按 参数+时间戳 命名，日志/CSV/图 全落这里
    run_dir = config.RESULT_DIR / ("T%d_gap%g_%s" % (
        inp.T, config.MIGAP, datetime.now().strftime("%Y%m%d_%H%M%S")))
    run_dir.mkdir(parents=True, exist_ok=True)
    config.RESULT_DIR = run_dir
    # 求解参数镜像 MATLAB 1164-1168
    m.Params.MIPGap = config.MIGAP
    m.Params.TimeLimit = config.TIME_LIMIT
    m.Params.MIPFocus = config.MIP_FOCUS
    m.Params.Heuristics = config.HEURISTICS
    m.Params.DualReductions = config.DUAL_REDUCTIONS
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
        # ---- 结果落盘 + 平衡残差（对齐 MATLAB 1177-1190 的自检）----
        import numpy as _np
        P = _np.asarray(Tv.P_th.X); u = _np.round(Tv.u_th.X).astype(int)
        Pg = _np.asarray(Gv.P_gas.X); ug = _np.round(Gv.u_gas.X).astype(int)
        Pn = _np.asarray(Nv.P_nuc.X)
        Pb = _np.asarray(Bv.P_bio.X)
        Pw = _np.asarray(Sv.P_wind.X); Ppv = _np.asarray(Sv.P_pv.X)
        Pwc = _np.asarray(Sv.P_wind_curt.X); Ppc = _np.asarray(Sv.P_pv_curt.X)
        Psh = _np.asarray(Sv.P_shed.X)
        Pso = _np.asarray(Sv.P_shift_out.X); Psi = _np.asarray(Sv.P_shift_in.X)
        Pct = _np.asarray(Sv.P_cut.X)
        Pps_gen = _np.asarray(Pv.P_ps_gen.X); Pps_pump = _np.asarray(Pv.P_ps_pump.X)
        Eps = _np.asarray(Pv.E_ps.X)
        Pes_ch = _np.asarray(Ev.P_es_charge.X); Pes_dis = _np.asarray(Ev.P_es_discharge.X)
        Ees = _np.asarray(Ev.E_es.X)
        Phr = _np.asarray(Hv.P_hyd_ror.X); Phs = _np.asarray(Hv.P_hyd_res.X)
        Rhy = _np.asarray(Hv.R_hyd_hot.X); Ehc = _np.asarray(Hv.E_hyd_curt.X)
        Pfw = _np.asarray(Fv.P_fr_wind_out.X); Pfp = _np.asarray(Fv.P_fr_pv_out.X)
        Pfc = _np.asarray(Fv.P_fr_charge.X); Pfd = _np.asarray(Fv.P_fr_discharge.X)
        Ef = _np.asarray(Fv.E_fr.X)

        # ---- 合并时序 CSV：每列一条曲线，列名与 m 文件"七、结果文件导出"段逐字符一致 ----
        # E 类取每小时末值（丢第 0 列初值，初值是输入回显两边恒等）；E_hyd_curt 是
        # 周期量 → 常量列；空设备不生成列；数值双精度原样落盘（不四舍五入，
        # 舍入噪声累加会污染 compare 的 1e-6 硬门槛）。
        cols = {"Time": [ts.strftime("%Y-%m-%d %H:%M") for ts in inp.Time]}

        def _acols(prefix, X):        # 分区级列 {prefix}_{AreaID}
            for r in range(inp.Narea):
                cols[f"{prefix}_{inp.AreaID[r]}"] = _np.asarray(X)[r]

        def _ucols(prefix, keys, X, skip=0):   # 机组级列 {prefix}_{UnitKey}；skip=1 丢 E 初值列
            for k, row in zip(keys, _np.asarray(X)):
                cols[f"{prefix}_{k}"] = _np.asarray(row)[skip:]

        _acols("P_load", inp.P_load)
        _ucols("P_th", coal.UnitKey_th, P); _ucols("u_th", coal.UnitKey_th, u)
        _ucols("P_gas", gas.UnitKey_gas, Pg); _ucols("u_gas", gas.UnitKey_gas, ug)
        _ucols("P_nuc", nuc.UnitKey_nuc, Pn); _ucols("P_bio", bio.UnitKey_bio, Pb)
        for prefix, X in [("P_wind", Pw), ("P_pv", Ppv), ("P_wind_curt", Pwc),
                          ("P_pv_curt", Ppc), ("P_shed", Psh), ("P_shift_out", Pso),
                          ("P_shift_in", Psi), ("P_cut", Pct)]:
            _acols(prefix, X)
        _ucols("P_ps_gen", ps.UnitKey_ps, Pps_gen); _ucols("P_ps_pump", ps.UnitKey_ps, Pps_pump)
        _ucols("E_ps", ps.UnitKey_ps, Eps, skip=1)
        _ucols("P_es_charge", es.UnitKey_es, Pes_ch)
        _ucols("P_es_discharge", es.UnitKey_es, Pes_dis)
        _ucols("E_es", es.UnitKey_es, Ees, skip=1)
        _ucols("P_hyd_ror", hyd.UnitKey_ror, Phr); _ucols("P_hyd_res", hyd.UnitKey_res, Phs)
        _ucols("R_hyd_hot", hyd.UnitKey_res, Rhy)
        for k in range(hyd.N_hyd_period):     # 周期量常量列：E_hyd_curt_{机组}_{年月}
            key = hyd.UnitKey_res[hyd.hydPeriodUnit[k]]
            ym = inp.Time[hyd.hydPeriodHours[k][0]].strftime("%Y%m")
            cols[f"E_hyd_curt_{key}_{ym}"] = _np.full(inp.T, Ehc[k, 0])
        _ucols("P_fr_wind_out", fr.UnitKey_fr, Pfw); _ucols("P_fr_pv_out", fr.UnitKey_fr, Pfp)
        _ucols("P_fr_charge", fr.UnitKey_fr, Pfc); _ucols("P_fr_discharge", fr.UnitKey_fr, Pfd)
        _ucols("E_fr", fr.UnitKey_fr, Ef, skip=1)
        pd.DataFrame(cols).to_csv(config.RESULT_DIR / f"时序结果_T{inp.T}.csv",
                                  index=False, encoding="utf-8")
        print("已导出: %s" % (config.RESULT_DIR / f"时序结果_T{inp.T}.csv"))
        # 绘图曲线 CSV（前端复现 设备运行_*.png）：plot 顶部 import solve，须函数内延迟导入
        import plot as PL
        PL.write_chart_csv(inp, coal, gas, nuc, bio, ps, es, hyd, fr,
                           Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv)
        # 平衡残差 = 发电 + 储能净出力 + 净受入 + 失负荷 − 响应后负荷（MATLAB 1178）
        A_th = _np.zeros((inp.Narea, coal.N_th)); A_th[coal.area_th, _np.arange(coal.N_th)] = 1
        A_gas = _np.zeros((inp.Narea, gas.N_gas)); A_gas[gas.area_gas, _np.arange(gas.N_gas)] = 1
        A_nuc = _np.zeros((inp.Narea, nuc.N_nuc)); A_nuc[nuc.area_nuc, _np.arange(nuc.N_nuc)] = 1
        A_bio = _np.zeros((inp.Narea, bio.N_bio)); A_bio[bio.area_bio, _np.arange(bio.N_bio)] = 1
        A_ps = _np.zeros((inp.Narea, ps.N_ps)); A_ps[ps.area_ps, _np.arange(ps.N_ps)] = 1
        A_es = _np.zeros((inp.Narea, es.N_es)); A_es[es.area_es, _np.arange(es.N_es)] = 1
        A_hyd_ror = _np.zeros((inp.Narea, hyd.N_hyd_ror)); A_hyd_ror[hyd.area_hyd_ror, _np.arange(hyd.N_hyd_ror)] = 1
        A_hyd_res = _np.zeros((inp.Narea, hyd.N_hyd_res)); A_hyd_res[hyd.area_hyd_res, _np.arange(hyd.N_hyd_res)] = 1
        A_fr = _np.zeros((inp.Narea, fr.N_fr)); A_fr[fr.area_fr, _np.arange(fr.N_fr)] = 1
        A_line = _np.zeros((inp.Narea, inp.N_line))
        A_line[inp.line_from, _np.arange(inp.N_line)] = -1
        A_line[inp.line_to, _np.arange(inp.N_line)] = 1
        P_G = A_th @ P + A_gas @ Pg + A_nuc @ Pn + A_bio @ Pb \
            + A_hyd_ror @ Phr + A_hyd_res @ Phs \
            + _np.asarray(Sv.P_wind.X) + _np.asarray(Sv.P_pv.X) \
            + A_fr @ (Pfw + Pfp + Pfd)                              # MATLAB 981（P_fr 展开项）
        P_sto = (A_es @ Pes_dis - A_es @ Pes_ch + A_ps @ (Pps_gen - Pps_pump))  # MATLAB 974
        P_tr = A_line @ _np.asarray(Sv.P_line.X)
        P_D = inp.P_load - _np.asarray(Sv.P_shift_out.X) + _np.asarray(Sv.P_shift_in.X) \
            - _np.asarray(Sv.P_cut.X)
        resid = P_G + P_sto + P_tr + _np.asarray(Sv.P_shed.X) - P_D
        print("功率平衡残差 max: %.3g MW（MATLAB 1178 行同款自检，应≈0）"
              % _np.abs(resid).max())

        # ---- 新指标（MATLAB 1181-1190）----
        Psh = _np.asarray(Sv.P_shed.X)
        # P_RE_available/P_RE_curt 与 context 3.16 同式：省级聚合含 fr 项（全数据/解值）
        A_prov = _np.zeros((inp.Nprovince, inp.Narea))
        A_prov[inp.areaProvince, _np.arange(inp.Narea)] = 1
        RE_avail = A_prov @ (inp.P_wind_available + inp.P_pv_available
                             + A_fr @ (fr.P_fr_wind_available
                                       + fr.P_fr_pv_available))     # 992 同式
        RE_curt = A_prov @ (_np.asarray(Sv.P_wind_curt.X)
                            + _np.asarray(Sv.P_pv_curt.X)
                            + A_fr @ (_np.asarray(Fv.P_fr_wind_curt.X)
                                      + _np.asarray(Fv.P_fr_pv_curt.X)))   # 993 同式
        REAvailableEnergy = RE_avail.sum(axis=1) * inp.Delta_t       # 1181
        RECurtailedEnergy = RE_curt.sum(axis=1) * inp.Delta_t        # 1182
        utilization = _np.full(inp.Nprovince, _np.nan)               # 1183：无风光能量时无定义
        hasRE = REAvailableEnergy > 0
        utilization[hasRE] = 1 - RECurtailedEnergy[hasRE] / REAvailableEnergy[hasRE]  # 1185
        EnergyNotServed = Psh.sum() * inp.Delta_t                    # 1187 未供电量，MWh
        LossOfLoadHours = float((Psh > 1e-4).any(axis=0).sum()) * inp.Delta_t   # 1188
        MaxLoadShortfall = Psh.sum(axis=0).max()                     # 1189 全省最大同时缺口，MW
        print("新能源利用率：%.2f%%，弃风光电量：%.3f MWh，失负荷：%.3f MWh"
              % (100 * utilization[0], RECurtailedEnergy.sum(), EnergyNotServed))  # 1186
        print("缺电小时：%.0f h，最大缺口：%.3f MW" % (LossOfLoadHours, MaxLoadShortfall))  # 1190

        # ---- 结果.json（服务/前端口径；report 19 字段对齐上游验证 json）----
        # 5 项本侧定义（m 文件无 report 段）：CO2_t=1001 同式总量、热/冷备用裕度=
        # 各对自身比例约束(1005/1006)的最小富余、储能闭合=max|E末−E初|、
        # 最小约束残差=Gurobi Slack 全约束最小值（负值=数值越界）
        series = {
            "P_th": _json_series(coal.UnitKey_th, P), "u_th": _json_series(coal.UnitKey_th, u),
            "P_gas": _json_series(gas.UnitKey_gas, Pg), "u_gas": _json_series(gas.UnitKey_gas, ug),
            "P_nuc": _json_series(nuc.UnitKey_nuc, Pn), "P_bio": _json_series(bio.UnitKey_bio, Pb),
            "P_wind": _json_series(inp.AreaName, Sv.P_wind.X),
            "P_pv": _json_series(inp.AreaName, Sv.P_pv.X),
            "P_wind_curt": _json_series(inp.AreaName, Sv.P_wind_curt.X),
            "P_pv_curt": _json_series(inp.AreaName, Sv.P_pv_curt.X),
            "P_shed": _json_series(inp.AreaName, Sv.P_shed.X),
            "P_shift_out": _json_series(inp.AreaName, Sv.P_shift_out.X),
            "P_shift_in": _json_series(inp.AreaName, Sv.P_shift_in.X),
            "P_cut": _json_series(inp.AreaName, Sv.P_cut.X),
            "P_ps_gen": _json_series(ps.UnitKey_ps, Pps_gen),
            "P_ps_pump": _json_series(ps.UnitKey_ps, Pps_pump),
            "E_ps": _json_series(ps.UnitKey_ps, Eps),
            "P_es_charge": _json_series(es.UnitKey_es, Pes_ch),
            "P_es_discharge": _json_series(es.UnitKey_es, Pes_dis),
            "E_es": _json_series(es.UnitKey_es, Ees),
            "P_hyd_ror": _json_series(hyd.UnitKey_ror, Phr),
            "P_hyd_res": _json_series(hyd.UnitKey_res, Phs),
            "R_hyd_hot": _json_series(hyd.UnitKey_res, Rhy),
            "E_hyd_curt": {   # 非 hour 网格：keys 即周期标签（机组·月），一行一值
                "keys": [f"{hyd.UnitKey_res[i]}·{inp.Time[hrs[0]]:%Y-%m}"
                         for i, hrs in zip(hyd.hydPeriodUnit, hyd.hydPeriodHours)],
                "data": Ehc.tolist()},
            "P_fr_wind_out": _json_series(fr.UnitKey_fr, Pfw),
            "P_fr_pv_out": _json_series(fr.UnitKey_fr, Pfp),
            "P_fr_charge": _json_series(fr.UnitKey_fr, Pfc),
            "P_fr_discharge": _json_series(fr.UnitKey_fr, Pfd),
            "E_fr": _json_series(fr.UnitKey_fr, Ef),
        }
        Rhot = A_prov @ (A_th @ Tv.R_th_hot.X + A_gas @ Gv.R_gas_hot.X
                         + A_hyd_res @ Hv.R_hyd_hot.X + A_ps @ Pv.R_ps_hot.X
                         + A_es @ Ev.R_es_hot.X)                                  # 1002 同式
        Rcold = A_prov @ (A_th @ Tv.R_th_cold.X + A_gas @ Gv.R_gas_cold.X)       # 1003 同式
        peak = inp.P_week_peak
        hot_margin = float((Rhot - _np.repeat(inp.hotReserveRatio[:, None], inp.T, 1) * peak).min())
        cold_margin = float((Rcold - _np.repeat(inp.coldReserveRatio[:, None], inp.T, 1) * peak).min())
        CO2_t = float(inp.Delta_t * (_np.asarray(Tv.CO2_th.X).sum()
                       + (_np.asarray(gas.CO2_gas_factor)[:, None] * Pg).sum()))  # 1001 同式
        closure = float(max(_np.abs(e[:, -1] - e[:, 0]).max(initial=0.0)
                            for e in (Eps, Ees, Ef)))
        try:
            # Gurobi MIP 的 Slack 对所有方向统一 = rhs−activity（沙盒实测：≥ 约束
            # 超额满足报负值），换算成"越界为负"的残差：≤ 取 slack、≥ 取 −slack、
            # = 取 −|slack|，与上游 minimumConstraintResidual 同口径（应 ~1e-11）
            sl = _np.array(m.getAttr("Slack", m.getConstrs()))
            se = _np.array([c.Sense for c in m.getConstrs()])
            min_residual = float(_np.where(se == ">", -sl,
                                           _np.where(se == "=", -_np.abs(sl), sl)).min())
        except gp.GurobiError:
            min_residual = None    # MIP 解取不到 Slack 时不造数，置 null
        report = {
            "hours": inp.T, "startTime": str(inp.Time[0]), "endTime": str(inp.Time[-1]),
            "scenario": config.HYDROLOGY_SCENARIO,
            "problem": 0 if m.Status == gp.GRB.OPTIMAL else int(m.Status),   # YALMIP 口径 0=解出
            "solverInfo": "Successfully solved (GUROBI)" if m.Status == gp.GRB.OPTIMAL
                          else names.get(m.Status, str(m.Status)),
            "solverSeconds": m.Runtime, "objectiveYuan": float(m.ObjVal),
            "balanceErrorMW": float(_np.abs(resid).max()),
            "minimumConstraintResidual": min_residual,
            "energyNotServedMWh": float(EnergyNotServed),
            "lossOfLoadHours": float(LossOfLoadHours),
            "maxLoadShortfallMW": float(MaxLoadShortfall),
            "renewableUtilization": float(utilization[0]),   # 1186 打印同口径取首省
            "CO2_t": CO2_t, "hotReserveMarginMW": hot_margin,
            "coldReserveMarginMW": cold_margin,
            "maxStorageClosureErrorMWh": closure,
            "mipGap": float(m.getAttr("MIPGap")),
        }
        _write_result_json(config.RESULT_DIR / "结果.json", inp, report, series)
        print("已导出: %s" % (config.RESULT_DIR / "结果.json"))
    return m, coal, gas, nuc, bio, ps, es, hyd, fr, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    inp = reader.load_inputs()
    m, coal, gas, nuc, bio, ps, es, hyd, fr, Tv, \
        Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv = solve(inp)
    if m.SolCount > 0:                                   # 无可行解不出图（对齐 plot.main 的判断）
        import plot as PL                                 # 放 __main__ 里延迟导入，避免和 plot.py 顶部的 import solve 循环
        PL.plt.rcParams["font.family"] = ["Microsoft YaHei"]   # 汉字字体，plot.main 原有设置
        PL.plt.rcParams["axes.unicode_minus"] = False
        PL.plot_operation(inp, coal, gas, nuc, bio, ps, es, hyd, fr,
                          Tv, Gv, Nv, Bv, Pv, Ev, Hv, Fv, Sv, save_png=True)
    print("---- 本次配置 ----")
    print("时段数 T=%d  起始 %s  步长 %g h  水文情景 %s"
          % (config.T, config.START_TIME, config.DELTA_T, config.HYDROLOGY_SCENARIO))
    print("MIPGap=%g  时限 %gs  MIPFocus=%g  Heuristics=%g  DualReductions=%g"
          % (config.MIGAP, config.TIME_LIMIT, config.MIP_FOCUS,
             config.HEURISTICS, config.DUAL_REDUCTIONS))
    print("省间互济=%s  既定跨区送电=%s"
          % (config.USE_INTERPROVINCIAL, config.USE_FIXED_TRANSFER))
    print("总耗时: %.1fs（读数+建模+求解+落盘+出图）" % (time.perf_counter() - t0))