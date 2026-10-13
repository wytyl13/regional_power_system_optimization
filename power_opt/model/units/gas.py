#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/23 15:20
@Author  : weiyutao
@File    : gas.py
气电模块：一比一对照 MATLAB 1.4 / 2.2 / 3.2 / 4.2。
读取的表：分区机组信息表[气电]、检修计划（Type=气电 行）。
与煤电的差异（对照 MATLAB）：线性成本 GEN_COST（无六节点/分段/SO2/NOX/烟尘），
排放是因子 CO2_FACTOR×P（无 CO2 变量，进平衡/省约束时现算表达式）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
import gurobipy as gp

import config
from reader import _read, _zstr, Inputs
from model.units.thermal import _col_repmat


@dataclass
class GasInputs:
    """MATLAB 1.4 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_gas: int
    UnitKey_gas: list
    name_gas: list                # 机组名（八~十一节分析表用，MATLAB gasData.NAME）
    area_gas: np.ndarray           # 机组 → 分区（0 基）
    P_gas_rated: np.ndarray        # 额定容量，仅固定运维基数（MATLAB 137）
    P_gas_max: np.ndarray; P_gas_min: np.ndarray
    m_gas: np.ndarray              # 检修：1 检修，0 可用（N_gas×T）
    c_gas_FOM: np.ndarray          # 本周期元/MW
    u_gas_0: np.ndarray
    c_gas_start: np.ndarray; c_gas_stop: np.ndarray
    c_gas_gen: np.ndarray          # 线性单位发电成本，元/MWh（MATLAB 152）
    P_gas_0: np.ndarray
    RU_gas: np.ndarray; RD_gas: np.ndarray; SU_gas: np.ndarray; SD_gas: np.ndarray
    CO2_gas_factor: np.ndarray     # t/MWh（MATLAB 169）
    T_gas_up: np.ndarray; T_gas_down: np.ndarray; T_gas_initial: np.ndarray


def load_gas(inp: Inputs) -> GasInputs:
    """镜像 MATLAB 1.4 节（138-172）。缺列缺值即报错。"""
    T = inp.T
    # ---- 检修计划：领取 Type=气电 的行（MATLAB 161-166，区间左闭右开）----
    mtn_gas = inp.maintenance[inp.maintenance["Type"].astype(str) == "气电"]

    # ---- 1.4 气电表（MATLAB 138-160）----
    req_gas = ["UnitKey", "SUBSTR(C.ID，12)", "CAP", "DERATE_RATE", "MIN_POWER_GEN", "INIT_STATUS",
               "COST_START_UP", "COST_SHUT_DOWN", "GEN_COST", "INIT_POWER",
               "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN", "START_RAMP", "STOP_RAMP",
               "CO2_FACTOR", "MIN_UP_TIME", "MIN_DOWN_TIME", "INIT_HOURS",
               "FOM_COST_PERIOD", "NAME"]
    gasData = _read(config.UNIT_FILE, "气电", req_gas)
    N_gas = len(gasData)
    zone_gas = [_zstr(v) for v in gasData["SUBSTR(C.ID，12)"]]
    areaPos = {a: i for i, a in enumerate(inp.AreaID)}
    if miss := [z for z in zone_gas if z not in areaPos]:
        raise KeyError(f"气电分区编号 {set(miss)} 不在分区表中")
    area_gas = np.array([areaPos[z] for z in zone_gas])
    f = lambda c: gasData[c].to_numpy(float)
    UnitKey_gas = gasData["UnitKey"].astype(str).tolist()
    name_gas = gasData["NAME"].astype(str).tolist()               # 八~十一节分析表用
    # MATLAB 137-138：额定容量只作固定运维基数；出力上限=额定×(1-受阻率)
    P_gas_rated = f("CAP")
    P_gas_max = P_gas_rated * (1.0 - f("DERATE_RATE")); P_gas_min = f("MIN_POWER_GEN")
    u_gas_0 = f("INIT_STATUS")
    c_gas_start = inp.costScale * f("COST_START_UP"); c_gas_stop = inp.costScale * f("COST_SHUT_DOWN")
    c_gas_gen = inp.costScale * f("GEN_COST")
    P_gas_0 = f("INIT_POWER"); RU_gas = f("RAMP_LIMIT_UP"); RD_gas = f("RAMP_LIMIT_DOWN")
    SU_gas = f("START_RAMP"); SD_gas = f("STOP_RAMP")
    CO2_gas_factor = f("CO2_FACTOR")
    T_gas_up = f("MIN_UP_TIME"); T_gas_down = f("MIN_DOWN_TIME"); T_gas_initial = f("INIT_HOURS")
    c_gas_FOM = inp.fixedCostScale * f("FOM_COST_PERIOD")

    m_gas = np.zeros((N_gas, T))
    keyPos = {k: i for i, k in enumerate(UnitKey_gas)}
    t_prev = inp.Time[0] - pd.Timedelta(hours=1)   # 起点前 1 小时（MATLAB 163）
    for _, plan in mtn_gas.iterrows():
        key = str(plan["UnitKey"])
        if key not in keyPos:
            raise KeyError(f"检修计划机组 {key} 不在气电表中（不猜、不忽略）")
        periods = (inp.Time >= plan["StartTime"]) & (inp.Time < plan["EndTime"])
        m_gas[keyPos[key], periods] = 1
        # MATLAB 163-167：起点前 1 小时已在检修窗口 → 初值覆盖为停机，累计已停机小时
        # （T_gas_initial 先读 INIT_HOURS（157 行），在此被覆盖）
        if plan["StartTime"] <= t_prev < plan["EndTime"]:
            i = keyPos[key]
            u_gas_0[i] = 0.0
            P_gas_0[i] = 0.0
            T_gas_initial[i] = (inp.Time[0] - plan["StartTime"]).total_seconds() / 3600.0

    for name, arr in [("P_gas_rated", P_gas_rated), ("P_gas_max", P_gas_max), ("P_gas_min", P_gas_min), ("m_gas", m_gas),
                      ("CO2_gas_factor", CO2_gas_factor)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查气电输入表")

    return GasInputs(
        N_gas=N_gas, UnitKey_gas=UnitKey_gas, name_gas=name_gas, area_gas=area_gas,
        P_gas_rated=P_gas_rated,
        P_gas_max=P_gas_max, P_gas_min=P_gas_min, m_gas=m_gas, c_gas_FOM=c_gas_FOM,
        u_gas_0=u_gas_0, c_gas_start=c_gas_start, c_gas_stop=c_gas_stop,
        c_gas_gen=c_gas_gen, P_gas_0=P_gas_0, RU_gas=RU_gas, RD_gas=RD_gas,
        SU_gas=SU_gas, SD_gas=SD_gas, CO2_gas_factor=CO2_gas_factor,
        T_gas_up=T_gas_up, T_gas_down=T_gas_down, T_gas_initial=T_gas_initial,
    )


# ------------------- 2.2 决策变量（MATLAB 591-600 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class GasVars:
    """MATLAB 2.2 节决策变量的镜像，共 8 个。"""
    P_gas: gp.MVar        # 实际出力，MW
    u_gas: gp.MVar        # 运行状态，0/1
    v_gas: gp.MVar        # 启动状态，0/1
    w_gas: gp.MVar        # 停机状态，0/1
    V_gas_count: gp.MVar  # 时段边界累计启动次数（N×(T+1)）
    W_gas_count: gp.MVar  # 时段边界累计停机次数（N×(T+1)）
    R_gas_hot: gp.MVar    # 热备用，MW
    R_gas_cold: gp.MVar   # 冷备用，MW


def add_vars(m: gp.Model, gas: GasInputs) -> GasVars:
    """连续变量 lb=-inf（镜像 sdpvar 无界默认），界限由 3.2 节约束钉。"""
    N, T = gas.N_gas, gas.m_gas.shape[1]
    P_gas       = m.addMVar((N, T),     lb=-INF, name="P_gas")              # MATLAB 592
    u_gas       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="u_gas")  # 593
    v_gas       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="v_gas")  # 594
    w_gas       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="w_gas")  # 595
    V_gas_count = m.addMVar((N, T + 1), lb=-INF, name="V_gas_count")        # 597
    W_gas_count = m.addMVar((N, T + 1), lb=-INF, name="W_gas_count")        # 598
    R_gas_hot   = m.addMVar((N, T),     lb=-INF, name="R_gas_hot")          # 599
    R_gas_cold  = m.addMVar((N, T),     lb=-INF, name="R_gas_cold")         # 600
    return GasVars(P_gas=P_gas, u_gas=u_gas, v_gas=v_gas, w_gas=w_gas,
                   V_gas_count=V_gas_count, W_gas_count=W_gas_count,
                   R_gas_hot=R_gas_hot, R_gas_cold=R_gas_cold)


# ------------------- 3.2 气电约束（MATLAB 741-778 行） -------------------

def add_constraints(m: gp.Model, gas: GasInputs, var: GasVars) -> None:
    """一比一对照 MATLAB 3.2 节。行数账本（T=24 → 44,888 行）：
    15 个 N×T 块 44,640 + count 初值 248；锁定行 0（INIT_HOURS 全满足，已核数据）。
    非零元账本（T=24 → 109,740）：logic 11,780（首列3元/其余4元）、vw 5,952、
    um 2,976、Pmin 5,952、Pmax_hot 8,928、Rhotlb+Rcoldlb 5,952、Rcold_u 5,952、
    Rcold_m 2,976（右端全常数）、爬坡上/下各 11,656（首列2元/其余4元）、
    Vinit+Winit 248、Vrec+Wrec 17,856、minup+mindn 17,856。
    对账来源：MATLAB log 减法 180,821 - 135,933 = 44,888。"""
    N, T = gas.N_gas, gas.m_gas.shape[1]
    dt = gas.m_gas                     # 检修 0/1 矩阵
    u, v, w = var.u_gas, var.v_gas, var.w_gas
    P, R_hot, R_cold = var.P_gas, var.R_gas_hot, var.R_gas_cold

    # ---- MATLAB 743-744：u - u_prev = v - w；首列 prev = u_gas_0 ----
    m.addConstr(u[:, 0] - gas.u_gas_0 == v[:, 0] - w[:, 0], name="logic_t0")
    if T > 1:
        m.addConstr(u[:, 1:] - u[:, :-1] == v[:, 1:] - w[:, 1:], name="logic")

    # ---- MATLAB 745：v + w <= 1；u <= 1 - m ----
    m.addConstr(v + w <= 1, name="vw")                                # 745
    m.addConstr(u <= 1 - dt, name="um")                               # 745

    # ---- MATLAB 746：P_min·u <= P ----
    PminU = _col_repmat(gas, gas.P_gas_min, T)
    m.addConstr(PminU * u <= P, name="Pmin")                          # 746

    # ---- MATLAB 748：P + R_hot <= Pmax·u ----
    PmaxU = _col_repmat(gas, gas.P_gas_max, T)
    m.addConstr(P + R_hot <= PmaxU * u, name="Pmax_hot")              # 748

    # ---- MATLAB 749：R_hot >= 0；R_cold >= 0 ----
    m.addConstr(R_hot >= 0, name="Rhotlb")                            # 749
    m.addConstr(R_cold >= 0, name="Rcoldlb")                          # 749

    # ---- MATLAB 750/751：R_cold <= Pmax(1-u)；R_cold <= Pmax(1-m) ----
    m.addConstr(R_cold <= PmaxU * (1 - u), name="Rcold_u")            # 750
    m.addConstr(R_cold <= PmaxU * (1 - dt), name="Rcold_m")           # 751

    # ---- MATLAB 752-756：爬坡。首时段 prev = P_gas_0 / u_gas_0，不经过 concatenate ----
    RU = _col_repmat(gas, gas.RU_gas, T); SU = _col_repmat(gas, gas.SU_gas, T)
    RD = _col_repmat(gas, gas.RD_gas, T); SD = _col_repmat(gas, gas.SD_gas, T)
    u0 = gas.u_gas_0[:, None]; P0 = gas.P_gas_0[:, None]     # N×1 初始状态/功率
    m.addConstr(P[:, 0:1] - P0 <= RU[:, 0:1] * u0 + SU[:, 0:1] * (u[:, 0:1] - u0),
                name="ramp_up_t0")                            # 753-754 首时段
    m.addConstr(P0 - P[:, 0:1] <= RD[:, 0:1] * u[:, 0:1] + SD[:, 0:1] * (u0 - u[:, 0:1]),
                name="ramp_dn_t0")                            # 755-756 首时段
    if T > 1:
        u_prev = u[:, :-1]; P_prev = P[:, :-1]
        m.addConstr(P[:, 1:] - P_prev <= RU[:, 1:] * u_prev
                    + SU[:, 1:] * (u[:, 1:] - u_prev), name="ramp_up")   # 其余时段
        m.addConstr(P_prev - P[:, 1:] <= RD[:, 1:] * u[:, 1:]
                    + SD[:, 1:] * (u_prev - u[:, 1:]), name="ramp_dn")

    # ---- MATLAB 759-761：累计启停量递推 ----
    Vc, Wc = var.V_gas_count, var.W_gas_count
    m.addConstr(Vc[:, 0] == 0, name="Vinit"); m.addConstr(Wc[:, 0] == 0, name="Winit")  # 759
    m.addConstr(Vc[:, 1:] - Vc[:, :-1] == v, name="Vrec")             # 760
    m.addConstr(Wc[:, 1:] - Wc[:, :-1] == w, name="Wrec")             # 761

    # ---- MATLAB 762-778：最小开停机。初始剩余义务 remaining 全为 0（已核数据），锁定行不触发 ----
    # remaining = clip(u0*T_up + (1-u0)*T_down - init_hours, 0, None)，>0 才建锁定行，镜像 MATLAB if 守卫。
    rem = np.clip(gas.u_gas_0 * gas.T_gas_up + (1 - gas.u_gas_0) * gas.T_gas_down
                  - gas.T_gas_initial, 0, None)
    for i in range(N):
        if rem[i] > 0:
            m.addConstr(u[i, :min(T, int(rem[i]))] == gas.u_gas_0[i],
                        name=f"lock_{i}")                             # 765

    # T_up/T_down 全>0（96/168 共 100 台、8/8 共 24 台，已核数据），MATLAB if 守卫全触发，整块直建。
    firstU = np.maximum(0, np.arange(T) - gas.T_gas_up.astype(int)[:, None] + 1)   # MATLAB 771
    firstD = np.maximum(0, np.arange(T) - gas.T_gas_down.astype(int)[:, None] + 1) # MATLAB 775
    m.addConstr(Vc[:, 1:] - Vc[np.arange(N)[:, None], firstU] <= u, name="minup")   # 772
    m.addConstr(Wc[:, 1:] - Wc[np.arange(N)[:, None], firstD] <= 1 - u, name="mindn")  # 776


if __name__ == "__main__":
    import numpy as np
    import reader
    inp = reader.load_inputs()
    gas = load_gas(inp)
    print('输入自检: N_gas=%d, m_gas sum=%s, FOM常数=%.2f'
          % (gas.N_gas, gas.m_gas.sum(), float(np.sum(gas.c_gas_FOM * gas.P_gas_rated))))  # MATLAB 1031 基数=额定

    m = gp.Model()
    Gv = add_vars(m, gas)
    m.update()
    N, T = gas.N_gas, gas.m_gas.shape[1]
    exp_cols = N*T*6 + N*(T+1)*2      # 6个N×T块 + 2个N×(T+1)
    exp_bins = N*T*3                  # u/v/w
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (exp_bins, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == exp_bins, '变量清单与推导不符'
    print('气电变量清单核对通过')

    add_constraints(m, gas, Gv)
    m.update()
    exp_rows = 15*N*T + 2*N           # 15个N×T块 + count初值2N；锁定行0（INIT_HOURS已核）
    exp_nz = 37*N*T - 3*N             # 结构化账本：江苏 N=124,T=24→109740、海南 N=3,T=168→18639 均吻合
    # （rem>0 触发锁定行时行数会超出公式，MATLAB 同构造；默认起点已核无锁定行）
    print('气电约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    print('气电非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.NumConstrs == exp_rows, '行数与结构化账本不符'
    assert m.getAttr('NumNZs') == exp_nz, '非零元与结构化账本不符'
    print('气电约束账本核对通过')
