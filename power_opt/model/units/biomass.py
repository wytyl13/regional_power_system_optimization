#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/23 18:45
@Author  : weiyutao
@File    : biomass.py
生物质模块：一比一对照 MATLAB 1.7 / 2.3 / 3.3 / 4.3。
读取的表：分区机组信息表[生物质]、检修计划（Type=生物质 行）。
与核电同族：不优化经济启停（u_bio=1-m_bio 由检修输入决定），只有 1 个
决策变量 P_bio；线性 GEN_COST + FOM 常数；不进 CO2/备用。
差异：检修边界爬坡用 SU/SD·m 放松（非 M=Pmax）；另有年度电量预算行。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
import gurobipy as gp

import config
from power_opt.reader import _read, _zstr, Inputs


@dataclass
class BiomassInputs:
    """MATLAB 1.7 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_bio: int
    UnitKey_bio: list
    area_bio: np.ndarray           # 机组 → 分区（0 基）
    P_bio_rated: np.ndarray        # 额定容量，仅固定运维基数（MATLAB 235）
    P_bio_max: np.ndarray; P_bio_min: np.ndarray
    m_bio: np.ndarray              # 检修：1 检修，0 可用（N_bio×T）
    c_bio_FOM: np.ndarray          # 本周期元/MW
    c_bio_gen: np.ndarray          # 线性单位发电成本，元/MWh（MATLAB 236）
    P_bio_0: np.ndarray
    RU_bio: np.ndarray; RD_bio: np.ndarray; SU_bio: np.ndarray; SD_bio: np.ndarray
    m_bio_0: np.ndarray            # 周期前检修状态，0/1（MATLAB 245）
    E_bio_limit: np.ndarray        # 本次可用发电量预算，MWh（MATLAB 254）
    Delta_t: float                 # 时段长度，h（MATLAB 795 用）


def load_biomass(inp: Inputs) -> BiomassInputs:
    """镜像 MATLAB 1.7 节（226-254）。缺列缺值即报错。"""
    T = inp.time.T
    mtn_bio = inp.maintenance[inp.maintenance["Type"].astype(str) == "生物质"]

    req_bio = ["UnitKey", "SUBSTR(C.ID，12)", "CAP", "DERATE_RATE", "MIN_POWER_GEN", "GEN_COST",
               "INIT_POWER", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN", "START_RAMP",
               "STOP_RAMP", "INIT_MAINTENANCE", "FOM_COST_PERIOD", "ENERGY_LIMIT_MWH"]
    bioData = _read(config.UNIT_FILE, "生物质", req_bio)
    N_bio = len(bioData)
    zone_bio = [_zstr(v) for v in bioData["SUBSTR(C.ID，12)"]]
    areaPos = {a: i for i, a in enumerate(inp.areas.AreaID)}
    if miss := [z for z in zone_bio if z not in areaPos]:
        raise KeyError(f"生物质分区编号 {set(miss)} 不在分区表中")
    area_bio = np.array([areaPos[z] for z in zone_bio])
    f = lambda c: bioData[c].to_numpy(float)
    UnitKey_bio = bioData["UnitKey"].astype(str).tolist()
    # MATLAB 235-236：额定容量只作固定运维基数；出力上限=额定×(1-受阻率)
    P_bio_rated = f("CAP")
    P_bio_max = P_bio_rated * (1.0 - f("DERATE_RATE")); P_bio_min = f("MIN_POWER_GEN")
    c_bio_gen = inp.econ.costScale * f("GEN_COST")
    P_bio_0 = f("INIT_POWER"); RU_bio = f("RAMP_LIMIT_UP"); RD_bio = f("RAMP_LIMIT_DOWN")
    SU_bio = f("START_RAMP"); SD_bio = f("STOP_RAMP")
    m_bio_0 = f("INIT_MAINTENANCE")
    c_bio_FOM = inp.econ.fixedCostScale * f("FOM_COST_PERIOD")
    E_bio_limit = f("ENERGY_LIMIT_MWH")

    m_bio = np.zeros((N_bio, T))
    keyPos = {k: i for i, k in enumerate(UnitKey_bio)}
    t_prev = inp.time.Time[0] - pd.Timedelta(hours=1)   # 起点前 1 小时（MATLAB 258）
    for _, plan in mtn_bio.iterrows():
        key = str(plan["UnitKey"])
        if key not in keyPos:
            raise KeyError(f"检修计划机组 {key} 不在生物质表中（不猜、不忽略）")
        periods = (inp.time.Time >= plan["StartTime"]) & (inp.time.Time < plan["EndTime"])
        m_bio[keyPos[key], periods] = 1
        # MATLAB 258-261：起点前 1 小时已在检修窗口 → 初值覆盖为检修态、功率 0
        if plan["StartTime"] <= t_prev < plan["EndTime"]:
            m_bio_0[keyPos[key]] = 1.0
            P_bio_0[keyPos[key]] = 0.0

    for name, arr in [("P_bio_rated", P_bio_rated), ("P_bio_max", P_bio_max), ("P_bio_min", P_bio_min), ("m_bio", m_bio)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查生物质输入表")

    return BiomassInputs(
        N_bio=N_bio, UnitKey_bio=UnitKey_bio, area_bio=area_bio,
        P_bio_rated=P_bio_rated,
        P_bio_max=P_bio_max, P_bio_min=P_bio_min, m_bio=m_bio, c_bio_FOM=c_bio_FOM,
        c_bio_gen=c_bio_gen, P_bio_0=P_bio_0, RU_bio=RU_bio, RD_bio=RD_bio,
        SU_bio=SU_bio, SD_bio=SD_bio, m_bio_0=m_bio_0, E_bio_limit=E_bio_limit,
        Delta_t=inp.time.Delta_t,
    )


# ------------------- 2.3 决策变量（MATLAB 603-604 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class BiomassVars:
    """MATLAB 2.3 节决策变量的镜像，共 1 个。"""
    P_bio: gp.MVar        # 实际出力，MW


def add_vars(m: gp.Model, bio: BiomassInputs) -> BiomassVars:
    """连续变量 lb=-inf（镜像 sdpvar 无界默认），界限由 3.3 节约束钉。"""
    N, T = bio.N_bio, bio.m_bio.shape[1]
    P_bio = m.addMVar((N, T), lb=-INF, name="P_bio")                     # MATLAB 604
    return BiomassVars(P_bio=P_bio)


# ------------------- 3.3 生物质约束（MATLAB 781-795 行） -------------------

def add_constraints(m: gp.Model, bio: BiomassInputs, var: BiomassVars) -> None:
    """一比一对照 MATLAB 3.3 节。行数账本（T=24, N=13 → 1,261 行）：
    4 个 N×T 块（Pmin·u<=P、P<=Pmax·u、爬坡上、爬坡下）1,248 + 电量预算 13。
    非零元账本（T=24 → 2,158）：Pmin 312、Pmax 312（右端全常数）、
    爬坡上/下各 611（首列 1 元——prev 是常数 P_bio_0；其余 2 元，右端
    u_bio_previous/m_bio_previous 全常数）、电量 312（每台一行、24 元）。
    数据特性（已核）：m_bio 全 0（检修不在 1 月窗口）→ u_bio 全 1；
    E_bio_limit 是年度量级（百万 MWh），24h 远不紧。
    对账来源：MATLAB log 减法 全模型行 - 气电轮行 - 核电行（已命中）。"""
    N, T = bio.N_bio, bio.m_bio.shape[1]
    P = var.P_bio
    u_bio = 1 - bio.m_bio                                     # MATLAB 782，常数矩阵

    # ---- MATLAB 783-784：P_min·u_bio <= P_bio <= P_max·u_bio（右端全常数）----
    PminU = np.repeat(bio.P_bio_min[:, None], T, axis=1) * u_bio    # repmat(P_min).*u
    PmaxU = np.repeat(bio.P_bio_max[:, None], T, axis=1) * u_bio    # repmat(P_max).*u
    m.addConstr(PminU <= P, name="Pmin")                                # 783
    m.addConstr(P <= PmaxU, name="Pmax")                                # 784

    # ---- MATLAB 785-788：previous 拼接（首列用初值）----
    m_prev = np.concatenate([bio.m_bio_0[:, None], bio.m_bio[:, :-1]], axis=1)      # 786
    u_prev = 1 - m_prev                                                 # 788，常数
    # ---- MATLAB 789-792：爬坡，检修边界用 SU/SD·m_previous 放松（右端全常数）----
    RU = np.repeat(bio.RU_bio[:, None], T, axis=1) * u_prev             # repmat(RU).*u_prev
    SU = np.repeat(bio.SU_bio[:, None], T, axis=1) * m_prev             # repmat(SU).*m_prev
    RD = np.repeat(bio.RD_bio[:, None], T, axis=1) * u_bio              # repmat(RD).*u_bio
    SD = np.repeat(bio.SD_bio[:, None], T, axis=1) * bio.m_bio          # repmat(SD).*m_bio
    P0 = bio.P_bio_0[:, None]
    m.addConstr(P[:, 0:1] - P0 <= RU[:, 0:1] + SU[:, 0:1], name="ramp_up_t0")   # 789-790 首时段
    m.addConstr(P0 - P[:, 0:1] <= RD[:, 0:1] + SD[:, 0:1], name="ramp_dn_t0")   # 791-792 首时段
    if T > 1:
        m.addConstr(P[:, 1:] - P[:, :-1] <= RU[:, 1:] + SU[:, 1:], name="ramp_up")
        m.addConstr(P[:, :-1] - P[:, 1:] <= RD[:, 1:] + SD[:, 1:], name="ramp_dn")

    # ---- MATLAB 795：年度/本次周期可用燃料对应的累计发电量上限，每台一行 ----
    for i in range(N):
        m.addConstr(gp.quicksum(P[i, :]) * bio.Delta_t <= bio.E_bio_limit[i],
                    name=f"energy_{i}")


if __name__ == "__main__":
    import power_opt.reader as reader
    inp = reader.load_inputs()
    bio = load_biomass(inp)
    print('输入自检: N_bio=%d, m_bio sum=%s, FOM常数=%.2f'
          % (bio.N_bio, bio.m_bio.sum(), float(np.sum(bio.c_bio_FOM * bio.P_bio_rated))))  # MATLAB 1037 基数=额定

    m = gp.Model()
    Bv = add_vars(m, bio)
    m.update()
    N, T = bio.N_bio, bio.m_bio.shape[1]
    exp_cols = N*T                        # 1 个 N×T 块
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=0  实际=%d' % m.NumBinVars)
    assert m.NumVars == exp_cols and m.NumBinVars == 0, '变量清单与推导不符'
    print('生物质变量清单核对通过')

    add_constraints(m, bio, Bv)
    m.update()
    exp_rows = 4*N*T + N                  # 4 个 N×T 块 + 电量 N 行
    exp_nz = 2*N*T + 2*(N + (T-1)*N*2) + N*T   # Pmin/Pmax + 爬坡 + 电量
    print('生物质约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    print('生物质非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    assert m.getAttr('NumNZs') == exp_nz, '非零元与手推账本不符'
    print('生物质约束账本核对通过')
