#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/15 14:00
@Author  : weiyutao
@File    : thermal.py
煤电单元：一个类 Thermal 自包含四步生命周期
（load → add_vars → add_constraints → add_cost）。
读取的表：分区机组信息表[煤电]、算例系统参数[煤电碳排节点]、检修计划（Type=煤电 行）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
import gurobipy as gp

import config
from power_opt.reader import _read, _zstr, Inputs
from model.units.base import _wsum


@dataclass
class CoalInputs:
    """MATLAB 1.3 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_th: int                                   # 机组台数
    UnitKey_th: list                            # 分区唯一编号
    name_th: list                               # 机组名
    area_th: np.ndarray                         # 机组 → 分区（0 基）
    P_th_rated: np.ndarray                      # 额定容量，仅固定运维基数
    P_th_max: np.ndarray; P_th_min: np.ndarray  # 非供暖期可用上限，MW / 最小出力 N_th×T（供暖日历切换），MW
    m_th: np.ndarray                            # 检修：1 检修，0 可用（N_th×T）
    c_th_FOM: np.ndarray                        # 本周期元/MW
    u_th_0: np.ndarray                          # 周期前运行状态，0/1
    c_th_start: np.ndarray; c_th_stop: np.ndarray  # 单次启动成本，元 / 单次停机成本，元
    a_th: np.ndarray; b_th: np.ndarray; c_th: np.ndarray  # 燃料成本二次/一次/常数项系数
    T_th_up: np.ndarray; T_th_down: np.ndarray; T_th_initial: np.ndarray  # 最小连续开机/停机小时数 / 周期前已保持初始状态小时数
    P_th_0: np.ndarray                          # 周期前功率，MW
    RU_th: np.ndarray; RD_th: np.ndarray; SU_th: np.ndarray; SD_th: np.ndarray  # 上/下爬坡速率 / 启动允许功率变化 / 停机允许功率变化
    SO2_th_factor: np.ndarray; NOX_th_factor: np.ndarray; dust_th_factor: np.ndarray  # SO2/NOx/烟尘排放因子
    CO2_th_node: np.ndarray        # N_th×6，t/h
    P_th_node: np.ndarray           # N_th×6，MW（MATLAB 105）
    F_th_node: np.ndarray           # N_th×6，元/h（MATLAB 106）


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


def _col_repmat(coal: CoalInputs, col: np.ndarray, T: int) -> np.ndarray:
    """把每台机组的参数向量铺成 N×T 矩阵（repmat(x,1,T) 等价）。
    首参 coal 为兼容 gas.py 既有调用保留，本模块内传 None。

    Args:
        coal (CoalInputs): 兼容位参数（忽略；gas.py 以 GasInputs 占位传入）。
        col (np.ndarray): 机组参数向量 (N,)。
        T (int): 调度时段数。

    Returns:
        np.ndarray: N×T 参数矩阵。
    """
    return np.repeat(col[:, None], T, axis=1)


def _interp_extrap(x: np.ndarray, y: np.ndarray, xq: np.ndarray) -> np.ndarray:
    """线性插值并在两端按首/末段斜率线性外推（MATLAB interp1 'linear','extrap' 等价）。

    Args:
        x (np.ndarray): 原节点横坐标 (6,)，严格递增。
        y (np.ndarray): 原节点纵坐标 (6,)。
        xq (np.ndarray): 查询横坐标 (6,)。

    Returns:
        np.ndarray: 插值/外推结果 (6,)。
    """
    yq = np.interp(xq, x, y)
    low = xq < x[0]
    if low.any():
        yq[low] = y[0] + (y[1] - y[0]) / (x[1] - x[0]) * (xq[low] - x[0])
    high = xq > x[-1]
    if high.any():
        yq[high] = y[-1] + (y[-1] - y[-2]) / (x[-1] - x[-2]) * (xq[high] - x[-1])
    return yq


class Thermal:
    """煤电单元：inputs/vars 为实例属性，四步生命周期依次组装进模型。"""

    inputs: CoalInputs      # 煤电输入（load 后生效）
    vars: ThermalVars       # 煤电决策变量（add_vars 后生效）

    def load(self, inp: Inputs) -> None:
        """镜像 MATLAB 1.3 节（87-135）+ 六节点派生（105-106）加载煤电输入。
        缺列缺值即报错；结果存 self.inputs。

        Args:
            inp (Inputs): 系统公共输入。

        Returns:
            None
        """
        T = inp.time.T
        # ---- 检修计划：领取 Type=煤电 的行（MATLAB 118-123，区间左闭右开）----
        mtn_coal = inp.maintenance[inp.maintenance["Type"].astype(str) == "煤电"]

        # ---- 1.3 煤电表（MATLAB 87-123）----
        """
        CAP：容量
        DERATE_RATE：非供暖受阻率
        MIN_POWER_GEN：历史最小技术出力（仅碳排旧节点坐标用，不再作约束下限）
        MIN_RATIO_HEATING：供暖期最小技术出力率（×额定CAP得MW）
        MIN_RATIO_NONHEATING：非供暖期最小技术出力率（×额定CAP得MW）
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
        req_th = ["UnitKey", "SUBSTR(C.ID，12)", "CAP", "DERATE_RATE", "MIN_POWER_GEN",
                  "MIN_RATIO_HEATING", "MIN_RATIO_NONHEATING", "INIT_STATUS",
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
        P_th_max = P_th_rated * (1.0 - f("DERATE_RATE")); P_th_min_plain = f("MIN_POWER_GEN")
        # MATLAB 103-108：两期出力率均以额定容量 CAP 为基准，按供暖日历逐时切换
        zonePos = {z: i for i, z in enumerate(inp.heating.ZoneID)}
        if miss := [z for z in zone_th if z not in zonePos]:
            raise KeyError(f"煤电分区编号 {set(miss)} 不在供暖期设置表中")
        P_th_min_heating = P_th_rated * f("MIN_RATIO_HEATING")
        P_th_min_nonheating = P_th_rated * f("MIN_RATIO_NONHEATING")
        heat = inp.heating.isHeatingPeriod[[zonePos[z] for z in zone_th], :]   # ismember 行序
        P_th_min = P_th_min_nonheating[:, None] + (P_th_min_heating - P_th_min_nonheating)[:, None] * heat  # 机组×T，MW
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

        # ---- 六节点燃料成本派生（MATLAB 118-121）：曲线覆盖两期可行出力范围，
        # 下限取两期出力率最小值；实际季节下限另由 P_th_min 逐时约束 ----
        P_th_node_min = np.minimum(P_th_min_heating, P_th_min_nonheating)
        frac = (np.arange(6) / 5.0)[None, :]                       # 0,0.2,...,1
        P_th_node = P_th_node_min[:, None] + (P_th_max - P_th_node_min)[:, None] * frac
        F_th_node = a_th[:, None] * P_th_node ** 2 + b_th[:, None] * P_th_node + c_th[:, None]

        # ---- 六节点排放（MATLAB 145-156）：每台 6 行，NodeIndex 必须是 0..5；
        # 碳排表节点坐标为历史 MIN_POWER_GEN 区间，插值到新节点，低端线性外推 ----
        em = _read(config.SYSTEM_FILE, "煤电碳排节点", ["UnitKey", "NodeIndex", "CO2Rate_t_h"])
        CO2_th_node = np.full((N_th, 6), np.nan)
        for i, key in enumerate(UnitKey_th):
            rows = em[em["UnitKey"].astype(str) == key]
            if len(rows) != 6:
                raise KeyError(f"煤电 {key} 碳排节点应为 6 行，实际 {len(rows)} 行")
            nodes = {int(n): float(v) for n, v in zip(rows["NodeIndex"], rows["CO2Rate_t_h"])}
            if set(nodes) != set(range(6)):
                raise KeyError(f"煤电 {key} NodeIndex 应为 0..5，实际 {sorted(nodes)}")
            # MATLAB 152-155：oldPowerNodes = MIN_POWER_GEN..P_max 六等分；interp1 'linear','extrap'
            oldPowerNodes = P_th_min_plain[i] + (P_th_max[i] - P_th_min_plain[i]) * np.arange(6) / 5.0
            CO2_th_node[i, :] = _interp_extrap(oldPowerNodes,
                                               np.array([nodes[k] for k in range(6)]),
                                               P_th_node[i, :])

        for name, arr in [("P_th_rated", P_th_rated), ("P_th_max", P_th_max), ("P_th_min", P_th_min),
                          ("m_th", m_th),
                          ("CO2_th_node", CO2_th_node), ("P_th_node", P_th_node), ("F_th_node", F_th_node)]:
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"{name} 含 NaN/Inf，检查煤电输入表")

        self.inputs = CoalInputs(
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

    def add_vars(self, m: gp.Model) -> None:
        """镜像 MATLAB 2.1 节（570-588）创建煤电决策变量，存 self.vars。
        连续变量 lb=-inf（镜像 sdpvar 无界默认）；二值默认 [0,1]。
        界限交给 3.1 节约束行去钉，保证行数与 MATLAB 一致。

        Args:
            m (gp.Model): Gurobi 模型。

        Returns:
            ThermalVars: 决策变量容器（同时存 self.vars，两处同引用）。
        """
        coal = self.inputs
        N, T = coal.N_th, coal.m_th.shape[1]
        P_th       = m.addMVar((N, T),     lb=-INF, name="P_th")              
        u_th       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="u_th")
        v_th       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="v_th")
        w_th       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="w_th")
        V_th_count = m.addMVar((N, T + 1), lb=-INF, name="V_th_count")
        W_th_count = m.addMVar((N, T + 1), lb=-INF, name="W_th_count")
        R_th_hot   = m.addMVar((N, T),     lb=-INF, name="R_th_hot")
        R_th_cold  = m.addMVar((N, T),     lb=-INF, name="R_th_cold")
        F_th       = m.addMVar((N, T),     lb=-INF, name="F_th")
        CO2_th     = m.addMVar((N, T),     lb=-INF, name="CO2_th")
        lambda_th  = m.addMVar((N, 6, T),  lb=-INF, name="lambda_th")
        segment_th = m.addMVar((N, 5, T),  vtype=gp.GRB.BINARY, name="segment_th")
        self.vars = ThermalVars(P_th=P_th, u_th=u_th, v_th=v_th, w_th=w_th,
                                V_th_count=V_th_count, W_th_count=W_th_count,
                                R_th_hot=R_th_hot, R_th_cold=R_th_cold,
                                F_th=F_th, CO2_th=CO2_th,
                                lambda_th=lambda_th, segment_th=segment_th)
        return self.vars

    def add_constraints(self, m: gp.Model, inp: Inputs, sv=None) -> None:
        """一比一对照 MATLAB 3.1 节（686-737）添加煤电约束。行数账本
        （T=24 → 132,440 行）：22 个 N×T 块 90,816 + count 初值 344 +
        λ≥0 24,768 + λ桥 16,512。煤电不交叉引用系统变量与公共输入，
        胖签名入参忽略。

        Args:
            m (gp.Model): Gurobi 模型。
            inp (Inputs): 系统公共输入（本单元忽略）。
            sv (SystemVars): 系统层决策变量（本单元忽略）。
            
        Returns:
            None
        """
        coal = self.inputs
        var = self.vars
        N, T = coal.N_th, coal.m_th.shape[1]
        dt = coal.m_th                     # 检修 0/1 矩阵
        u, v, w = var.u_th, var.v_th, var.w_th
        P, R_hot, R_cold = var.P_th, var.R_th_hot, var.R_th_cold

        m.addConstr(u[:, 0] - coal.u_th_0 == v[:, 0] - w[:, 0], name="logic_t0") # Math: u_{i,1} - u_{i,0} = v_{i,1} - w_{i,1}（首时段启停逻辑约束）
        if T > 1:
            m.addConstr(u[:, 1:] - u[:, :-1] == v[:, 1:] - w[:, 1:], name="logic") # Math: u_{i,t} - u_{i,t-1} = v_{i,t} - w_{i,t}（常规时段启停逻辑约束）

        m.addConstr(v + w <= 1, name="vw") # Math: v_{i,t} + w_{i,t} \le 1（同一时段不能同时启动和停机互斥约束）
        m.addConstr(u <= 1 - dt, name="um") # Math: u_{i,t} \le 1 - m_{i,t}（计划检修状态必须停机约束）

        m.addConstr(R_hot >= 0, name="Rhotlb") # Math: R_{th,i,t}^{hot} \ge 0（热备用容量非负约束）
        m.addConstr(R_cold >= 0, name="Rcoldlb") # Math: R_{th,i,t}^{cold} \ge 0（冷备用容量非负约束）

        PminU = coal.P_th_min                                # 已是 N×T 时变矩阵（MATLAB 738 直接点乘）
        m.addConstr(PminU * u <= P, name="Pmin") # Math: P_{th,i}^{min} u_{i,t} \le P_{th,i,t}（最小稳定燃烧出力下限约束，P_min 逐时供暖切换）

        PmaxU = _col_repmat(coal, coal.P_th_max, T)
        m.addConstr(P + R_hot <= PmaxU * u, name="Pmax_hot") # Math: P_{th,i,t} + R_{th,i,t}^{hot} \le P_{th,i}^{max} u_{i,t}（最大技术出力与热备用容量上限约束）

        m.addConstr(R_cold <= PmaxU * (1 - u), name="Rcold_u") # Math: R_{th,i,t}^{cold} \le P_{th,i}^{max} (1 - u_{i,t})（在线机组不能提供冷备用约束）
        m.addConstr(R_cold <= PmaxU * (1 - dt), name="Rcold_m") # Math: R_{th,i,t}^{cold} \le P_{th,i}^{max} (1 - m_{i,t})（检修机组不能提供冷备用约束）

        RU = _col_repmat(coal, coal.RU_th, T); SU = _col_repmat(coal, coal.SU_th, T)
        RD = _col_repmat(coal, coal.RD_th, T); SD = _col_repmat(coal, coal.SD_th, T)
        u0 = coal.u_th_0[:, None]; P0 = coal.P_th_0[:, None]     # N×1 初始状态/功率
        m.addConstr(P[:, 0:1] - P0 <= RU[:, 0:1] * u0 + SU[:, 0:1] * (u[:, 0:1] - u0),
                    name="ramp_up_t0") # Math: P_{th,i,1} - P_{th,i,0} \le R_{th,i}^{U} u_{i,0} + S_{th,i}^{U} (u_{i,1} - u_{i,0})（首时段向上爬坡约束）
        m.addConstr(P0 - P[:, 0:1] <= RD[:, 0:1] * u[:, 0:1] + SD[:, 0:1] * (u0 - u[:, 0:1]),
                    name="ramp_dn_t0") # Math: P_{th,i,0} - P_{th,i,1} \le R_{th,i}^{D} u_{i,1} + S_{th,i}^{D} (u_{i,0} - u_{i,1})（首时段向下爬坡约束）
        if T > 1:
            u_prev = u[:, :-1]; P_prev = P[:, :-1]
            m.addConstr(P[:, 1:] - P_prev <= RU[:, 1:] * u_prev
                        + SU[:, 1:] * (u[:, 1:] - u_prev), name="ramp_up") # Math: P_{th,i,t} - P_{th,i,t-1} \le R_{th,i}^{U} u_{i,t-1} + S_{th,i}^{U} (u_{i,t} - u_{i,t-1})（常规时段向上爬坡约束）
            m.addConstr(P_prev - P[:, 1:] <= RD[:, 1:] * u[:, 1:]
                        + SD[:, 1:] * (u_prev - u[:, 1:]), name="ramp_dn") # Math: P_{th,i,t-1} - P_{th,i,t} \le R_{th,i}^{D} u_{i,t} + S_{th,i}^{D} (u_{i,t-1} - u_{i,t})（常规时段向下爬坡约束）

        # ---- MATLAB 704-706：累计启停量递推 ----
        Vc, Wc = var.V_th_count, var.W_th_count
        m.addConstr(Vc[:, 0] == 0, name="Vinit"); m.addConstr(Wc[:, 0] == 0, name="Winit") # Math: V_{i,0} = 0, \ W_{i,0} = 0（周期起点累计启停次数清零约束）
        m.addConstr(Vc[:, 1:] - Vc[:, :-1] == v, name="Vrec") # Math: V_{i,t} - V_{i,t-1} = v_{i,t}（累计启动次数递推约束）
        m.addConstr(Wc[:, 1:] - Wc[:, :-1] == w, name="Wrec") # Math: W_{i,t} - W_{i,t-1} = w_{i,t}（累计停机次数递推约束）

        # ---- MATLAB 707-723：最小开停机。初始剩余义务 remaining 全为 0 ----
        # remaining = clip(u0*T_up + (1-u0)*T_down - init_hours, 0, None)，>0 才建锁定行，镜像 MATLAB if 守卫。
        rem = np.clip(coal.u_th_0 * coal.T_th_up + (1 - coal.u_th_0) * coal.T_th_down
                      - coal.T_th_initial, 0, None)
        for i in range(N):
            if rem[i] > 0:
                m.addConstr(u[i, :min(T, int(rem[i]))] == coal.u_th_0[i],
                            name=f"lock_{i}") # Math: u_{i,t} = u_{i,0} \quad \forall t \le L_{i}（初始历史状态剩余时段锁定约束）

        firstU = np.maximum(0, np.arange(T) - coal.T_th_up.astype(int)[:, None] + 1)
        firstD = np.maximum(0, np.arange(T) - coal.T_th_down.astype(int)[:, None] + 1)
        m.addConstr(Vc[:, 1:] - Vc[np.arange(N)[:, None], firstU] <= u, name="minup") # Math: \sum_{\tau=t-T_{up}+1}^{t} v_{i,\tau} \le u_{i,t}（最小连续开机时间约束）
        m.addConstr(Wc[:, 1:] - Wc[np.arange(N)[:, None], firstD] <= 1 - u, name="mindn") # Math: \sum_{\tau=t-T_{dn}+1}^{t} w_{i,\tau} \le 1 - u_{i,t}（最小连续停机时间约束）

        # ---- MATLAB 724-737：五段分段线性（SOS2 等价）----
        lam, seg = var.lambda_th, var.segment_th
        m.addConstr(lam >= 0, name="lam_lb") # Math: \lambda_{i,t,k} \ge 0（权重变量非负约束）
        m.addConstr(lam.sum(axis=1) == u, name="lam_sum") # Math: \sum_{k=1}^{6} \lambda_{i,t,k} = u_{i,t}（分段线性化节点权重之和等于启停状态约束）
        
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
                            name=f"Pnode_{t}_{i}") # Math: P_{th,i,t} = \sum_{k=1}^{6} \lambda_{i,t,k} P_{th,i,k}^{node}（节点权重表达实际有功出力约束）
                m.addConstr(var.F_th[i, t] == gp.quicksum(lam_t[i, k] * coal.F_th_node[i, k] for k in range(K)),
                            name=f"Fnode_{t}_{i}") # Math: F_{th,i,t} = \sum_{k=1}^{6} \lambda_{i,t,k} F_{th,i,k}^{node}（节点权重表达燃料成本约束）
                m.addConstr(var.CO2_th[i, t] == gp.quicksum(lam_t[i, k] * coal.CO2_th_node[i, k] for k in range(K)),
                            name=f"Cnode_{t}_{i}") # Math: E_{th,i,t}^{CO2} = \sum_{k=1}^{6} \lambda_{i,t,k} E_{th,i,k}^{CO2,node}（节点权重表达碳排放量约束）
        m.addConstr(seg.sum(axis=1) == u, name="seg_sum") # Math: \sum_{s=1}^{5} z_{i,t,s} = u_{i,t}（区段指示变量之和等于启停状态约束）
        m.addConstr(lam[:, 0, :] <= seg[:, 0, :], name="lam_seg0") # Math: \lambda_{i,t,1} \le z_{i,t,1}（SOS2首节点相邻非零约束）
        m.addConstr(lam[:, 1:5, :] <= seg[:, 0:4, :] + seg[:, 1:5, :], name="lam_seg") # Math: \lambda_{i,t,k} \le z_{i,t,k-1} + z_{i,t,k} \quad (k=2,3,4,5)（SOS2中间节点相邻非零约束）
        m.addConstr(lam[:, 5, :] <= seg[:, 4, :], name="lam_seg5") # Math: \lambda_{i,t,6} \le z_{i,t,5}（SOS2末节点相邻非零约束）

    def add_cost(self, dt: float, econ) -> dict:
        """镜像 MATLAB 4.1 节（1016-1025）汇总煤电成本表达式。
        加法顺序冻结，不得重排（浮点分组变即末位漂移）。
        键名是对外成本，冻结。

        Args:
            dt (float): 单位步长（小时）。
            econ (EconomicParams): 系统经济参数。

        Returns:
            dict: 煤电成本表达式与分项（键名与 solve 层成本 dict 一致）。
        """
        T = self.inputs.m_th.shape[1]
        var, coal = self.vars, self.inputs
        C_th_fuel  = dt * var.F_th.sum() # Math: C_{th,fuel} = \sum_{t=1}^{T} \sum_{i} F_{th,i,t} \Delta t（整个调度周期内煤电机组总燃料成本）
        C_th_start = _wsum(coal.c_th_start, var.v_th, T) # Math: C_{th,start} = \sum_{t=1}^{T} \sum_{i} c_{th,i}^{start} v_{th,i,t}（煤电机组总启动成本）
        C_th_stop  = _wsum(coal.c_th_stop, var.w_th, T) # Math: C_{th,stop} = \sum_{t=1}^{T} \sum_{i} c_{th,i}^{stop} w_{th,i,t}（煤电机组总停机成本）
        C_th_FOM   = float(np.sum(coal.c_th_FOM * coal.P_th_rated)) # Math: C_{th,FOM} = \sum_{i} c_{th,i}^{FOM} P_{th,i}^{rated}（煤电机组固定运行维护成本）
        C_th_CO2   = econ.c_CO2 * dt * var.CO2_th.sum() # Math: C_{th,CO2} = c_{CO2} \Delta t \sum_{t=1}^{T} \sum_{i} E_{th,i,t}^{CO2}（煤电机组碳排放成本）
        C_th_SO2   = econ.c_SO2 * dt * _wsum(coal.SO2_th_factor, var.P_th, T) # Math: C_{th,SO2} = c_{SO2} \Delta t \sum_{t=1}^{T} \sum_{i} e_{th,i}^{SO2} P_{th,i,t}（煤电机组SO₂排放成本）
        C_th_NOX   = econ.c_NOX * dt * _wsum(coal.NOX_th_factor, var.P_th, T) # Math: C_{th,NOX} = c_{NOX} \Delta t \sum_{t=1}^{T} \sum_{i} e_{th,i}^{NOX} P_{th,i,t}（煤电机组NOₓ排放成本）
        C_th_dust  = econ.c_dust * dt * _wsum(coal.dust_th_factor, var.P_th, T) # Math: C_{th,dust} = c_{dust} \Delta t \sum_{t=1}^{T} \sum_{i} e_{th,i}^{dust} P_{th,i,t}（煤电机组烟尘排放成本）
        C_th = C_th_fuel + C_th_start + C_th_stop + C_th_FOM + \
            C_th_CO2 + C_th_SO2 + C_th_NOX + C_th_dust # Math: C_{th} = C_{th,fuel} + C_{th,start} + C_{th,stop} + C_{th,FOM} + C_{th,CO2} + C_{th,SO2} + C_{th,NOX} + C_{th,dust}（大区域煤电机组总运行成本）
        return {
            "C_th": C_th, "C_th_fuel": C_th_fuel, "C_th_start": C_th_start,
            "C_th_stop": C_th_stop, "C_th_FOM": C_th_FOM, "C_th_CO2": C_th_CO2,
            "C_th_SO2": C_th_SO2, "C_th_NOX": C_th_NOX, "C_th_dust": C_th_dust,
        }


