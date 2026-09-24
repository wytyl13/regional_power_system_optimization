#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/22 13:43
@Author  : weiyutao
@File    : context.py
对应 MATLAB 2.6-2.13 变量（623-624、629-634、665-676）与 3.12-3.18 约束（930-999）。
各电源模块向 register_* 挂钩；耦合只发生在本层。
本轮煤电单一电源：P_G 仅 A_th·P_th，其余空表电源聚合项为 0（MATLAB 行为一致）。
已实测语义（沙盒，黑体写法不再变）：
  - 参数广播用 np.repeat 显式铺 (N,T)；MLinExpr 无 .T，聚合一律左乘稀疏 A @ P。
  - MVar(3D) 矩阵乘按 N² 展开（thermal 教训），本层变量全 2D，无此坑。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass

import gurobipy as gp
import scipy.sparse as sp

from reader import Inputs


@dataclass
class RegisteredUnits:
    """电源注册表：solve.py 组装时填；本层只读这些项做全局约束。"""
    thermal: object = None          # thermal.ThermalVars
    gas: object = None              # gas.GasVars（气电轮接入）
    nuclear: object = None          # nuclear.NuclearVars（核生轮接入）
    biomass: object = None          # biomass.BiomassVars
    pumped_storage: object = None   # pumped_storage.PSVars（储能轮接入）
    electrochemical: object = None  # electrochemical.ESVars
    hydro: object = None            # hydro.HydVars（水电轮接入）


@dataclass
class SystemVars:
    P_wind: gp.MVar; P_pv: gp.MVar
    P_wind_curt: gp.MVar; P_pv_curt: gp.MVar
    P_shed: gp.MVar
    P_line: gp.MVar
    P_shift_out: gp.MVar; P_shift_in: gp.MVar; P_cut: gp.MVar
    P_shift_pair: list
    u_ps_area_gen: gp.MVar; u_ps_area_pump: gp.MVar


def add_vars(m: gp.Model, inp: Inputs) -> SystemVars:
    Narea, T = inp.Narea, inp.T
    INF = gp.GRB.INFINITY
    P_wind      = m.addMVar((Narea, T), lb=-INF, name="P_wind")              # MATLAB 629
    P_pv        = m.addMVar((Narea, T), lb=-INF, name="P_pv")                # 633
    P_wind_curt = m.addMVar((Narea, T), lb=-INF, name="P_wind_curt")         # 630
    P_pv_curt   = m.addMVar((Narea, T), lb=-INF, name="P_pv_curt")           # 634
    P_shed      = m.addMVar((Narea, T), lb=-INF, name="P_shed")              # 666
    P_line      = m.addMVar((inp.N_line, T), lb=-INF, name="P_line")         # 667
    P_shift_out = m.addMVar((Narea, T), lb=-INF, name="P_shift_out")          # 670
    P_shift_in  = m.addMVar((Narea, T), lb=-INF, name="P_shift_in")           # 671
    P_cut       = m.addMVar((Narea, T), lb=-INF, name="P_cut")               # 672
    P_shift_pair = [m.addMVar(len(inp.shiftFrom[r]), lb=-INF,
                              name=f"shift_pair_{r}") if len(inp.shiftFrom[r]) else None
                   for r in range(Narea)]                                    # 673-676
    u_ps_area_gen  = m.addMVar((Narea, T), vtype=gp.GRB.BINARY, name="u_ps_area_gen")   # 623
    u_ps_area_pump = m.addMVar((Narea, T), vtype=gp.GRB.BINARY, name="u_ps_area_pump")  # 624
    m.addConstr(u_ps_area_gen + u_ps_area_pump <= 1, name="u_ps_area_mu")  # 840：同分区抽发互斥（无守卫，N_ps=0 仍建）
    return SystemVars(P_wind=P_wind, P_pv=P_pv, P_wind_curt=P_wind_curt,
                      P_pv_curt=P_pv_curt, P_shed=P_shed, P_line=P_line,
                      P_shift_out=P_shift_out, P_shift_in=P_shift_in, P_cut=P_cut,
                      P_shift_pair=P_shift_pair, u_ps_area_gen=u_ps_area_gen,
                      u_ps_area_pump=u_ps_area_pump)


