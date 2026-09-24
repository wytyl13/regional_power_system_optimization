#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/24 11:40
@Author  : weiyutao
@File    : hydro.py
径流式+库容式水电模块：一比一对照 MATLAB 1.17 / 2.4 / 3.4 / 4.4。
机组行来自分区机组信息表[径流式水电/库容式水电]（AreaID 分区列，非 SUBSTR）；
时序与调节周期来自算例系统参数[水电时序/水电调节周期]（跨文件，注意副本要成对换）。
守卫纪律（坑 4 镜像）：3.4 节 if N_hyd_ror>0 / if N_hyd_res>0 逐行照抄不增不删。
E_hyd_curt 变量无条件建、无条件进目标（第 1 轮坑 2 的根源：空机组表+周期有行
= 无界），靠数据副本保证不发生，本模块不自行兜底。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
import gurobipy as gp

import config
from reader import _read, _zstr, _to_hours, Inputs


@dataclass
class HydInputs:
    """MATLAB 1.17 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_hyd_ror: int; N_hyd_res: int; N_hyd_period: int
    UnitKey_ror: list; UnitKey_res: list
    area_hyd_ror: np.ndarray       # 机组 → 分区（0 基）
    area_hyd_res: np.ndarray
    P_hyd_ror_min: np.ndarray; P_hyd_ror_expected: np.ndarray   # N_ror×T，MW
    P_hyd_res_min: np.ndarray; P_hyd_res_expected: np.ndarray   # N_res×T
    hydPeriodUnit: np.ndarray      # 周期行 → 库容机组（0 基，MATLAB 439）
    hydPeriodHours: list           # 每周期覆盖的小时序号（0 基，MATLAB 443）
    E_hyd_available: np.ndarray    # 周期可用电量，MWh（MATLAB 444）
    CAP_ror: np.ndarray; CAP_res: np.ndarray          # 成本 4.4 用
    FOM_ror: np.ndarray; FOM_res: np.ndarray          # 原表值（未乘折算）


def load_hyd(inp: Inputs) -> HydInputs:
    """镜像 MATLAB 1.17 节（402-445）。缺列缺值即报错，不补 0。"""
    req_hyd = ["UnitKey", "AreaID", "CAP", "FOM_COST_PERIOD"]
    rorData = _read(config.UNIT_FILE, "径流式水电", req_hyd)
    resData = _read(config.UNIT_FILE, "库容式水电", req_hyd)
    N_ror, N_res = len(rorData), len(resData)
    areaPos = {a: i for i, a in enumerate(inp.AreaID)}
    zone_ror = [_zstr(v) for v in rorData["AreaID"]]
    zone_res = [_zstr(v) for v in resData["AreaID"]]
    if miss := [z for z in zone_ror if z not in areaPos]:
        raise KeyError(f"径流式水电分区编号 {set(miss)} 不在分区表中")
    if miss := [z for z in zone_res if z not in areaPos]:
        raise KeyError(f"库容式水电分区编号 {set(miss)} 不在分区表中")
    area_ror = np.array([areaPos[z] for z in zone_ror], dtype=int)
    area_res = np.array([areaPos[z] for z in zone_res], dtype=int)
    UnitKey_ror = rorData["UnitKey"].astype(str).tolist()
    UnitKey_res = resData["UnitKey"].astype(str).tolist()

    # ---- 时序（MATLAB 410、419-434）：每台机按 UnitKey 领行，缺时刻即报错 ----
    hydTimeData = _read(config.SYSTEM_FILE, "水电时序",
                        ["UnitKey", "Time", "ForcedMW", "ExpectedMW"])
    T = inp.T
    P_ror_min = np.zeros((N_ror, T)); P_ror_exp = np.zeros((N_ror, T))
    P_res_min = np.zeros((N_res, T)); P_res_exp = np.zeros((N_res, T))
    for keys, mins, exps in ((UnitKey_ror, P_ror_min, P_ror_exp),
                             (UnitKey_res, P_res_min, P_res_exp)):
        for i, key in enumerate(keys):
            rows = hydTimeData[hydTimeData["UnitKey"].astype(str) == key]
            rows = rows.set_index(_to_hours(rows["Time"]))
            try:
                sel = rows.loc[inp.Time]
            except KeyError as e:
                raise KeyError(f"水电时序缺机组 {key} 的时刻 {e}") from None
            mins[i, :] = sel["ForcedMW"].to_numpy(float)
            exps[i, :] = sel["ExpectedMW"].to_numpy(float)

    # ---- 调节周期（MATLAB 411、415-418、436-445）：左闭右开，过滤到仿真区间 ----
    perData = _read(config.SYSTEM_FILE, "水电调节周期",
                    ["UnitKey", "StartTime", "EndTime", "AverageMW"])
    per_start = _to_hours(perData["StartTime"])
    per_end = _to_hours(perData["EndTime"])
    t0 = inp.Time[0]
    t1 = inp.Time[-1] + pd.Timedelta(hours=1)                      # MATLAB 436-437
    keep = (per_end > t0) & (per_start < t1)
    perData, per_start, per_end = perData[keep], per_start[keep], per_end[keep]
    N_period = len(perData)
    resPos = {k: i for i, k in enumerate(UnitKey_res)}
    try:
        hydPeriodUnit = np.array([resPos[str(k)] for k in perData["UnitKey"]],
                                 dtype=int)                        # MATLAB 439
    except KeyError as e:
        raise KeyError(f"水电调节周期的机组 {e} 不在库容式水电表中") from None
    hydPeriodHours = []
    E_hyd_available = np.zeros(N_period)
    for k in range(N_period):                                       # MATLAB 442-445
        hrs = np.where((inp.Time >= per_start.iloc[k])
                       & (inp.Time < per_end.iloc[k]))[0].astype(int)
        hydPeriodHours.append(hrs)
        E_hyd_available[k] = float(perData["AverageMW"].iloc[k]) * len(hrs) * inp.Delta_t

    for name, arr in [("P_hyd_ror_min", P_ror_min), ("P_hyd_ror_expected", P_ror_exp),
                      ("P_hyd_res_min", P_res_min), ("P_hyd_res_expected", P_res_exp),
                      ("E_hyd_available", E_hyd_available)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查水电输入表")

    return HydInputs(
        N_hyd_ror=N_ror, N_hyd_res=N_res, N_hyd_period=N_period,
        UnitKey_ror=UnitKey_ror, UnitKey_res=UnitKey_res,
        area_hyd_ror=area_ror, area_hyd_res=area_res,
        P_hyd_ror_min=P_ror_min, P_hyd_ror_expected=P_ror_exp,
        P_hyd_res_min=P_res_min, P_hyd_res_expected=P_res_exp,
        hydPeriodUnit=hydPeriodUnit, hydPeriodHours=hydPeriodHours,
        E_hyd_available=E_hyd_available,
        CAP_ror=rorData["CAP"].to_numpy(float), CAP_res=resData["CAP"].to_numpy(float),
        FOM_ror=rorData["FOM_COST_PERIOD"].to_numpy(float),
        FOM_res=resData["FOM_COST_PERIOD"].to_numpy(float),
    )


# ------------------- 2.4 决策变量（MATLAB 611-614 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class HydVars:
    """MATLAB 2.4 节决策变量的镜像，共 4 个（全连续）。"""
    P_hyd_ror: gp.MVar; P_hyd_res: gp.MVar   # 出力，MW
    R_hyd_hot: gp.MVar                        # 库容式热备用，MW
    E_hyd_curt: gp.MVar                       # 周期弃水电量，MWh（N_period×1）


def add_vars(m: gp.Model, hyd: HydInputs, T: int) -> HydVars:
    """连续变量 lb=-inf 照 MATLAB 611-614；E_hyd_curt 无条件建（坑 2 语义）。"""
    P_hyd_ror = m.addMVar((hyd.N_hyd_ror, T), lb=-INF, name="P_hyd_ror")     # 611
    P_hyd_res = m.addMVar((hyd.N_hyd_res, T), lb=-INF, name="P_hyd_res")     # 612
    R_hyd_hot = m.addMVar((hyd.N_hyd_res, T), lb=-INF, name="R_hyd_hot")     # 613
    E_hyd_curt = m.addMVar((hyd.N_hyd_period, 1), lb=-INF, name="E_hyd_curt")  # 614
    return HydVars(P_hyd_ror=P_hyd_ror, P_hyd_res=P_hyd_res,
                   R_hyd_hot=R_hyd_hot, E_hyd_curt=E_hyd_curt)


# ------------------- 3.4 水电约束（MATLAB 801-818 行） -------------------

def add_constraints(m: gp.Model, hyd: HydInputs, var: HydVars,
                    inp: Inputs, T: int) -> None:
    """一比一对照 MATLAB 3.4 节。守卫逐行照抄（坑 4）：if N_hyd_ror>0（803）、
    if N_hyd_res>0（806）；P_hyd 聚合表达式（818）留给 context 层。
    行数账本（T=24, N_ror=N_res=1, N_period=1 → 123 行）：ror 上下限 48
    + res 三块 72（Pmin≤P、R≥0、P+R≤expected）+ 周期块 3（E_used≤E_avail、
    E_curt==差、E_curt≥0）。
    非零元账本（242）：ror 2×24 + res 24+24+48 + 周期 4×H+2（H=24：P、R 各
    2×24 元 + E_curt 1 + E_curt≥0 1）。对账来源：MATLAB log 减法
    559,464 − 559,150 − 72(系统层) = 242。"""
    if hyd.N_hyd_ror > 0:                                            # MATLAB 803
        m.addConstr(hyd.P_hyd_ror_min <= var.P_hyd_ror, name="ror_lb")        # 804
        m.addConstr(var.P_hyd_ror <= hyd.P_hyd_ror_expected, name="ror_ub")
    if hyd.N_hyd_res > 0:                                            # MATLAB 806
        m.addConstr(hyd.P_hyd_res_min <= var.P_hyd_res, name="res_lb")        # 807
        m.addConstr(0 <= var.R_hyd_hot, name="res_R_lb")                      # 808
        m.addConstr(var.P_hyd_res + var.R_hyd_hot
                    <= hyd.P_hyd_res_expected, name="res_R_ub")
        for k in range(hyd.N_hyd_period):                            # MATLAB 809
            i = hyd.hydPeriodUnit[k]
            periods = hyd.hydPeriodHours[k]
            # MATLAB 813：E_hyd_used = sum(P+R over 周期时段)*Delta_t（表达式不建行）
            E_used = (var.P_hyd_res[i, periods].sum()
                      + var.R_hyd_hot[i, periods].sum()) * inp.Delta_t
            m.addConstr(E_used <= hyd.E_hyd_available[k], name=f"period_budget_{k}")
            m.addConstr(var.E_hyd_curt[k, 0] == hyd.E_hyd_available[k] - E_used,
                        name=f"period_curt_{k}")
            m.addConstr(var.E_hyd_curt[k, 0] >= 0, name=f"curt_lb_{k}")


if __name__ == "__main__":
    import reader
    inp = reader.load_inputs()
    hyd = load_hyd(inp)
    T = inp.T
    fom_const = float(inp.fixedCostScale
                      * (np.sum(hyd.FOM_ror * hyd.CAP_ror)
                         + np.sum(hyd.FOM_res * hyd.CAP_res)))      # MATLAB 1036-1037
    print('输入自检: N_ror=%d, N_res=%d, N_period=%d' %
          (hyd.N_hyd_ror, hyd.N_hyd_res, hyd.N_hyd_period))
    print('  ror: min[0]=%s expected[0]=%s' %
          (hyd.P_hyd_ror_min[0, :2], hyd.P_hyd_ror_expected[0, :2]))
    print('  res: min[0]=%s expected[0]=%s' %
          (hyd.P_hyd_res_min[0, :2], hyd.P_hyd_res_expected[0, :2]))
    print('  周期: 机组=%d 小时数=%d E_avail=%.2f MWh' %
          (hyd.hydPeriodUnit[0], len(hyd.hydPeriodHours[0]),
           hyd.E_hyd_available[0]))
    print('  C_hyd FOM 常数=%.2f 元, c_hyd_curt=%s 元/MWh' % (fom_const, inp.c_hyd_curt))

    m = gp.Model()
    Hv = add_vars(m, hyd, T)
    m.update()
    H_all = sum(len(h) for h in hyd.hydPeriodHours)
    exp_cols = (hyd.N_hyd_ror + 2 * hyd.N_hyd_res) * T + hyd.N_hyd_period
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    assert m.NumVars == exp_cols, '变量清单与推导不符'
    print('二值变量  期望=0  实际=%d' % m.NumBinVars)
    assert m.NumBinVars == 0, '水电不应有二值变量'

    add_constraints(m, hyd, Hv, inp, T)
    m.update()
    exp_rows = (2 * hyd.N_hyd_ror + 3 * hyd.N_hyd_res) * T + 3 * hyd.N_hyd_period
    exp_nz = (2 * hyd.N_hyd_ror + 4 * hyd.N_hyd_res) * T + 4 * H_all + 2 * hyd.N_hyd_period
    print('水电约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    print('水电非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.getAttr('NumNZs') == exp_nz, '非零元与手推账本不符'
    print('水电账本核对通过（MATLAB log 减法参照: +123 行 +242 元，T=24）')