if __name__ == "__main__":
    import numpy as np
    import power_opt.reader as reader
    inp = reader.load_inputs()
    th = Thermal()
    th.load(inp)
    coal = th.inputs
    print('输入自检: N_th=%d, m_th sum=%s, FOM常数=%.2f'
          % (coal.N_th, coal.m_th.sum(), float(np.sum(coal.c_th_FOM * coal.P_th_rated))))  # MATLAB 1020 基数=额定

    m = gp.Model()
    th.add_vars(m)
    m.update()
    N, T = coal.N_th, coal.m_th.shape[1]
    exp_cols = N*T*8 + N*(T+1)*2 + N*6*T + N*5*T   # 8个N×T块 + 2个N×(T+1) + lambda + segment
    exp_bins = N*T*3 + N*5*T                       # u/v/w + segment
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (exp_bins, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == exp_bins, '变量清单与推导不符'
    print('煤电变量清单核对通过')

    th.add_constraints(m, inp)
    m.update()
    exp_rows = 32*N*T + 2*N           # 结构化账本：江苏 N=172,T=24→132440、海南 N=12,T=168→64536 均吻合
    print('煤电约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    print('煤电非零元: %d（海南 v2 全模型四项以 gurobi_hainan_last.log 为锚）' % m.getAttr('NumNZs'))