def _area_agg(arr_rows: np.ndarray, n_col: int, Narea: int):
    """机组→分区聚合矩阵：rows 是 0 基分区索引。返回 scipy 稀疏 (Narea, n_col)。"""
    return sp.csr_matrix((np.ones(len(arr_rows)), (arr_rows, np.arange(len(arr_rows)))),
                         shape=(Narea, len(arr_rows)))


def _scatter(edges: int, rows: np.ndarray, T: int):
    """边→小时散射矩阵 (edges, T)：边 e 计入其起点小时 rows[e]。"""
    return sp.csr_matrix((np.ones(edges), (np.arange(edges), rows)), shape=(edges, T))


def add_constraints(m: gp.Model, inp: Inputs, sysv: SystemVars,
                    units: RegisteredUnits, coal, gas, nuc, bio, ps, es, hyd) -> None:
    Narea, T, Np = inp.Narea, inp.T, inp.Nprovince
    INF = gp.GRB.INFINITY
    # ---- 3.7/3.8：风、光（可用=出力+弃；煤电单测中可用=0 → 全被钉成 0）----
    m.addConstr(sysv.P_wind + sysv.P_wind_curt == inp.P_wind_available, name="wind_bal")  # 861
    m.addConstr(sysv.P_pv + sysv.P_pv_curt == inp.P_pv_available, name="pv_bal")         # 866
    m.addConstr(sysv.P_wind[:,:] >= 0, name="wind_lb")                                    # 860
    m.addConstr(sysv.P_pv[:,:] >= 0, name="pv_lb")                                       # 865
    m.addConstr(sysv.P_wind_curt[:,:] >= 0, name="windcurt_lb")                          # 860
    m.addConstr(sysv.P_pv_curt[:,:] >= 0, name="pvcurt_lb")                              # 865

    # ---- 3.12 断面（MATLAB 932-933）----
    fwd = np.repeat(inp.P_line_forward[:, None], T, axis=1)      # (N_line,T) 实测广播
    rev = np.repeat(inp.P_line_reverse[:, None], T, axis=1)
    m.addConstr(sysv.P_line <= fwd, name="line_fwd")             # 933
    m.addConstr(sysv.P_line >= -rev, name="line_rev")            # 932

    # ---- 3.14 需求响应（MATLAB 947-965）----
    m.addConstr(sysv.P_shift_out >= 0, name="so_lb")             # 947
    m.addConstr(sysv.P_shift_out <= inp.P_shift_out_max, name="so_ub")    # 947
    m.addConstr(sysv.P_shift_in >= 0, name="si_lb")               # 948
    m.addConstr(sysv.P_shift_in <= inp.P_shift_in_max, name="si_ub")      # 948
    m.addConstr(sysv.P_cut >= 0, name="cut_lb")                   # 949
    m.addConstr(sysv.P_cut <= inp.P_cut_max, name="cut_ub")       # 949
    for r in range(Narea):
        nedge = len(inp.shiftFrom[r])
        if nedge:
            Sout = _scatter(nedge, inp.shiftFrom[r], T)
            SinS = _scatter(nedge, inp.shiftTo[r], T)
            m.addConstr(sysv.P_shift_pair[r] >= 0, name=f"pair_lb_{r}")            # 955（有守卫）
            m.addConstr(sysv.P_shift_pair[r] @ Sout == sysv.P_shift_out[r, :],     # 957（无守卫）
                        name=f"pair_out_{r}")
            m.addConstr(sysv.P_shift_pair[r] @ SinS == sysv.P_shift_in[r, :],     # 958（无守卫）
                        name=f"pair_in_{r}")
        else:   # MATLAB 无守卫：空边时 shiftOutMap 为空矩阵，等价把 shift 钉 0
            m.addConstr(sysv.P_shift_out[r, :] == 0, name=f"pair_out_{r}")
            m.addConstr(sysv.P_shift_in[r, :] == 0, name=f"pair_in_{r}")
    for d in range(inp.Nday):
        hours = np.where(inp.Time.normalize() == inp.Day[d])[0]
        m.addConstr(sysv.P_shift_out[:, hours].sum(axis=1) * inp.Delta_t
                    == sysv.P_shift_in[:, hours].sum(axis=1) * inp.Delta_t,
                    name=f"shift_day_{d}")                        # 961
        m.addConstr(sysv.P_cut[:, hours].sum(axis=1) * inp.Delta_t
                    <= inp.E_cut_day_max[:, d], name=f"cutday_{d}")          # 962
    m.addConstr(sysv.P_cut.sum(axis=1) * inp.Delta_t <= inp.AnnualCutEnergy_MWh,
                name="cut_year")                                  # 965

    # ---- 3.15 功率平衡（MATLAB 969-976）----
    A_th = _area_agg(coal.area_th, coal.N_th, Narea)
    A_gas = _area_agg(gas.area_gas, gas.N_gas, Narea)                 # MATLAB 311
    A_nuc = _area_agg(nuc.area_nuc, nuc.N_nuc, Narea)                 # MATLAB 316
    A_bio = _area_agg(bio.area_bio, bio.N_bio, Narea)                 # 317
    A_ps = _area_agg(ps.area_ps, ps.N_ps, Narea)                      # 315
    A_es = _area_agg(es.area_es, es.N_es, Narea)                      # 381
    A_hyd_ror = _area_agg(hyd.area_hyd_ror, hyd.N_hyd_ror, Narea)     # MATLAB 408
    A_hyd_res = _area_agg(hyd.area_hyd_res, hyd.N_hyd_res, Narea)     # 409
    P_G = (A_th @ units.thermal.P_th + A_gas @ units.gas.P_gas
           + A_nuc @ units.nuclear.P_nuc + A_bio @ units.biomass.P_bio
           + A_hyd_ror @ units.hydro.P_hyd_ror
           + A_hyd_res @ units.hydro.P_hyd_res                         # 818/976（P_hyd 展开进 P_G）
           + sysv.P_wind + sysv.P_pv)                                 # 970-971
    # 核/生/水电不进 CO2（992 只有煤、气），MATLAB 同；储能进平衡（P_sto）与热备用（998）
    P_sto = (A_es @ units.electrochemical.P_es_discharge             # 974：P_es_dis−P_es_ch
             - A_es @ units.electrochemical.P_es_charge
             + A_ps @ (units.pumped_storage.P_ps_gen
                       - units.pumped_storage.P_ps_pump))             # 832/833 先聚合后作差
    # 风/光是系统层变量，恒进平衡式（煤电单测中被钉 0，但项必须存在，MATLAB 同）
    P_tr = _line_net(inp, sysv)                                     # 934
    P_D = inp.P_load - sysv.P_shift_out + sysv.P_shift_in - sysv.P_cut   # 972
    m.addConstr(P_D >= 0, name="PD_lb")                             # 973
    m.addConstr(sysv.P_shed >= 0, name="shed_lb")                   # 973
    shedCap = np.repeat(inp.ShedMaxRatio[:, None], T, axis=1)
    m.addConstr(sysv.P_shed <= shedCap * P_D, name="shed_cap")      # 974
    m.addConstr(P_G + P_sto + P_tr + sysv.P_shed == P_D, name="balance")   # 976（互济 0、跨区 0）

    # ---- 3.16 新能源利用率（MATLAB 981-985）----
    A_prov = sp.csr_matrix((np.ones(Narea), (inp.areaProvince, np.arange(Narea))),
                           shape=(Np, Narea))
    curtail = A_prov @ (sysv.P_wind_curt + sysv.P_pv_curt)          # 省级弃电 (Np,T)
    availab = A_prov @ (inp.P_wind_available + inp.P_pv_available)  # 省级可用 (Np,T)
    minU = np.repeat(inp.renewableUtilizationMin[:, None], T, axis=1)
    m.addConstr(curtail.sum(axis=1) * inp.Delta_t
                <= (1 - minU[:, 0]) * availab.sum(axis=1) * inp.Delta_t,
                name="renewable")                                   # 985（Np 行）

    # ---- 3.17 CO2 省级上限（MATLAB 989-992）----
    CO2_gas = np.repeat(gas.CO2_gas_factor[:, None], T, axis=1) * units.gas.P_gas  # 991 t/h，因子×P 表达式
    CO2_prov = (A_prov @ (A_th @ units.thermal.CO2_th
                          + A_gas @ CO2_gas)).sum(axis=1) * inp.Delta_t              # 992
    m.addConstr(CO2_prov <= inp.CO2Limit, name="co2_cap")           # 993（Np 行）

    # ---- 3.18 周备用（MATLAB 995-999）----
    R_hot = A_prov @ (A_th @ units.thermal.R_th_hot
                      + A_gas @ units.gas.R_gas_hot
                      + A_hyd_res @ units.hydro.R_hyd_hot            # 1001：库容水电热备
                      + A_ps @ units.pumped_storage.R_ps_hot
                      + A_es @ units.electrochemical.R_es_hot)      # 997-998：储进热备
    R_cold = A_prov @ (A_th @ units.thermal.R_th_cold
                       + A_gas @ units.gas.R_gas_cold)               # 999：冷备只有煤、气（水电无冷备）
    pk = np.repeat((inp.adequacyFactor - 1)[:, None], T, axis=1)
    cr = np.repeat(inp.coldReserveRatio[:, None], T, axis=1)
    m.addConstr(R_hot + R_cold >= pk * inp.P_week_peak, name="reserve_hot")  # 998
    m.addConstr(R_cold >= cr * inp.P_week_peak, name="reserve_cold")        # 999


