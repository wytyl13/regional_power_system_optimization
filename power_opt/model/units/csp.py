#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/16 15:30
@Author  : weiyutao
@File    : csp.py
光热（CSP，聚光太阳能热发电）单元：一个类 Csp 自包含四步生命周期
（load → add_vars → add_constraints → add_cost）。
机组行来自分区机组信息表[光热]，时序来自算例系统参数[光热时序]（按 UnitKey
取 DNI → 集热场热功率 Q_csp_sf）。
一比一对照 MATLAB 1.18（453-473）/ 2.11（664-675）/ 3.11（913-939）/
4.11（1075-1077）。约束块整体包在 if N_csp>0 内（MATLAB 915 同），
空表 → 零维变量 + 零行约束 + 零成本（海南口径，N_csp=0）。
光热不进弃电惩罚、新能源利用率、CO2 与备用（MATLAB 1090/992-993/998 同）。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
import gurobipy as gp

import config
from power_opt.reader import _read, _zstr, _to_hours, _match_rows, Inputs
from model.units.base import _wsum


@dataclass
class CspInputs:
    """MATLAB 1.18 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_csp: int                                   # 机组台数
    UnitKey_csp: list                            # 分区唯一编号
    area_csp: np.ndarray                         # 机组 → 分区（0 基）
    P_csp_max: np.ndarray                        # 额定电功率，MW
    eta_csp: np.ndarray                          # 热电转换效率（汽轮机+发电机）
    Q_csp_sf: np.ndarray                         # 集热场输出热功率，MWt（N_csp×T）
    HEAT_CH_MAX: np.ndarray; HEAT_DIS_MAX: np.ndarray   # 最大充/放热功率，MWt
    HEAT_LOSS: np.ndarray                        # 逐时段储热热损比例
    HEAT_ETA_IN: np.ndarray; HEAT_ETA_OUT: np.ndarray   # 储热充/放热效率
    HEAT_MIN: np.ndarray; HEAT_MAX: np.ndarray   # 储热量下/上限，MWht
    HEAT_INITIAL: np.ndarray                     # 初始储热量，MWht
    RAMP_LIMIT_UP: np.ndarray; RAMP_LIMIT_DOWN: np.ndarray  # 上/下爬坡速率，MW/h
    START_RAMP: np.ndarray; STOP_RAMP: np.ndarray         # 启动/停机功率变化量，MW
    u_csp_0: np.ndarray                          # 周期前运行状态，0/1
    P_csp_0: np.ndarray                          # 周期前功率，MW
    c_csp_gen: np.ndarray                        # 线性单位发电成本，元/MWh（MATLAB 1076）
    c_csp_FOM: np.ndarray                        # 本周期元/MW（MATLAB 1077）


# ------------------- 2.11 决策变量（MATLAB 664-675 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class CspVars:
    """MATLAB 2.11 节决策变量的镜像：5 连续 + 4 二值 + 储热量（N×T+1）。
    新版 760：放热状态取表达式 u_csp_out = 1 - u_csp_in，不再单独建二值变量。"""
    P_csp_unit: gp.MVar     # 发电功率，MWe
    u_csp: gp.MVar; v_csp: gp.MVar; w_csp: gp.MVar   # 运行/启动/停机状态
    Q_csp_in: gp.MVar       # 充热功率，MWt
    Q_csp_out: gp.MVar      # 放热功率，MWt
    Q_csp_curt: gp.MVar     # 弃热功率，MWt
    Q_csp_pg: gp.MVar       # 进入汽轮机热功率，MWt
    u_csp_in: gp.MVar       # 充热状态，二值
    u_csp_out: object       # 放热状态表达式 1-u_csp_in
    E_csp: gp.MVar          # 储热量，MWht（N×T+1）


class Csp:
    """光热单元：inputs/vars 为实例属性，四步生命周期依次组装进模型。"""

    inputs: CspInputs      # 光热输入（load 后生效）
    vars: CspVars          # 光热决策变量（add_vars 后生效）

    def load(self, inp: Inputs) -> None:
        """镜像 MATLAB 1.18 节（453-473）加载光热输入。缺列缺值即报错；
        结果存 self.inputs。

        Args:
            inp (Inputs): 系统公共输入。

        Returns:
            None
        """
        T = inp.time.T
        req_csp = ["UnitKey", "AreaID", "CAP", "THERMAL_EFFICIENCY",
                   "INIT_STATUS", "INIT_POWER", "SOLAR_MULTIPLE", "DNI_REFERENCE",
                   "HEAT_CH_MAX", "HEAT_DIS_MAX", "HEAT_LOSS",
                   "HEAT_ETA_IN", "HEAT_ETA_OUT", "HEAT_MIN", "HEAT_MAX",
                   "HEAT_INITIAL", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
                   "START_RAMP", "STOP_RAMP", "GEN_COST", "FOM_COST_PERIOD"]
        """
        CAP：额定电功率
        THERMAL_EFFICIENCY：热电转换效率
        INIT_STATUS / INIT_POWER：周期前运行状态与功率
        SOLAR_MULTIPLE：太阳能倍数（集热场与汽轮机额定热功率之比）
        DNI_REFERENCE：设计参考法向直接辐照度，W/m2
        HEAT_CH_MAX / HEAT_DIS_MAX：储热最大充/放热功率
        HEAT_LOSS：储热热损比例
        HEAT_ETA_IN / HEAT_ETA_OUT：储热充/放热效率
        HEAT_MIN / HEAT_MAX / HEAT_INITIAL：储热量下限/上限/初始值
        RAMP_LIMIT_UP / DOWN、START_RAMP / STOP_RAMP：爬坡与启停功率变化量
        GEN_COST：单位发电成本；FOM_COST_PERIOD：年度功率运维成本
        """
        cspData = _read(config.UNIT_FILE, "光热", req_csp)
        N_csp = len(cspData)
        areaPos = {a: i for i, a in enumerate(inp.areas.AreaID)}
        zone_csp = [_zstr(v) for v in cspData["AreaID"]]
        if miss := [z for z in zone_csp if z not in areaPos]:
            raise KeyError(f"光热分区编号 {set(miss)} 不在分区表中")
        area_csp = np.array([areaPos[z] for z in zone_csp], dtype=int)
        f = lambda c: cspData[c].to_numpy(float)
        UnitKey_csp = cspData["UnitKey"].astype(str).tolist()
        P_csp_max = f("CAP"); eta_csp = f("THERMAL_EFFICIENCY")
        u_csp_0 = f("INIT_STATUS"); P_csp_0 = f("INIT_POWER")
        c_csp_gen = inp.econ.costScale * f("GEN_COST")                 # MATLAB 1076
        c_csp_FOM = inp.econ.fixedCostScale * f("FOM_COST_PERIOD")     # MATLAB 1077

        # ---- 光热时序（MATLAB 462-473）：按 UnitKey 取 DNI → 集热场热功率 ----
        cspTimeData = _read(config.SYSTEM_FILE, "光热时序",
                            ["UnitKey", "Time", "DNI_W_m2"])
        DNI = np.full((N_csp, T), np.nan)
        keysTime = cspTimeData["UnitKey"].astype(str)
        for i, key in enumerate(UnitKey_csp):
            rows = cspTimeData[keysTime == key]
            if rows.empty:
                raise KeyError(f"光热时序缺机组 {key} 的行（不补值）")
            timeRows = _match_rows(_to_hours(rows["Time"]),
                                   inp.time.Time, f"光热时序[{key}]")   # MATLAB 469
            DNI[i, :] = rows["DNI_W_m2"].to_numpy(float)[timeRows]     # MATLAB 470
        # MATLAB 472-473：Q_sf = SOLAR_MULTIPLE·P_max/(η·DNI_REF)·DNI，MWt
        # 分子分母都显式 [:, None] 成 (N,1)：1 维与 (N,1) 直接广播会错成 (N,N)
        Q_csp_sf = np.repeat((f("SOLAR_MULTIPLE") * P_csp_max
                              / (eta_csp * f("DNI_REFERENCE")))[:, None], T, axis=1) * DNI

        for name, arr in [("P_csp_max", P_csp_max), ("eta_csp", eta_csp),
                          ("Q_csp_sf", Q_csp_sf), ("DNI", DNI)]:
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"{name} 含 NaN/Inf，检查光热输入表")

        self.inputs = CspInputs(
            N_csp=N_csp, UnitKey_csp=UnitKey_csp, area_csp=area_csp,
            P_csp_max=P_csp_max, eta_csp=eta_csp, Q_csp_sf=Q_csp_sf,
            HEAT_CH_MAX=f("HEAT_CH_MAX"), HEAT_DIS_MAX=f("HEAT_DIS_MAX"),
            HEAT_LOSS=f("HEAT_LOSS"),
            HEAT_ETA_IN=f("HEAT_ETA_IN"), HEAT_ETA_OUT=f("HEAT_ETA_OUT"),
            HEAT_MIN=f("HEAT_MIN"), HEAT_MAX=f("HEAT_MAX"),
            HEAT_INITIAL=f("HEAT_INITIAL"),
            RAMP_LIMIT_UP=f("RAMP_LIMIT_UP"), RAMP_LIMIT_DOWN=f("RAMP_LIMIT_DOWN"),
            START_RAMP=f("START_RAMP"), STOP_RAMP=f("STOP_RAMP"),
            u_csp_0=u_csp_0, P_csp_0=P_csp_0,
            c_csp_gen=c_csp_gen, c_csp_FOM=c_csp_FOM,
        )

    def add_vars(self, m: gp.Model) -> CspVars:
        """镜像 MATLAB 2.11 节（664-675）创建光热决策变量，存 self.vars。
        连续变量 lb=-inf（镜像 sdpvar 无界默认），界限由 3.11 节约束钉；
        建序冻结：P, u, v, w, Q_in, Q_out, Q_curt, Q_pg, u_in, u_out, E。

        Args:
            m (gp.Model): Gurobi 模型。

        Returns:
            CspVars: 决策变量容器（同时存 self.vars，两处同引用）。
        """
        csp = self.inputs
        N, T = csp.N_csp, csp.Q_csp_sf.shape[1]
        P_csp_unit = m.addMVar((N, T), lb=-INF, name="P_csp_unit")     # 665
        u_csp = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_csp")   # 666
        v_csp = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="v_csp")   # 667
        w_csp = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="w_csp")   # 668
        Q_csp_in = m.addMVar((N, T), lb=-INF, name="Q_csp_in")         # 669
        Q_csp_out = m.addMVar((N, T), lb=-INF, name="Q_csp_out")       # 670
        Q_csp_curt = m.addMVar((N, T), lb=-INF, name="Q_csp_curt")     # 671
        Q_csp_pg = m.addMVar((N, T), lb=-INF, name="Q_csp_pg")         # 672
        u_csp_in = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_csp_in")    # 673
        u_csp_out = 1 - u_csp_in                   # 新版 760：放热状态为表达式
        E_csp = m.addMVar((N, T + 1), lb=-INF, name="E_csp")           # 675
        self.vars = CspVars(P_csp_unit=P_csp_unit, u_csp=u_csp, v_csp=v_csp,
                            w_csp=w_csp, Q_csp_in=Q_csp_in, Q_csp_out=Q_csp_out,
                            Q_csp_curt=Q_csp_curt, Q_csp_pg=Q_csp_pg,
                            u_csp_in=u_csp_in, u_csp_out=u_csp_out, E_csp=E_csp)
        return self.vars

    def add_constraints(self, m: gp.Model, inp: Inputs, sv=None) -> None:
        """一比一对照 MATLAB 3.11 节（913-939）添加光热约束。整块包在
        if N_csp>0 内（MATLAB 915 同），空表零行。行数账本
        （N×T 块 17 个 + 储热上下限 2N(T+1) + 初末 2N）：
        期望行数 = 19·N·T + 4·N。光热不交叉引用系统变量与公共输入，
        胖签名入参忽略。

        Args:
            m (gp.Model): Gurobi 模型。
            inp (Inputs): 系统公共输入（本单元忽略）。
            sv (SystemVars): 系统层决策变量（本单元忽略）。

        Returns:
            None
        """
        csp = self.inputs
        var = self.vars
        N, T = csp.N_csp, csp.Q_csp_sf.shape[1]
        if N == 0:                                                     # MATLAB 915
            return
        dt = inp.time.Delta_t
        rp = lambda a: np.repeat(np.asarray(a)[:, None], T, axis=1)    # repmat(N×1 → N×T)

        # ---- MATLAB 916：热量非负 ----
        m.addConstr(var.Q_csp_in >= 0, name="heat_in_pos") # Math: Q_{csp,i,t}^{in} \ge 0（充热功率非负）
        m.addConstr(var.Q_csp_out >= 0, name="heat_out_pos") # Math: Q_{csp,i,t}^{out} \ge 0（放热功率非负）
        m.addConstr(var.Q_csp_curt >= 0, name="heat_curt_pos") # Math: Q_{csp,i,t}^{curt} \ge 0（弃热功率非负）
        m.addConstr(var.Q_csp_pg >= 0, name="heat_pg_pos") # Math: Q_{csp,i,t}^{pg} \ge 0（汽轮机吸热非负）

        # ---- MATLAB 1031-1033：充/放热上限×状态（u_out=1-u_in 表达式，互斥行已删）、充热≤集热 ----
        m.addConstr(var.Q_csp_in <= rp(csp.HEAT_CH_MAX) * var.u_csp_in, name="heat_in_max") # Math: Q_{csp,i,t}^{in} \le \bar{Q}_{csp,i}^{in} u_{csp,i,t}^{in}（充热功率上限约束）
        m.addConstr(var.Q_csp_out <= rp(csp.HEAT_DIS_MAX) * var.u_csp_out, name="heat_out_max") # Math: Q_{csp,i,t}^{out} \le \bar{Q}_{csp,i}^{out} (1 - u_{csp,i,t}^{in})（放热功率上限约束，单整数互斥）
        m.addConstr(var.Q_csp_in <= csp.Q_csp_sf, name="heat_in_sf") # Math: Q_{csp,i,t}^{in} \le Q_{csp,i,t}^{sf}（充热不得超过集热功率）

        # ---- MATLAB 920-922：储热量平衡（热损 + 充放热效率）----
        m.addConstr(var.E_csp[:, 1:] == rp(1 - csp.HEAT_LOSS) * var.E_csp[:, :-1]
                    + rp(csp.HEAT_ETA_IN) * var.Q_csp_in * dt
                    - rp(1.0 / csp.HEAT_ETA_OUT) * var.Q_csp_out * dt, name="heat_balance") # Math: E_{csp,i,t+1} = (1-\ell_{csp,i}) E_{csp,i,t} + \eta_{csp,i}^{in} Q_{csp,i,t}^{in} \Delta t - Q_{csp,i,t}^{out} \Delta t / \eta_{csp,i}^{out}（储热量动态平衡约束）

        # ---- MATLAB 923-925：储热量上下限、初末=初值 ----
        rpT1 = lambda a: np.repeat(np.asarray(a)[:, None], T + 1, axis=1)
        m.addConstr(rpT1(csp.HEAT_MIN) <= var.E_csp, name="heat_min") # Math: \underline{E}_{csp,i} \le E_{csp,i,t}（储热量下限约束）
        m.addConstr(var.E_csp <= rpT1(csp.HEAT_MAX), name="heat_max") # Math: E_{csp,i,t} \le \bar{E}_{csp,i}（储热量上限约束）
        m.addConstr(var.E_csp[:, 0] == csp.HEAT_INITIAL, name="heat_init") # Math: E_{csp,i,1} = E_{csp,i}^{0}（初始储热量约束）
        m.addConstr(var.E_csp[:, T] == csp.HEAT_INITIAL, name="heat_final") # Math: E_{csp,i,T+1} = E_{csp,i}^{0}（周期末储热量约束）

        # ---- MATLAB 926-927：汽轮机热平衡、热电转换 ----
        m.addConstr(var.Q_csp_pg == csp.Q_csp_sf - var.Q_csp_curt
                    - var.Q_csp_in + var.Q_csp_out, name="pg_balance") # Math: Q_{csp,i,t}^{pg} = Q_{csp,i,t}^{sf} - Q_{csp,i,t}^{curt} - Q_{csp,i,t}^{in} + Q_{csp,i,t}^{out}（进入汽轮机的热功率平衡约束）
        m.addConstr(var.P_csp_unit == rp(csp.eta_csp) * var.Q_csp_pg, name="eta_convert") # Math: P_{csp,i,t} = \eta_{csp,i} Q_{csp,i,t}^{pg}（发电功率与热电转换约束）

        # ---- MATLAB 928-930：运行/启停逻辑（首列用初值）----
        u_csp_previous = np.concatenate([csp.u_csp_0[:, None], var.u_csp[:, :-1]], axis=1)  # 928
        P_csp_previous = np.concatenate([csp.P_csp_0[:, None], var.P_csp_unit[:, :-1]], axis=1)  # 929
        m.addConstr(var.u_csp - u_csp_previous == var.v_csp - var.w_csp, name="logic") # Math: u_{csp,i,t} - u_{csp,i,t-1} = v_{csp,i,t} - w_{csp,i,t}（运行状态与启停逻辑约束）
        m.addConstr(var.v_csp + var.w_csp <= 1, name="vw_mutex") # Math: v_{csp,i,t} + w_{csp,i,t} \le 1（启停互斥约束）

        # ---- MATLAB 931：出力上下限 ----
        m.addConstr(var.P_csp_unit >= 0, name="pmin") # Math: P_{csp,i,t} \ge 0（发电功率非负）
        m.addConstr(var.P_csp_unit <= rp(csp.P_csp_max) * var.u_csp, name="pmax") # Math: P_{csp,i,t} \le \bar{P}_{csp,i} u_{csp,i,t}（最大出力上限约束）

        # ---- MATLAB 932-937：爬坡（含启停修正）----
        m.addConstr(var.P_csp_unit - P_csp_previous
                    <= rp(csp.RAMP_LIMIT_UP) * u_csp_previous
                    + rp(csp.START_RAMP) * (var.u_csp - u_csp_previous), name="ramp_up") # Math: P_{csp,i,t} - P_{csp,i,t-1} \le R_{csp,i}^{U} u_{csp,i,t-1} + S_{csp,i}^{U} (u_{csp,i,t} - u_{csp,i,t-1})（向上爬坡约束含启动修正）
        m.addConstr(P_csp_previous - var.P_csp_unit
                    <= rp(csp.RAMP_LIMIT_DOWN) * var.u_csp
                    + rp(csp.STOP_RAMP) * (u_csp_previous - var.u_csp), name="ramp_dn") # Math: P_{csp,i,t-1} - P_{csp,i,t} \le R_{csp,i}^{D} u_{csp,i,t} + S_{csp,i}^{D} (u_{csp,i,t-1} - u_{csp,i,t})（向下爬坡约束含停机修正）

    def add_cost(self, dt: float, econ) -> dict:
        """镜像 MATLAB 4.11 节（1075-1077）汇总光热成本表达式。
        加法顺序冻结，不得重排。键名是对外成本合同，冻结。
        FOM 基数为 P_csp_max；无启停/排放成本。

        Args:
            dt (float): 单位步长（小时）。
            econ (EconomicParams): 系统经济参数（折算已在 load 完成）。

        Returns:
            dict: 光热成本表达式与分项（键名与 solve 层成本 dict 一致）。
        """
        T = self.inputs.Q_csp_sf.shape[1]
        csp = self.inputs
        C_csp_gen = dt * _wsum(csp.c_csp_gen, self.vars.P_csp_unit, T) # Math: C_{csp,gen} = \Delta t \sum_{t=1}^{T} \sum_{i} c_{csp,i}^{gen} P_{csp,i,t}（光热机组总发电成本）
        C_csp_FOM = float(np.sum(csp.c_csp_FOM * csp.P_csp_max)) # Math: C_{csp,FOM} = \sum_{i} c_{csp,i}^{FOM} \bar{P}_{csp,i}（光热机组固定运行维护成本）
        C_csp = C_csp_gen + C_csp_FOM # Math: C_{csp} = C_{csp,gen} + C_{csp,FOM}（光热机组总运行成本）
        return {"C_csp": C_csp, "C_csp_gen": C_csp_gen, "C_csp_FOM": C_csp_FOM}


if __name__ == "__main__":
    import power_opt.reader as reader
    inp = reader.load_inputs()
    cs = Csp()
    cs.load(inp)
    csp = cs.inputs
    print('输入自检: N_csp=%d, FOM常数=%.2f'
          % (csp.N_csp, float(np.sum(csp.c_csp_FOM * csp.P_csp_max))))

    m = gp.Model()
    cs.add_vars(m)
    m.update()
    N, T = csp.N_csp, csp.Q_csp_sf.shape[1]
    exp_cols = 9*N*T + N*(T+1)         # 9 个 N×T 块 + E_csp N×(T+1)（u_out 为表达式）
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (4*N*T, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == 4*N*T, '变量清单与推导不符'
    print('光热变量清单核对通过')

    cs.add_constraints(m, inp)
    m.update()
    exp_rows = 18*N*T + 4*N            # 16 个 N×T 块（互斥行已删） + 储热上下限 2N(T+1) + 初末 2N
    print('光热约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    print('光热约束账本核对通过')
