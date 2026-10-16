#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/15 15:30
@Author  : weiyutao
@File    : gas.py
气电单元：一个类 Gas 自包含四步生命周期
（load → add_vars → add_constraints → add_cost）。
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
from power_opt.reader import _read, _zstr, Inputs
from model.units.base import _wsum
from model.units.thermal import _col_repmat


@dataclass
class GasInputs:
    """MATLAB 1.4 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_gas: int                                   # 机组台数
    UnitKey_gas: list                            # 分区唯一编号
    name_gas: list                               # 机组名（八~十一节分析表用，MATLAB gasData.NAME）
    area_gas: np.ndarray                         # 机组 → 分区（0 基）
    P_gas_rated: np.ndarray                      # 额定容量，仅固定运维基数（MATLAB 137）
    P_gas_max: np.ndarray; P_gas_min: np.ndarray  # 非供暖期可用上限，MW / 最小出力 N_gas×T（供暖日历切换），MW
    m_gas: np.ndarray                            # 检修：1 检修，0 可用（N_gas×T）
    c_gas_FOM: np.ndarray                        # 本周期元/MW
    u_gas_0: np.ndarray                          # 周期前运行状态，0/1
    c_gas_start: np.ndarray; c_gas_stop: np.ndarray  # 单次启动成本，元 / 单次停机成本，元
    c_gas_gen: np.ndarray                        # 线性单位发电成本，元/MWh（MATLAB 152）
    P_gas_0: np.ndarray                          # 周期前功率，MW
    RU_gas: np.ndarray; RD_gas: np.ndarray; SU_gas: np.ndarray; SD_gas: np.ndarray  # 上/下爬坡速率 / 启动允许功率变化 / 停机允许功率变化
    CO2_gas_factor: np.ndarray                   # CO2 排放因子，t/MWh（MATLAB 169）
    T_gas_up: np.ndarray; T_gas_down: np.ndarray; T_gas_initial: np.ndarray  # 最小连续开机/停机小时数 / 周期前已保持初始状态小时数


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


class Gas:
    """气电单元：inputs/vars 为实例属性，四步生命周期依次组装进模型。"""

    inputs: GasInputs      # 气电输入（load 后生效）
    vars: GasVars          # 气电决策变量（add_vars 后生效）

    def load(self, inp: Inputs) -> None:
        """镜像 MATLAB 1.4 节（138-172）加载气电输入。缺列缺值即报错；
        结果存 self.inputs。

        Args:
            inp (Inputs): 系统公共输入。

        Returns:
            None
        """
        T = inp.time.T
        # ---- 检修计划：领取 Type=气电 的行（MATLAB 161-166，区间左闭右开）----
        mtn_gas = inp.maintenance[inp.maintenance["Type"].astype(str) == "气电"]

        # ---- 1.4 气电表（MATLAB 138-160）----
        """
        CAP：额定容量
        DERATE_RATE：非供暖受阻率
        MIN_RATIO_HEATING：供暖期最小技术出力率（×额定CAP得MW）
        MIN_RATIO_NONHEATING：非供暖期最小技术出力率（×额定CAP得MW）
        INIT_STATUS：周期前运行状态，0/1
        COST_START_UP：启动成本，元/次
        COST_SHUT_DOWN：停机成本，元/次
        GEN_COST：单位发电成本，元/MWh
        INIT_POWER：周期前功率（初始出力）
        RAMP_LIMIT_UP：向上爬坡（允许的功率上升速率）
        RAMP_LIMIT_DOWN：向下爬坡（允许的功率下降速率）
        START_RAMP：启动允许功率变化
        STOP_RAMP：停机允许功率变化
        CO2_FACTOR：CO2排放因子，t/MWh
        MIN_UP_TIME：最小连续开机时间
        MIN_DOWN_TIME：最小连续停机时间
        INIT_HOURS：初始状态已持续时长
        FOM_COST_PERIOD：年度功率运维成本
        """
        req_gas = ["UnitKey", "SUBSTR(C.ID，12)", "CAP", "DERATE_RATE",
                   "MIN_RATIO_HEATING", "MIN_RATIO_NONHEATING", "INIT_STATUS",
                   "COST_START_UP", "COST_SHUT_DOWN", "GEN_COST", "INIT_POWER",
                   "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN", "START_RAMP", "STOP_RAMP",
                   "CO2_FACTOR", "MIN_UP_TIME", "MIN_DOWN_TIME", "INIT_HOURS",
                   "FOM_COST_PERIOD", "NAME"]
        gasData = _read(config.UNIT_FILE, "气电", req_gas)
        N_gas = len(gasData)
        zone_gas = [_zstr(v) for v in gasData["SUBSTR(C.ID，12)"]]
        areaPos = {a: i for i, a in enumerate(inp.areas.AreaID)}
        if miss := [z for z in zone_gas if z not in areaPos]:
            raise KeyError(f"气电分区编号 {set(miss)} 不在分区表中")
        area_gas = np.array([areaPos[z] for z in zone_gas])
        f = lambda c: gasData[c].to_numpy(float)
        UnitKey_gas = gasData["UnitKey"].astype(str).tolist()
        name_gas = gasData["NAME"].astype(str).tolist()               # 八~十一节分析表用
        # MATLAB 137-138：额定容量只作固定运维基数；出力上限=额定×(1-受阻率)
        P_gas_rated = f("CAP")
        P_gas_max = P_gas_rated * (1.0 - f("DERATE_RATE"))
        # MATLAB 170-174：两期出力率均以额定容量 CAP 为基准，按供暖日历逐时切换
        zonePos = {z: i for i, z in enumerate(inp.heating.ZoneID)}
        if miss := [z for z in zone_gas if z not in zonePos]:
            raise KeyError(f"气电分区编号 {set(miss)} 不在供暖期设置表中")
        P_gas_min_heating = P_gas_rated * f("MIN_RATIO_HEATING")
        P_gas_min_nonheating = P_gas_rated * f("MIN_RATIO_NONHEATING")
        heat = inp.heating.isHeatingPeriod[[zonePos[z] for z in zone_gas], :]   # ismember 行序
        P_gas_min = P_gas_min_nonheating[:, None] + (P_gas_min_heating - P_gas_min_nonheating)[:, None] * heat  # 机组×T，MW
        u_gas_0 = f("INIT_STATUS")
        c_gas_start = inp.econ.costScale * f("COST_START_UP"); c_gas_stop = inp.econ.costScale * f("COST_SHUT_DOWN")
        c_gas_gen = inp.econ.costScale * f("GEN_COST")
        P_gas_0 = f("INIT_POWER"); RU_gas = f("RAMP_LIMIT_UP"); RD_gas = f("RAMP_LIMIT_DOWN")
        SU_gas = f("START_RAMP"); SD_gas = f("STOP_RAMP")
        CO2_gas_factor = f("CO2_FACTOR")
        T_gas_up = f("MIN_UP_TIME"); T_gas_down = f("MIN_DOWN_TIME"); T_gas_initial = f("INIT_HOURS")
        c_gas_FOM = inp.econ.fixedCostScale * f("FOM_COST_PERIOD")

        m_gas = np.zeros((N_gas, T))
        keyPos = {k: i for i, k in enumerate(UnitKey_gas)}
        t_prev = inp.time.Time[0] - pd.Timedelta(hours=1)   # 起点前 1 小时（MATLAB 163）
        for _, plan in mtn_gas.iterrows():
            key = str(plan["UnitKey"])
            if key not in keyPos:
                raise KeyError(f"检修计划机组 {key} 不在气电表中（不猜、不忽略）")
            periods = (inp.time.Time >= plan["StartTime"]) & (inp.time.Time < plan["EndTime"])
            m_gas[keyPos[key], periods] = 1
            # MATLAB 163-167：起点前 1 小时已在检修窗口 → 初值覆盖为停机，累计已停机小时
            # （T_gas_initial 先读 INIT_HOURS（157 行），在此被覆盖）
            if plan["StartTime"] <= t_prev < plan["EndTime"]:
                i = keyPos[key]
                u_gas_0[i] = 0.0
                P_gas_0[i] = 0.0
                T_gas_initial[i] = (inp.time.Time[0] - plan["StartTime"]).total_seconds() / 3600.0

        for name, arr in [("P_gas_rated", P_gas_rated), ("P_gas_max", P_gas_max), ("P_gas_min", P_gas_min), ("m_gas", m_gas),
                          ("CO2_gas_factor", CO2_gas_factor)]:
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"{name} 含 NaN/Inf，检查气电输入表")

        self.inputs = GasInputs(
            N_gas=N_gas, UnitKey_gas=UnitKey_gas, name_gas=name_gas, area_gas=area_gas,
            P_gas_rated=P_gas_rated,
            P_gas_max=P_gas_max, P_gas_min=P_gas_min, m_gas=m_gas, c_gas_FOM=c_gas_FOM,
            u_gas_0=u_gas_0, c_gas_start=c_gas_start, c_gas_stop=c_gas_stop,
            c_gas_gen=c_gas_gen, P_gas_0=P_gas_0, RU_gas=RU_gas, RD_gas=RD_gas,
            SU_gas=SU_gas, SD_gas=SD_gas, CO2_gas_factor=CO2_gas_factor,
            T_gas_up=T_gas_up, T_gas_down=T_gas_down, T_gas_initial=T_gas_initial,
        )

    def add_vars(self, m: gp.Model) -> GasVars:
        """镜像 MATLAB 2.2 节（591-600）创建气电决策变量，存 self.vars。
        连续变量 lb=-inf（镜像 sdpvar 无界默认），界限由 3.2 节约束钉。

        Args:
            m (gp.Model): Gurobi 模型。

        Returns:
            GasVars: 决策变量容器（同时存 self.vars，两处同引用）。
        """
        gas = self.inputs
        N, T = gas.N_gas, gas.m_gas.shape[1]
        P_gas       = m.addMVar((N, T),     lb=-INF, name="P_gas")
        u_gas       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="u_gas")
        v_gas       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="v_gas")
        w_gas       = m.addMVar((N, T),     vtype=gp.GRB.BINARY, name="w_gas")
        V_gas_count = m.addMVar((N, T + 1), lb=-INF, name="V_gas_count")
        W_gas_count = m.addMVar((N, T + 1), lb=-INF, name="W_gas_count")
        R_gas_hot   = m.addMVar((N, T),     lb=-INF, name="R_gas_hot")
        R_gas_cold  = m.addMVar((N, T),     lb=-INF, name="R_gas_cold")
        self.vars = GasVars(P_gas=P_gas, u_gas=u_gas, v_gas=v_gas, w_gas=w_gas,
                            V_gas_count=V_gas_count, W_gas_count=W_gas_count,
                            R_gas_hot=R_gas_hot, R_gas_cold=R_gas_cold)
        return self.vars

    def add_constraints(self, m: gp.Model, inp: Inputs, sv=None) -> None:
        """一比一对照 MATLAB 3.2 节（741-778）添加气电约束。行数账本
        （T=24 → 44,888 行）：15 个 N×T 块 44,640 + count 初值 248；锁定行 0
        （INIT_HOURS 全满足，已核数据）。非零元账本（T=24 → 109,740）见
        各条行内注释。气电不交叉引用系统变量与公共输入，胖签名入参忽略。

        Args:
            m (gp.Model): Gurobi 模型。
            inp (Inputs): 系统公共输入（本单元忽略）。
            sv (SystemVars): 系统层决策变量（本单元忽略）。

        Returns:
            None
        """
        gas = self.inputs
        var = self.vars
        N, T = gas.N_gas, gas.m_gas.shape[1]
        dt = gas.m_gas                     # 检修 0/1 矩阵
        u, v, w = var.u_gas, var.v_gas, var.w_gas
        P, R_hot, R_cold = var.P_gas, var.R_gas_hot, var.R_gas_cold

        # ---- MATLAB 743-744：u - u_prev = v - w；首列 prev = u_gas_0 ----
        m.addConstr(u[:, 0] - gas.u_gas_0 == v[:, 0] - w[:, 0], name="logic_t0") # Math: u_{i,1} - u_{i,0} = v_{i,1} - w_{i,1}（首时段启停逻辑约束）
        if T > 1:
            m.addConstr(u[:, 1:] - u[:, :-1] == v[:, 1:] - w[:, 1:], name="logic") # Math: u_{i,t} - u_{i,t-1} = v_{i,t} - w_{i,t}（常规时段启停逻辑约束）

        m.addConstr(v + w <= 1, name="vw") # Math: v_{i,t} + w_{i,t} \le 1（同一时段不能同时启动和停机互斥约束）
        m.addConstr(u <= 1 - dt, name="um") # Math: u_{i,t} \le 1 - m_{i,t}（计划检修状态必须停机约束）

        # ---- MATLAB 746：P_min·u <= P（P_min 已是 N×T 时变矩阵）----
        PminU = gas.P_gas_min
        m.addConstr(PminU * u <= P, name="Pmin") # Math: P_{gas,i}^{min} u_{i,t} \le P_{gas,i,t}（最小技术出力下限约束，P_min 逐时供暖切换）

        # ---- MATLAB 748：P + R_hot <= Pmax·u ----
        PmaxU = _col_repmat(gas, gas.P_gas_max, T)
        m.addConstr(P + R_hot <= PmaxU * u, name="Pmax_hot") # Math: P_{gas,i,t} + R_{gas,i,t}^{hot} \le P_{gas,i}^{max} u_{i,t}（最大技术出力与热备用容量上限约束）

        m.addConstr(R_hot >= 0, name="Rhotlb") # Math: R_{gas,i,t}^{hot} \ge 0（热备用容量非负约束）
        m.addConstr(R_cold >= 0, name="Rcoldlb") # Math: R_{gas,i,t}^{cold} \ge 0（冷备用容量非负约束）

        m.addConstr(R_cold <= PmaxU * (1 - u), name="Rcold_u") # Math: R_{gas,i,t}^{cold} \le P_{gas,i}^{max} (1 - u_{i,t})（在线机组不能提供冷备用约束）
        m.addConstr(R_cold <= PmaxU * (1 - dt), name="Rcold_m") # Math: R_{gas,i,t}^{cold} \le P_{gas,i}^{max} (1 - m_{i,t})（检修机组不能提供冷备用约束）

        # ---- MATLAB 752-756：爬坡。首时段 prev = P_gas_0 / u_gas_0，不经过 concatenate ----
        RU = _col_repmat(gas, gas.RU_gas, T); SU = _col_repmat(gas, gas.SU_gas, T)
        RD = _col_repmat(gas, gas.RD_gas, T); SD = _col_repmat(gas, gas.SD_gas, T)
        u0 = gas.u_gas_0[:, None]; P0 = gas.P_gas_0[:, None]     # N×1 初始状态/功率
        m.addConstr(P[:, 0:1] - P0 <= RU[:, 0:1] * u0 + SU[:, 0:1] * (u[:, 0:1] - u0),
                    name="ramp_up_t0") # Math: P_{gas,i,1} - P_{gas,i,0} \le R_{gas,i}^{U} u_{i,0} + S_{gas,i}^{U} (u_{i,1} - u_{i,0})（首时段向上爬坡约束）
        m.addConstr(P0 - P[:, 0:1] <= RD[:, 0:1] * u[:, 0:1] + SD[:, 0:1] * (u0 - u[:, 0:1]),
                    name="ramp_dn_t0") # Math: P_{gas,i,0} - P_{gas,i,1} \le R_{gas,i}^{D} u_{i,1} + S_{gas,i}^{D} (u_{i,0} - u_{i,1})（首时段向下爬坡约束）
        if T > 1:
            u_prev = u[:, :-1]; P_prev = P[:, :-1]
            m.addConstr(P[:, 1:] - P_prev <= RU[:, 1:] * u_prev
                        + SU[:, 1:] * (u[:, 1:] - u_prev), name="ramp_up") # Math: P_{gas,i,t} - P_{gas,i,t-1} \le R_{gas,i}^{U} u_{i,t-1} + S_{gas,i}^{U} (u_{i,t} - u_{i,t-1})（常规时段向上爬坡约束）
            m.addConstr(P_prev - P[:, 1:] <= RD[:, 1:] * u[:, 1:]
                        + SD[:, 1:] * (u_prev - u[:, 1:]), name="ramp_dn") # Math: P_{gas,i,t-1} - P_{gas,i,t} \le R_{gas,i}^{D} u_{i,t} + S_{gas,i}^{D} (u_{i,t-1} - u_{i,t})（常规时段向下爬坡约束）

        # ---- MATLAB 759-761：累计启停量递推 ----
        Vc, Wc = var.V_gas_count, var.W_gas_count
        m.addConstr(Vc[:, 0] == 0, name="Vinit"); m.addConstr(Wc[:, 0] == 0, name="Winit") # Math: V_{i,0} = 0, \ W_{i,0} = 0（周期起点累计启停次数清零约束）
        m.addConstr(Vc[:, 1:] - Vc[:, :-1] == v, name="Vrec") # Math: V_{i,t} - V_{i,t-1} = v_{i,t}（累计启动次数递推约束）
        m.addConstr(Wc[:, 1:] - Wc[:, :-1] == w, name="Wrec") # Math: W_{i,t} - W_{i,t-1} = w_{i,t}（累计停机次数递推约束）

        # ---- MATLAB 762-778：最小开停机。初始剩余义务 remaining 全为 0（已核数据），锁定行不触发 ----
        # remaining = clip(u0*T_up + (1-u0)*T_down - init_hours, 0, None)，>0 才建锁定行，镜像 MATLAB if 守卫。
        rem = np.clip(gas.u_gas_0 * gas.T_gas_up + (1 - gas.u_gas_0) * gas.T_gas_down
                      - gas.T_gas_initial, 0, None)
        for i in range(N):
            if rem[i] > 0:
                m.addConstr(u[i, :min(T, int(rem[i]))] == gas.u_gas_0[i],
                            name=f"lock_{i}") # Math: u_{i,t} = u_{i,0} \quad \forall t \le L_{i}（初始历史状态剩余时段锁定约束）

        # T_up/T_down 全>0（96/168 共 100 台、8/8 共 24 台，已核数据），MATLAB if 守卫全触发，整块直建。
        firstU = np.maximum(0, np.arange(T) - gas.T_gas_up.astype(int)[:, None] + 1)   # MATLAB 771
        firstD = np.maximum(0, np.arange(T) - gas.T_gas_down.astype(int)[:, None] + 1) # MATLAB 775
        m.addConstr(Vc[:, 1:] - Vc[np.arange(N)[:, None], firstU] <= u, name="minup") # Math: \sum_{\tau=t-T_{up}+1}^{t} v_{i,\tau} \le u_{i,t}（最小连续开机时间约束）
        m.addConstr(Wc[:, 1:] - Wc[np.arange(N)[:, None], firstD] <= 1 - u, name="mindn") # Math: \sum_{\tau=t-T_{dn}+1}^{t} w_{i,\tau} \le 1 - u_{i,t}（最小连续停机时间约束）

    def add_cost(self, dt: float, econ) -> dict:
        """镜像 MATLAB 4.2 节（1027-1033）汇总气电成本表达式。
        加法顺序冻结，不得重排。键名是对外成本合同，冻结。
        气电无六节点/SO2/NOX/烟尘；CO2 按因子现算（无 CO2 变量，MATLAB 991+1032）。

        Args:
            dt (float): 单位步长（小时）。
            econ (EconomicParams): 系统经济参数。

        Returns:
            dict: 气电成本表达式与分项（键名与 solve 层成本 dict 一致）。
        """
        T = self.inputs.m_gas.shape[1]
        var, gas = self.vars, self.inputs
        C_gas_gen   = dt * _wsum(gas.c_gas_gen, var.P_gas, T) # Math: C_{gas,gen} = \Delta t \sum_{t=1}^{T} \sum_{i} c_{gas,i}^{gen} P_{gas,i,t}（气电机组总发电成本）
        C_gas_start = _wsum(gas.c_gas_start, var.v_gas, T) # Math: C_{gas,start} = \sum_{t=1}^{T} \sum_{i} c_{gas,i}^{start} v_{gas,i,t}（气电机组总启动成本）
        C_gas_stop  = _wsum(gas.c_gas_stop, var.w_gas, T) # Math: C_{gas,stop} = \sum_{t=1}^{T} \sum_{i} c_{gas,i}^{stop} w_{gas,i,t}（气电机组总停机成本）
        C_gas_FOM   = float(np.sum(gas.c_gas_FOM * gas.P_gas_rated)) # Math: C_{gas,FOM} = \sum_{i} c_{gas,i}^{FOM} P_{gas,i}^{rated}（气电机组固定运行维护成本）
        C_gas_CO2   = econ.c_CO2 * dt * _wsum(gas.CO2_gas_factor, var.P_gas, T) # Math: C_{gas,CO2} = c_{CO2} \Delta t \sum_{t=1}^{T} \sum_{i} e_{gas,i}^{CO2} P_{gas,i,t}（气电机组碳排放成本）
        C_gas = C_gas_gen + C_gas_start + C_gas_stop + C_gas_FOM + C_gas_CO2 # Math: C_{gas} = C_{gas,gen} + C_{gas,start} + C_{gas,stop} + C_{gas,FOM} + C_{gas,CO2}（气电机组总运行成本）
        return {
            "C_gas": C_gas, "C_gas_gen": C_gas_gen, "C_gas_start": C_gas_start,
            "C_gas_stop": C_gas_stop, "C_gas_FOM": C_gas_FOM, "C_gas_CO2": C_gas_CO2,
        }


if __name__ == "__main__":
    import numpy as np
    import power_opt.reader as reader
    inp = reader.load_inputs()
    ga = Gas()
    ga.load(inp)
    gas = ga.inputs
    print('输入自检: N_gas=%d, m_gas sum=%s, FOM常数=%.2f'
          % (gas.N_gas, gas.m_gas.sum(), float(np.sum(gas.c_gas_FOM * gas.P_gas_rated))))

    m = gp.Model()
    ga.add_vars(m)
    m.update()
    N, T = gas.N_gas, gas.m_gas.shape[1]
    exp_cols = N*T*6 + N*(T+1)*2      # 6个N×T块 + 2个N×(T+1)
    exp_bins = N*T*3                  # u/v/w
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (exp_bins, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == exp_bins, '变量清单与推导不符'
    print('气电变量清单核对通过')

    ga.add_constraints(m, inp)
    m.update()
    exp_rows = 15*N*T + 2*N           # 15个N×T块 + count初值2N；锁定行0（INIT_HOURS已核）
    exp_nz = 37*N*T - 3*N             # 结构化账本：江苏 N=124,T=24→109740、海南 N=3,T=168→18639 均吻合
    # （rem>0 触发锁定行时行数会超出公式，MATLAB 同构造；默认起点已核无锁定行）
    print('气电约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    print('气电非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.NumConstrs == exp_rows, '行数与结构化账本不符'
    assert m.getAttr('NumNZs') == exp_nz, '非零元与结构化账本不符'
    print('气电约束账本核对通过')
