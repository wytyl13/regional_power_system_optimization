#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/23 18:40
@Author  : weiyutao
@File    : nuclear.py
核电模块：一比一对照 MATLAB 1.6 / 2.10 / 3.10 / 4.10。
读取的表：分区机组信息表[核电]、检修计划（Type=核电 行）。
与煤电的差异：不优化经济启停——状态 u_nuc=1-m_nuc 由检修输入直接决定，
只有 1 个决策变量 P_nuc；成本为线性 GEN_COST + FOM 常数；不进 CO2/备用。
爬坡在检修边界用 M=Pmax 放松（MATLAB 900 行）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
import gurobipy as gp

import config
from reader import _read, _zstr, Inputs


@dataclass
class NuclearInputs:
    """MATLAB 1.6 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_nuc: int
    UnitKey_nuc: list
    area_nuc: np.ndarray           # 机组 → 分区（0 基）
    P_nuc_max: np.ndarray; P_nuc_min: np.ndarray
    m_nuc: np.ndarray              # 检修：1 检修，0 可用（N_nuc×T）
    c_nuc_FOM: np.ndarray          # 本周期元/MW
    c_nuc_gen: np.ndarray          # 线性单位发电成本，元/MWh（MATLAB 211）
    P_nuc_0: np.ndarray
    RU_nuc: np.ndarray; RD_nuc: np.ndarray
    m_nuc_0: np.ndarray            # 周期前检修状态，0/1（MATLAB 217）


def load_nuclear(inp: Inputs) -> NuclearInputs:
    """镜像 MATLAB 1.6 节（201-224）。缺列缺值即报错。"""
    T = inp.T
    mtn_nuc = inp.maintenance[inp.maintenance["Type"].astype(str) == "核电"]

    req_nuc = ["UnitKey", "SUBSTR(C.ID，12)", "CAP", "MIN_POWER_GEN", "GEN_COST",
               "INIT_POWER", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
               "INIT_MAINTENANCE", "FOM_COST_PERIOD"]
    nucData = _read(config.UNIT_FILE, "核电", req_nuc)
    N_nuc = len(nucData)
    zone_nuc = [_zstr(v) for v in nucData["SUBSTR(C.ID，12)"]]
    areaPos = {a: i for i, a in enumerate(inp.AreaID)}
    if miss := [z for z in zone_nuc if z not in areaPos]:
        raise KeyError(f"核电分区编号 {set(miss)} 不在分区表中")
    area_nuc = np.array([areaPos[z] for z in zone_nuc])
    f = lambda c: nucData[c].to_numpy(float)
    UnitKey_nuc = nucData["UnitKey"].astype(str).tolist()
    P_nuc_max = f("CAP"); P_nuc_min = f("MIN_POWER_GEN")
    c_nuc_gen = inp.costScale * f("GEN_COST")
    P_nuc_0 = f("INIT_POWER"); RU_nuc = f("RAMP_LIMIT_UP"); RD_nuc = f("RAMP_LIMIT_DOWN")
    m_nuc_0 = f("INIT_MAINTENANCE")
    c_nuc_FOM = inp.fixedCostScale * f("FOM_COST_PERIOD")

    m_nuc = np.zeros((N_nuc, T))
    keyPos = {k: i for i, k in enumerate(UnitKey_nuc)}
    t_prev = inp.Time[0] - pd.Timedelta(hours=1)   # 起点前 1 小时（MATLAB 225）
    for _, plan in mtn_nuc.iterrows():
        key = str(plan["UnitKey"])
        if key not in keyPos:
            raise KeyError(f"检修计划机组 {key} 不在核电表中（不猜、不忽略）")
        periods = (inp.Time >= plan["StartTime"]) & (inp.Time < plan["EndTime"])
        m_nuc[keyPos[key], periods] = 1
        # MATLAB 225-228：起点前 1 小时已在检修窗口 → 初值覆盖为检修态、功率 0
        if plan["StartTime"] <= t_prev < plan["EndTime"]:
            m_nuc_0[keyPos[key]] = 1.0
            P_nuc_0[keyPos[key]] = 0.0

    for name, arr in [("P_nuc_max", P_nuc_max), ("P_nuc_min", P_nuc_min), ("m_nuc", m_nuc)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查核电输入表")

    return NuclearInputs(
        N_nuc=N_nuc, UnitKey_nuc=UnitKey_nuc, area_nuc=area_nuc,
        P_nuc_max=P_nuc_max, P_nuc_min=P_nuc_min, m_nuc=m_nuc, c_nuc_FOM=c_nuc_FOM,
        c_nuc_gen=c_nuc_gen, P_nuc_0=P_nuc_0, RU_nuc=RU_nuc, RD_nuc=RD_nuc,
        m_nuc_0=m_nuc_0,
    )


# ------------------- 2.10 决策变量（MATLAB 651-652 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class NuclearVars:
    """MATLAB 2.10 节决策变量的镜像，共 1 个。"""
    P_nuc: gp.MVar        # 实际出力，MW


def add_vars(m: gp.Model, nuc: NuclearInputs) -> NuclearVars:
    """连续变量 lb=-inf（镜像 sdpvar 无界默认），界限由 3.10 节约束钉。"""
    N, T = nuc.N_nuc, nuc.m_nuc.shape[1]
    P_nuc = m.addMVar((N, T), lb=-INF, name="P_nuc")                     # MATLAB 652
    return NuclearVars(P_nuc=P_nuc)


# ------------------- 3.10 核电约束（MATLAB 892-902 行） -------------------

def add_constraints(m: gp.Model, nuc: NuclearInputs, var: NuclearVars) -> None:
    """一比一对照 MATLAB 3.10 节。行数账本（T=24, N=10 → 960 行）：
    4 个 N×T 块（Pmin·u<=P、P<=Pmax·u、爬坡上、爬坡下），无 count/锁定。
    非零元账本（T=24 → 1,420）：Pmin 240、Pmax 240（右端全常数）、
    爬坡上/下各 470（首列 1 元——prev 是常数 P_nuc_0；其余 2 元）。
    数据特性（已核）：Pmin=Pmax=660 → P_nuc 被钉 660；RU/RD=792>660、
    M_nuc 右端全常数 → 爬坡不紧；m_nuc 全 0（检修不在 1 月窗口）。
    对账来源：MATLAB log 减法 全模型行 - 气电轮行（待 log 落地）。"""
    N, T = nuc.N_nuc, nuc.m_nuc.shape[1]
    P = var.P_nuc
    u_nuc = 1 - nuc.m_nuc                                    # MATLAB 893，常数矩阵

    # ---- MATLAB 894-895：P_min·u_nuc <= P_nuc <= P_max·u_nuc（u_nuc 常数，右端全常数）----
    PminU = np.repeat(nuc.P_nuc_min[:, None], T, axis=1) * u_nuc    # repmat(P_min).*u
    PmaxU = np.repeat(nuc.P_nuc_max[:, None], T, axis=1) * u_nuc    # repmat(P_max).*u
    m.addConstr(PminU <= P, name="Pmin")                                # 894
    m.addConstr(P <= PmaxU, name="Pmax")                                # 895

    # ---- MATLAB 896-897：previous 拼接（首列用初值）----
    m_prev = np.concatenate([nuc.m_nuc_0[:, None], nuc.m_nuc[:, :-1]], axis=1)  # 897
    # ---- MATLAB 900：M_nuc = repmat(P_max).*(m_nuc + m_nuc_previous)，常数矩阵 ----
    M_nuc = np.repeat(nuc.P_nuc_max[:, None], T, axis=1) * (nuc.m_nuc + m_prev)
    RU = np.repeat(nuc.RU_nuc[:, None], T, axis=1)
    RD = np.repeat(nuc.RD_nuc[:, None], T, axis=1)
    P0 = nuc.P_nuc_0[:, None]

    # ---- MATLAB 901-902：爬坡，检修边界以 M 放松。首时段 prev = P_nuc_0 常数 ----
    m.addConstr(P[:, 0:1] - P0 <= RU[:, 0:1] + M_nuc[:, 0:1], name="ramp_up_t0")   # 901 首时段
    m.addConstr(P0 - P[:, 0:1] <= RD[:, 0:1] + M_nuc[:, 0:1], name="ramp_dn_t0")   # 902 首时段
    if T > 1:
        m.addConstr(P[:, 1:] - P[:, :-1] <= RU[:, 1:] + M_nuc[:, 1:], name="ramp_up")  # 901
        m.addConstr(P[:, :-1] - P[:, 1:] <= RD[:, 1:] + M_nuc[:, 1:], name="ramp_dn")  # 902


if __name__ == "__main__":
    import reader
    inp = reader.load_inputs()
    nuc = load_nuclear(inp)
    print('输入自检: N_nuc=%d, m_nuc sum=%s, FOM常数=%.2f'
          % (nuc.N_nuc, nuc.m_nuc.sum(), float(np.sum(nuc.c_nuc_FOM * nuc.P_nuc_max))))

    m = gp.Model()
    Nv = add_vars(m, nuc)
    m.update()
    N, T = nuc.N_nuc, nuc.m_nuc.shape[1]
    exp_cols = N*T                        # 1 个 N×T 块
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=0  实际=%d' % m.NumBinVars)
    assert m.NumVars == exp_cols and m.NumBinVars == 0, '变量清单与推导不符'
    print('核电变量清单核对通过')

    add_constraints(m, nuc, Nv)
    m.update()
    exp_rows = 4*N*T                      # 4 个 N×T 块
    exp_nz = 2*N*T + 2*(N + (T-1)*N*2)    # Pmin/Pmax 各 N*T + 爬坡首列N、其余2元
    print('核电约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    print('核电非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    assert m.getAttr('NumNZs') == exp_nz, '非零元与手推账本不符'
    print('核电约束账本核对通过')
