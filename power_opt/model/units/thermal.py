#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/21 18:00
@Author  : weiyutao
@File    : thermal.py
煤电模块：一比一对照 MATLAB 1.3 / 2.1 / 3.1 / 4.1。
读取的表：分区机组信息表[煤电]、算例系统参数[煤电碳排节点]、检修计划（Type=煤电 行）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
import gurobipy as gp

import config
from power_opt.reader import _read, _zstr, Inputs


@dataclass
class CoalInputs:
    """MATLAB 1.3 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_th: int                                   # 机组台数
    UnitKey_th: list                            # 分区唯一编号                    
    name_th: list                               # 机组名
    area_th: np.ndarray                         # 机组 → 分区（0 基）
    P_th_rated: np.ndarray                      # 额定容量，仅固定运维基数
    P_th_max: np.ndarray; P_th_min: np.ndarray  # 非供暖期可用上限，MW / 单机最小技术出力，MW
    m_th: np.ndarray                            # 检修：1 检修，0 可用（N_th×T）
    c_th_FOM: np.ndarray                        # 本周期元/MW
    u_th_0: np.ndarray                          # 周期前运行状态，0/1
    c_th_start: np.ndarray; c_th_stop: np.ndarray # 单次启动成本，元
    a_th: np.ndarray; b_th: np.ndarray; c_th: np.ndarray # 燃料成本二次项系数
    T_th_up: np.ndarray; T_th_down: np.ndarray; T_th_initial: np.ndarray # 最小连续开机小时数 / 最小连续停机小时数 / 周期前已保持初始状态的小时数
    P_th_0: np.ndarray # 周期前功率，MW
    RU_th: np.ndarray; RD_th: np.ndarray; SU_th: np.ndarray; SD_th: np.ndarray
    SO2_th_factor: np.ndarray; NOX_th_factor: np.ndarray; dust_th_factor: np.ndarray
    CO2_th_node: np.ndarray        # N_th×6，t/h
    P_th_node: np.ndarray           # N_th×6，MW（MATLAB 105）
    F_th_node: np.ndarray           # N_th×6，元/h（MATLAB 106）


def load_coal(inp: Inputs) -> CoalInputs:
    """镜像 MATLAB 1.3 节（87-135）+ 六节点派生（105-106）。缺列缺值即报错。"""
    T = inp.time.T
    # ---- 检修计划：领取 Type=煤电 的行（MATLAB 118-123，区间左闭右开）----
    mtn_coal = inp.maintenance[inp.maintenance["Type"].astype(str) == "煤电"]

    # ---- 1.3 煤电表（MATLAB 87-123）----
    """
    CAP：容量
    DERATE_RATE：非供暖受阻率
    MIN_POWER_GEN：最小技术出力
    INIT_STATUS：周期前运行状态
    COST_START_UP：启动成本
    COST_SHUT_DOWN：停机成本
    COST_A：燃料二次项（耗煤成本函数的二次系数）
    COST_B：燃料一次项（耗煤成本函数的一次系数）
    COST_C：燃料常数项（耗煤成本函数的常数项）
    MIN_UP_TIME：最小连续开机时间
    MIN_DOWN_TIME：最小连续停机时间
    INIT_HOURS：初始状态已持续时长
    INIT_POWER：周期前功率（初始出力）
    RAMP_LIMIT_UP：向上爬坡（允许的功率上升速率）
    RAMP_LIMIT_DOWN：向下爬坡（允许的功率下降速率）
    START_RAMP：启动允许功率变化
    STOP_RAMP：停机允许功率变化
    FOM_COST_PERIOD：年度功率运维成本
    SO2_FACTOR：SO2排放因子
    NOX_FACTOR：NOx排放因子
    DUST_FACTOR：烟尘排放因子
    """
    req_th = ["UnitKey", "SUBSTR(C.ID，12)", "CAP", "DERATE_RATE", "MIN_POWER_GEN", "INIT_STATUS",
              "COST_START_UP", "COST_SHUT_DOWN", "COST_A", "COST_B", "COST_C",
              "MIN_UP_TIME", "MIN_DOWN_TIME", "INIT_HOURS", "INIT_POWER",
              "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN", "START_RAMP", "STOP_RAMP",
              "FOM_COST_PERIOD", "SO2_FACTOR", "NOX_FACTOR", "DUST_FACTOR", "NAME"]
    thData = _read(config.UNIT_FILE, "煤电", req_th)
    N_th = len(thData)
    zone_th = [_zstr(v) for v in thData["SUBSTR(C.ID，12)"]]
    areaPos = {a: i for i, a in enumerate(inp.areas.AreaID)}
    if miss := [z for z in zone_th if z not in areaPos]:
        raise KeyError(f"煤电分区编号 {set(miss)} 不在分区表中")
    area_th = np.array([areaPos[z] for z in zone_th])
    f = lambda c: thData[c].to_numpy(float)
    UnitKey_th = thData["UnitKey"].astype(str).tolist()
    name_th = thData["NAME"].astype(str).tolist()                 # 八~十一节分析表用

    # MATLAB 81-82：额定容量只作固定运维基数；出力上限=额定×(1-受阻率)
    P_th_rated = f("CAP")
    P_th_max = P_th_rated * (1.0 - f("DERATE_RATE")); P_th_min = f("MIN_POWER_GEN")
    u_th_0 = f("INIT_STATUS")
    c_th_start = inp.econ.costScale * f("COST_START_UP"); c_th_stop = inp.econ.costScale * f("COST_SHUT_DOWN")
    a_th = inp.econ.costScale * f("COST_A"); b_th = inp.econ.costScale * f("COST_B"); c_th = inp.econ.costScale * f("COST_C")
    T_th_up = f("MIN_UP_TIME"); T_th_down = f("MIN_DOWN_TIME"); T_th_initial = f("INIT_HOURS")
    P_th_0 = f("INIT_POWER"); RU_th = f("RAMP_LIMIT_UP"); RD_th = f("RAMP_LIMIT_DOWN")
    SU_th = f("START_RAMP"); SD_th = f("STOP_RAMP")
    c_th_FOM = inp.econ.fixedCostScale * f("FOM_COST_PERIOD")
    SO2_th_factor = f("SO2_FACTOR"); NOX_th_factor = f("NOX_FACTOR"); dust_th_factor = f("DUST_FACTOR")

    m_th = np.zeros((N_th, T))
    keyPos = {k: i for i, k in enumerate(UnitKey_th)}
    t_prev = inp.time.Time[0] - pd.Timedelta(hours=1)   # 起点前 1 小时（MATLAB 114）
    for _, plan in mtn_coal.iterrows():
        key = str(plan["UnitKey"])
        if key not in keyPos:
            raise KeyError(f"检修计划机组 {key} 不在煤电表中（不猜、不忽略）")
        periods = (inp.time.Time >= plan["StartTime"]) & (inp.time.Time < plan["EndTime"])
        m_th[keyPos[key], periods] = 1
        # MATLAB 114-118：起点前 1 小时已在检修窗口 → 初值覆盖为停机，累计已停机小时
        if plan["StartTime"] <= t_prev < plan["EndTime"]:
            i = keyPos[key]
            u_th_0[i] = 0.0
            P_th_0[i] = 0.0
            T_th_initial[i] = (inp.time.Time[0] - plan["StartTime"]).total_seconds() / 3600.0

    # ---- 六节点排放（MATLAB 125-135）：每台 6 行，NodeIndex 必须是 0..5 ----
    em = _read(config.SYSTEM_FILE, "煤电碳排节点", ["UnitKey", "NodeIndex", "CO2Rate_t_h"])
    CO2_th_node = np.full((N_th, 6), np.nan)
    for i, key in enumerate(UnitKey_th):
        rows = em[em["UnitKey"].astype(str) == key]
        if len(rows) != 6:
            raise KeyError(f"煤电 {key} 碳排节点应为 6 行，实际 {len(rows)} 行")
        nodes = {int(n): float(v) for n, v in zip(rows["NodeIndex"], rows["CO2Rate_t_h"])}
        if set(nodes) != set(range(6)):
            raise KeyError(f"煤电 {key} NodeIndex 应为 0..5，实际 {sorted(nodes)}")
        CO2_th_node[i, :] = [nodes[k] for k in range(6)]

    # ---- 六节点燃料成本派生（MATLAB 105-106）----
    frac = (np.arange(6) / 5.0)[None, :]                       # 0,0.2,...,1
    P_th_node = P_th_min[:, None] + (P_th_max - P_th_min)[:, None] * frac
    F_th_node = a_th[:, None] * P_th_node ** 2 + b_th[:, None] * P_th_node + c_th[:, None]

    for name, arr in [("P_th_rated", P_th_rated), ("P_th_max", P_th_max), ("P_th_min", P_th_min),
                      ("m_th", m_th),
                      ("CO2_th_node", CO2_th_node), ("P_th_node", P_th_node), ("F_th_node", F_th_node)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查煤电输入表")

    return CoalInputs(
        N_th=N_th, UnitKey_th=UnitKey_th, name_th=name_th, area_th=area_th,
        P_th_rated=P_th_rated,
        P_th_max=P_th_max, P_th_min=P_th_min, m_th=m_th, c_th_FOM=c_th_FOM,
        u_th_0=u_th_0, c_th_start=c_th_start, c_th_stop=c_th_stop,
        a_th=a_th, b_th=b_th, c_th=c_th, T_th_up=T_th_up, T_th_down=T_th_down,
        T_th_initial=T_th_initial, P_th_0=P_th_0, RU_th=RU_th, RD_th=RD_th,
        SU_th=SU_th, SD_th=SD_th, SO2_th_factor=SO2_th_factor, NOX_th_factor=NOX_th_factor,
        dust_th_factor=dust_th_factor, CO2_th_node=CO2_th_node,
        P_th_node=P_th_node, F_th_node=F_th_node,
    )


# ------------------- 2.1 决策变量（MATLAB 570-588 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class ThermalVars:
    """MATLAB 2.1 节决策变量的镜像。
    lambda_th/segment_th 合并为 (N,6,T)/(N,5,T) 整块，对应 MATLAB 172 个 cell；
    数学行为等价（下标 [i,:,t] == lambda_th{i}(:,t)）。"""
    P_th: gp.MVar        # 机组实际出力，MW
    u_th: gp.MVar        # 运行状态，0/1
    v_th: gp.MVar        # 启动状态，0/1
    w_th: gp.MVar        # 停机状态，0/1
    V_th_count: gp.MVar  # 时段边界累计启动次数（N×(T+1)）
    W_th_count: gp.MVar  # 时段边界累计停机次数（N×(T+1)）
    R_th_hot: gp.MVar    # 热备用，MW
    R_th_cold: gp.MVar   # 冷备用，MW
    F_th: gp.MVar        # 燃料成本速率，元/h
    CO2_th: gp.MVar      # 排放速率，t/h
    lambda_th: gp.MVar   # 六节点权重 (N,6,T)
    segment_th: gp.MVar  # 五段选择，0/1 (N,5,T)


def add_vars(m: gp.Model, coal: CoalInputs) -> ThermalVars:
    """连续变量 lb=-inf（镜像 sdpvar 无界默认）；二值默认 [0,1]。
    界限交给 3.1 节约束行去钉，保证行数与 MATLAB 一致（见文件头说明）。"""
    N, T = coal.N_th, coal.m_th.shape[1]
    P_th       = m.addMVar((N, T),     lb=-INF, name="P_th")              # MATLAB 572
    u_th       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="u_th")  # 573
    v_th       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="v_th")  # 574
    w_th       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="w_th")  # 575
    V_th_count = m.addMVar((N, T + 1), lb=-INF, name="V_th_count")        # 577
    W_th_count = m.addMVar((N, T + 1), lb=-INF, name="W_th_count")        # 578
    R_th_hot   = m.addMVar((N, T),     lb=-INF, name="R_th_hot")          # 579
    R_th_cold  = m.addMVar((N, T),     lb=-INF, name="R_th_cold")         # 580
    F_th       = m.addMVar((N, T),     lb=-INF, name="F_th")              # 581
    CO2_th     = m.addMVar((N, T),     lb=-INF, name="CO2_th")            # 582
    lambda_th  = m.addMVar((N, 6, T),  lb=-INF, name="lambda_th")          # 586
    segment_th = m.addMVar((N, 5, T),  vtype=gp.GRB.BINARY, name="segment_th")  # 587
    return ThermalVars(P_th=P_th, u_th=u_th, v_th=v_th, w_th=w_th,
                       V_th_count=V_th_count, W_th_count=W_th_count,
                       R_th_hot=R_th_hot, R_th_cold=R_th_cold,
                       F_th=F_th, CO2_th=CO2_th,
                       lambda_th=lambda_th, segment_th=segment_th)


# ------------------- 3.1 煤电约束（MATLAB 686-737 行） -------------------

def _col_repmat(coal: CoalInputs, col: np.ndarray, T: int) -> np.ndarray:
    """repmat(x,1,T) 的等价：把每台机组的参数铺成 N×T 矩阵。"""
    return np.repeat(col[:, None], T, axis=1)


def _row_dot(lam2d: gp.MVar, node: np.ndarray):
    """占位（已弃用）：原计划做逐台点积表达式列表，实测 addConstr 矩阵化
    无法接受 list 运算，改为 add_constraints 内逐台 quicksum 直建。保留空壳
    防止外部引用报错。"""
    raise NotImplementedError


def add_constraints(m: gp.Model, coal: CoalInputs, var: ThermalVars) -> None:
    """一比一对照 MATLAB 3.1 节。行数账本（T=24 → 132,440 行）：
    22 个 N×T 块 90,816 + count 初值 344 + λ≥0 24,768 + λ桥 16,512。"""
    N, T = coal.N_th, coal.m_th.shape[1]
    dt = coal.m_th                     # 检修 0/1 矩阵
    u, v, w = var.u_th, var.v_th, var.w_th
    P, R_hot, R_cold = var.P_th, var.R_th_hot, var.R_th_cold

    # ---- MATLAB 688-689：u - u_prev = v - w；首列 prev = u_th_0 ----
    m.addConstr(u[:, 0] - coal.u_th_0 == v[:, 0] - w[:, 0], name="logic_t0")
    if T > 1:
        m.addConstr(u[:, 1:] - u[:, :-1] == v[:, 1:] - w[:, 1:], name="logic")

    # ---- MATLAB 690：v + w <= 1；u <= 1 - m ----
    m.addConstr(v + w <= 1, name="vw")                                # 690
    m.addConstr(u <= 1 - dt, name="um")                               # 690

    # ——MATLAB 704 行头："Constraints = [Constraints, R_th_hot>=0, R_th_cold>=0]"
    m.addConstr(R_hot >= 0, name="Rhotlb")                            # 694（一行拆两条）
    m.addConstr(R_cold >= 0, name="Rcoldlb")                          # 694

    # ---- MATLAB 691：P_min·u <= P ----
    PminU = _col_repmat(coal, coal.P_th_min, T)
    m.addConstr(PminU * u <= P, name="Pmin")                          # 691

    # ---- MATLAB 693：P + R_hot <= Pmax·u ----
    PmaxU = _col_repmat(coal, coal.P_th_max, T)
    m.addConstr(P + R_hot <= PmaxU * u, name="Pmax_hot")              # 693

    # ---- MATLAB 695/696：R_cold <= Pmax(1-u)；R_cold <= Pmax(1-m) ----
    m.addConstr(R_cold <= PmaxU * (1 - u), name="Rcold_u")            # 695
    m.addConstr(R_cold <= PmaxU * (1 - dt), name="Rcold_m")           # 696

    # ---- MATLAB 697-701：爬坡。首时段 prev = P_th_0 / u_th_0，不经过 concatenate ----
    RU = _col_repmat(coal, coal.RU_th, T); SU = _col_repmat(coal, coal.SU_th, T)
    RD = _col_repmat(coal, coal.RD_th, T); SD = _col_repmat(coal, coal.SD_th, T)
    u0 = coal.u_th_0[:, None]; P0 = coal.P_th_0[:, None]     # N×1 初始状态/功率
    m.addConstr(P[:, 0:1] - P0 <= RU[:, 0:1] * u0 + SU[:, 0:1] * (u[:, 0:1] - u0),
                name="ramp_up_t0")                            # 698-699 首时段
    m.addConstr(P0 - P[:, 0:1] <= RD[:, 0:1] * u[:, 0:1] + SD[:, 0:1] * (u0 - u[:, 0:1]),
                name="ramp_dn_t0")                            # 700-701 首时段
    if T > 1:
        u_prev = u[:, :-1]; P_prev = P[:, :-1]
        m.addConstr(P[:, 1:] - P_prev <= RU[:, 1:] * u_prev
                    + SU[:, 1:] * (u[:, 1:] - u_prev), name="ramp_up")   # 其余时段
        m.addConstr(P_prev - P[:, 1:] <= RD[:, 1:] * u[:, 1:]
                    + SD[:, 1:] * (u_prev - u[:, 1:]), name="ramp_dn")

    # ---- MATLAB 704-706：累计启停量递推 ----
    Vc, Wc = var.V_th_count, var.W_th_count
    m.addConstr(Vc[:, 0] == 0, name="Vinit"); m.addConstr(Wc[:, 0] == 0, name="Winit")  # 704
    m.addConstr(Vc[:, 1:] - Vc[:, :-1] == v, name="Vrec")             # 705
    m.addConstr(Wc[:, 1:] - Wc[:, :-1] == w, name="Wrec")             # 706

    # ---- MATLAB 707-723：最小开停机。初始剩余义务 remaining 全为 0（已核数据），锁定行不触发 ----
    # remaining = clip(u0*T_up + (1-u0)*T_down - init_hours, 0, None)，>0 才建锁定行，镜像 MATLAB if 守卫。
    rem = np.clip(coal.u_th_0 * coal.T_th_up + (1 - coal.u_th_0) * coal.T_th_down
                  - coal.T_th_initial, 0, None)
    for i in range(N):
        if rem[i] > 0:
            m.addConstr(u[i, :min(T, int(rem[i]))] == coal.u_th_0[i],
                        name=f"lock_{i}")                             # 710

    firstU = np.maximum(0, np.arange(T) - coal.T_th_up.astype(int)[:, None] + 1)   # MATLAB 716
    firstD = np.maximum(0, np.arange(T) - coal.T_th_down.astype(int)[:, None] + 1) # MATLAB 719
    m.addConstr(Vc[:, 1:] - Vc[np.arange(N)[:, None], firstU] <= u, name="minup")   # 717
    m.addConstr(Wc[:, 1:] - Wc[np.arange(N)[:, None], firstD] <= 1 - u, name="mindn")  # 721

    # ---- MATLAB 724-737：五段分段线性（SOS2 等价）----
    lam, seg = var.lambda_th, var.segment_th
    m.addConstr(lam >= 0, name="lam_lb")                              # 728
    m.addConstr(lam.sum(axis=1) == u, name="lam_sum")                 # 729  Σλ_k = u
    # P/F/CO2 = node·λ：N×T 个"行"约束，六节点混合权重（MATLAB 730-732）。
    # gurobipy 的 MVar@MVar 出 2 维，这里 λ 是 3 维，按机器维度循环第 3 维（时段）实现"每台每时刻一条"。
    # P/F/CO2 = node·λ：每台每时刻一条（MATLAB 730-732）。
    # gurobipy 的 3D MVar 矩阵乘按 N² 展开行（实测），故逐台 quicksum；
    # 4128 条/块 实测 7 秒级，一次性建 3 块。
    K = lam.shape[1]
    for t in range(T):
        lam_t = lam[:, :, t]                       # N×6 截面（2D MVar）
        for i in range(N):
            m.addConstr(P[i, t] == gp.quicksum(lam_t[i, k] * coal.P_th_node[i, k] for k in range(K)),
                        name=f"Pnode_{t}_{i}")       # 730
            m.addConstr(var.F_th[i, t] == gp.quicksum(lam_t[i, k] * coal.F_th_node[i, k] for k in range(K)),
                        name=f"Fnode_{t}_{i}")       # 731
            m.addConstr(var.CO2_th[i, t] == gp.quicksum(lam_t[i, k] * coal.CO2_th_node[i, k] for k in range(K)),
                        name=f"Cnode_{t}_{i}")       # 732
    m.addConstr(seg.sum(axis=1) == u, name="seg_sum")                 # 733
    m.addConstr(lam[:, 0, :] <= seg[:, 0, :], name="lam_seg0")        # 734
    m.addConstr(lam[:, 1:5, :] <= seg[:, 0:4, :] + seg[:, 1:5, :], name="lam_seg")  # 735
    m.addConstr(lam[:, 5, :] <= seg[:, 4, :], name="lam_seg5")        # 736


if __name__ == "__main__":
    import numpy as np
    import power_opt.reader as reader
    inp = reader.load_inputs()
    coal = load_coal(inp)
    print('输入自检: N_th=%d, m_th sum=%s, FOM常数=%.2f'
          % (coal.N_th, coal.m_th.sum(), float(np.sum(coal.c_th_FOM * coal.P_th_rated))))  # MATLAB 1020 基数=额定

    m = gp.Model()
    Tv = add_vars(m, coal)
    m.update()
    N, T = coal.N_th, coal.m_th.shape[1]
    exp_cols = N*T*8 + N*(T+1)*2 + N*6*T + N*5*T   # 8个N×T块 + 2个N×(T+1) + lambda + segment
    exp_bins = N*T*3 + N*5*T                       # u/v/w + segment
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (exp_bins, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == exp_bins, '变量清单与推导不符'
    print('煤电变量清单核对通过')

    add_constraints(m, coal, Tv)
    m.update()
    exp_rows = 32*N*T + 2*N           # 结构化账本：江苏 N=172,T=24→132440、海南 N=12,T=168→64536 均吻合
    print('煤电约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    print('煤电非零元: %d（海南 v2 全模型四项以 gurobi_hainan_last.log 为锚）' % m.getAttr('NumNZs'))