def _line_net(inp: Inputs, sysv: SystemVars):
    """省内断面净受入 P_tr = A_line·P_line（MATLAB 934；送端-1 受端+1）。"""
    Nl, Narea = inp.N_line, inp.Narea
    rows = np.concatenate([inp.line_from, inp.line_to])
    cols = np.concatenate([np.arange(Nl), np.arange(Nl)])
    vals = np.concatenate([-np.ones(Nl), np.ones(Nl)])
    A_line = sp.csr_matrix((vals, (rows, cols)), shape=(Narea, Nl))
    return A_line @ sysv.P_line


if __name__ == "__main__":
    import numpy as np
    import sys as _sys
    _sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
    import reader
    from model.units import thermal as TH
    from model.units import gas as GA
    from model.units import nuclear as NU
    from model.units import biomass as BI
    from model.units import pumped_storage as PS
    from model.units import electrochemical as ES
    from model.units import hydro as HY
    inp = reader.load_inputs()
    m = gp.Model()
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
    Sv = add_vars(m, inp)
    units = RegisteredUnits(thermal=Tv, gas=Gv, nuclear=Nv, biomass=Bv,
                            pumped_storage=Pv, electrochemical=Ev, hydro=Hv)
    TH.add_constraints(m, coal, Tv)
    GA.add_constraints(m, gas, Gv)
    NU.add_constraints(m, nuc, Nv)
    BI.add_constraints(m, bio, Bv)
    PS.add_constraints(m, ps, Pv, inp, Sv)
    ES.add_constraints(m, es, Ev, inp, inp.T)
    HY.add_constraints(m, hyd, Hv, inp, inp.T)
    add_constraints(m, inp, Sv, units, coal, gas, nuc, bio, ps, es, hyd)
    m.update()
    exp_sys_rows = m.NumConstrs - 132440 - 44888 - 960 - 1261 - 9100 - 268 - 123
    print('系统层变量  期望=2204  实际=%d' % (m.NumVars - 87032 - 24056 - 240 - 312 - 3625 - 145 - 73))
    print('系统层约束行  期望=3493  实际=%d' % exp_sys_rows)
    print('全模型: 列 %d(期望 117687) 行 %d(期望 192533) 二值 %d(期望 43440)' %
          (m.NumVars, m.NumConstrs, m.NumBinVars))
    print('全模型非零元: %d（期望 559464）' % m.getAttr('NumNZs'))
    assert (m.NumVars, m.NumConstrs, m.NumBinVars, m.getAttr('NumNZs')) \
        == (117687, 192533, 43440, 559464), '四项与 MATLAB log 不符'
    print('四项与 MATLAB log（gurobi_202609241109.log）逐位一致')