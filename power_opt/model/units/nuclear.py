#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/15 16:00
@Author  : weiyutao
@File    : nuclear.py
核电单元：一个类 Nuclear 自包含四步生命周期
（load → add_vars → add_constraints → add_cost）。
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
from power_opt.reader import _read, _zstr, Inputs
from model.units.base import _wsum


@dataclass
class NuclearInputs:
    """MATLAB 1.6 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_nuc: int                                   # 机组台数
    UnitKey_nuc: list                            # 分区唯一编号
    area_nuc: np.ndarray                         # 机组 → 分区（0 基）
    P_nuc_max: np.ndarray; P_nuc_min: np.ndarray  # 可用上限，MW / 最小出力，MW
    m_nuc: np.ndarray                            # 检修：1 检修，0 可用（N_nuc×T）
    c_nuc_FOM: np.ndarray                        # 本周期元/MW
    c_nuc_gen: np.ndarray                        # 线性单位发电成本，元/MWh（MATLAB 211）
    P_nuc_0: np.ndarray                          # 周期前功率，MW
    RU_nuc: np.ndarray; RD_nuc: np.ndarray       # 上/下爬坡速率
    m_nuc_0: np.ndarray                          # 周期前检修状态，0/1（MATLAB 217）


# ------------------- 2.10 决策变量（MATLAB 651-652 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class NuclearVars:
    """MATLAB 2.10 节决策变量的镜像，共 1 个。"""
    P_nuc: gp.MVar        # 实际出力，MW


class Nuclear:
    """核电单元：inputs/vars 为实例属性，四步生命周期依次组装进模型。"""

    inputs: NuclearInputs      # 核电输入（load 后生效）
    vars: NuclearVars          # 核电决策变量（add_vars 后生效）

    def load(self, inp: Inputs) -> None:
        """镜像 MATLAB 1.6 节（201-224）加载核电输入。缺列缺值即报错；
        结果存 self.inputs。

        Args:
            inp (Inputs): 系统公共输入。

        Returns:
            None
        """
        T = inp.time.T
        mtn_nuc = inp.maintenance[inp.maintenance["Type"].astype(str) == "核电"]

        req_nuc = ["UnitKey", "SUBSTR(C.ID，12)", "CAP", "MIN_POWER_GEN", "GEN_COST",
                   "INIT_POWER", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
                   "INIT_MAINTENANCE", "FOM_COST_PERIOD"]
        """
        CAP：额定容量
        MIN_POWER_GEN：最小技术出力
        GEN_COST：单位发电成本，元/MWh
        INIT_POWER：周期前功率（初始出力）
        RAMP_LIMIT_UP：向上爬坡（允许的功率上升速率）
        RAMP_LIMIT_DOWN：向下爬坡（允许的功率下降速率）
        INIT_MAINTENANCE：周期前检修状态，0/1
        FOM_COST_PERIOD：年度功率运维成本
        """
        nucData = _read(config.UNIT_FILE, "核电", req_nuc)
        N_nuc = len(nucData)
        zone_nuc = [_zstr(v) for v in nucData["SUBSTR(C.ID，12)"]]
        areaPos = {a: i for i, a in enumerate(inp.areas.AreaID)}
        if miss := [z for z in zone_nuc if z not in areaPos]:
            raise KeyError(f"核电分区编号 {set(miss)} 不在分区表中")
        area_nuc = np.array([areaPos[z] for z in zone_nuc])
        f = lambda c: nucData[c].to_numpy(float)
        UnitKey_nuc = nucData["UnitKey"].astype(str).tolist()
        P_nuc_max = f("CAP"); P_nuc_min = f("MIN_POWER_GEN")
        c_nuc_gen = inp.econ.costScale * f("GEN_COST")
        P_nuc_0 = f("INIT_POWER"); RU_nuc = f("RAMP_LIMIT_UP"); RD_nuc = f("RAMP_LIMIT_DOWN")
        m_nuc_0 = f("INIT_MAINTENANCE")
        c_nuc_FOM = inp.econ.fixedCostScale * f("FOM_COST_PERIOD")

        m_nuc = np.zeros((N_nuc, T))
        keyPos = {k: i for i, k in enumerate(UnitKey_nuc)}
        t_prev = inp.time.Time[0] - pd.Timedelta(hours=1)   # 起点前 1 小时（MATLAB 225）
        for _, plan in mtn_nuc.iterrows():
            key = str(plan["UnitKey"])
            if key not in keyPos:
                raise KeyError(f"检修计划机组 {key} 不在核电表中（不猜、不忽略）")
            periods = (inp.time.Time >= plan["StartTime"]) & (inp.time.Time < plan["EndTime"])
            m_nuc[keyPos[key], periods] = 1
            # MATLAB 225-228：起点前 1 小时已在检修窗口 → 初值覆盖为检修态、功率 0
            if plan["StartTime"] <= t_prev < plan["EndTime"]:
                m_nuc_0[keyPos[key]] = 1.0
                P_nuc_0[keyPos[key]] = 0.0

        for name, arr in [("P_nuc_max", P_nuc_max), ("P_nuc_min", P_nuc_min), ("m_nuc", m_nuc)]:
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"{name} 含 NaN/Inf，检查核电输入表")

        self.inputs = NuclearInputs(
            N_nuc=N_nuc, UnitKey_nuc=UnitKey_nuc, area_nuc=area_nuc,
            P_nuc_max=P_nuc_max, P_nuc_min=P_nuc_min, m_nuc=m_nuc, c_nuc_FOM=c_nuc_FOM,
            c_nuc_gen=c_nuc_gen, P_nuc_0=P_nuc_0, RU_nuc=RU_nuc, RD_nuc=RD_nuc,
            m_nuc_0=m_nuc_0,
        )

    def add_vars(self, m: gp.Model) -> NuclearVars:
        """镜像 MATLAB 2.10 节（651-652）创建核电决策变量，存 self.vars。
        连续变量 lb=-inf（镜像 sdpvar 无界默认），界限由 3.10 节约束钉。

        Args:
            m (gp.Model): Gurobi 模型。

        Returns:
            NuclearVars: 决策变量容器（同时存 self.vars，两处同引用）。
        """
        nuc = self.inputs
        N, T = nuc.N_nuc, nuc.m_nuc.shape[1]
        P_nuc = m.addMVar((N, T), lb=-INF, name="P_nuc")
        self.vars = NuclearVars(P_nuc=P_nuc)
        return self.vars

    def add_constraints(self, m: gp.Model, inp: Inputs, sv=None) -> None:
        """一比一对照 MATLAB 3.10 节（892-902）添加核电约束。行数账本
        （T=24, N=10 → 960 行）：4 个 N×T 块（Pmin·u<=P、P<=Pmax·u、爬坡上、
        爬坡下），无 count/锁定。核电不交叉引用系统变量与公共输入，
        胖签名入参忽略。

        Args:
            m (gp.Model): Gurobi 模型。
            inp (Inputs): 系统公共输入（本单元忽略）。
            sv (SystemVars): 系统层决策变量（本单元忽略）。

        Returns:
            None
        """
        nuc = self.inputs
        var = self.vars
        N, T = nuc.N_nuc, nuc.m_nuc.shape[1]
        P = var.P_nuc
        u_nuc = 1 - nuc.m_nuc                                    # MATLAB 893，常数矩阵

        # ---- MATLAB 894-895：P_min·u_nuc <= P_nuc <= P_max·u_nuc（u_nuc 常数，右端全常数）----
        PminU = np.repeat(nuc.P_nuc_min[:, None], T, axis=1) * u_nuc    # repmat(P_min).*u
        PmaxU = np.repeat(nuc.P_nuc_max[:, None], T, axis=1) * u_nuc    # repmat(P_max).*u
        m.addConstr(PminU <= P, name="Pmin") # Math: P_{nuc,i}^{min} u_{nuc,i,t} \le P_{nuc,i,t}（最小技术出力下限约束）
        m.addConstr(P <= PmaxU, name="Pmax") # Math: P_{nuc,i,t} \le P_{nuc,i}^{max} u_{nuc,i,t}（最大技术出力上限约束）

        # ---- MATLAB 896-897：previous 拼接（首列用初值）----
        m_prev = np.concatenate([nuc.m_nuc_0[:, None], nuc.m_nuc[:, :-1]], axis=1)  # 897
        # ---- MATLAB 900：M_nuc = repmat(P_max).*(m_nuc + m_nuc_previous)，常数矩阵 ----
        M_nuc = np.repeat(nuc.P_nuc_max[:, None], T, axis=1) * (nuc.m_nuc + m_prev)
        RU = np.repeat(nuc.RU_nuc[:, None], T, axis=1)
        RD = np.repeat(nuc.RD_nuc[:, None], T, axis=1)
        P0 = nuc.P_nuc_0[:, None]

        # ---- MATLAB 901-902：爬坡，检修边界以 M 放松。首时段 prev = P_nuc_0 常数 ----
        m.addConstr(P[:, 0:1] - P0 <= RU[:, 0:1] + M_nuc[:, 0:1], name="ramp_up_t0") # Math: P_{nuc,i,1} - P_{nuc,i,0} \le R_{nuc,i}^{U} + M_{nuc,i} (m_{nuc,i,1} + m_{nuc,i,0})（首时段向上爬坡约束）
        m.addConstr(P0 - P[:, 0:1] <= RD[:, 0:1] + M_nuc[:, 0:1], name="ramp_dn_t0") # Math: P_{nuc,i,0} - P_{nuc,i,1} \le R_{nuc,i}^{D} + M_{nuc,i} (m_{nuc,i,1} + m_{nuc,i,0})（首时段向下爬坡约束）
        if T > 1:
            m.addConstr(P[:, 1:] - P[:, :-1] <= RU[:, 1:] + M_nuc[:, 1:], name="ramp_up") # Math: P_{nuc,i,t} - P_{nuc,i,t-1} \le R_{nuc,i}^{U} + M_{nuc,i} (m_{nuc,i,t} + m_{nuc,i,t-1})（常规时段向上爬坡约束）
            m.addConstr(P[:, :-1] - P[:, 1:] <= RD[:, 1:] + M_nuc[:, 1:], name="ramp_dn") # Math: P_{nuc,i,t-1} - P_{nuc,i,t} \le R_{nuc,i}^{D} + M_{nuc,i} (m_{nuc,i,t} + m_{nuc,i,t-1})（常规时段向下爬坡约束）

    def add_cost(self, dt: float, econ) -> dict:
        """镜像 MATLAB 4.10 节（1070-1073）汇总核电成本表达式。
        加法顺序冻结，不得重排。键名是对外成本合同，冻结。
        核电无启停/排放；FOM 基数为 P_nuc_max（无受阻率列，额定即上限）。

        Args:
            dt (float): 单位步长（小时）。
            econ (EconomicParams): 系统经济参数。

        Returns:
            dict: 核电成本表达式与分项（键名与 solve 层成本 dict 一致）。
        """
        T = self.inputs.m_nuc.shape[1]
        var, nuc = self.vars, self.inputs
        C_nuc_gen = dt * _wsum(nuc.c_nuc_gen, var.P_nuc, T) # Math: C_{nuc,gen} = \Delta t \sum_{t=1}^{T} \sum_{i} c_{nuc,i}^{gen} P_{nuc,i,t}（核电机组总发电成本）
        C_nuc_FOM = float(np.sum(nuc.c_nuc_FOM * nuc.P_nuc_max)) # Math: C_{nuc,FOM} = \sum_{i} c_{nuc,i}^{FOM} P_{nuc,i}^{max}（核电机组固定运行维护成本）
        C_nuc = C_nuc_gen + C_nuc_FOM # Math: C_{nuc} = C_{nuc,gen} + C_{nuc,FOM}（核电机组总运行成本）
        return {"C_nuc": C_nuc, "C_nuc_gen": C_nuc_gen, "C_nuc_FOM": C_nuc_FOM}


if __name__ == "__main__":
    import power_opt.reader as reader
    inp = reader.load_inputs()
    nu = Nuclear()
    nu.load(inp)
    nuc = nu.inputs
    print('输入自检: N_nuc=%d, m_nuc sum=%s, FOM常数=%.2f'
          % (nuc.N_nuc, nuc.m_nuc.sum(), float(np.sum(nuc.c_nuc_FOM * nuc.P_nuc_max))))

    m = gp.Model()
    nu.add_vars(m)
    m.update()
    N, T = nuc.N_nuc, nuc.m_nuc.shape[1]
    exp_cols = N*T                        # 1 个 N×T 块
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=0  实际=%d' % m.NumBinVars)
    assert m.NumVars == exp_cols and m.NumBinVars == 0, '变量清单与推导不符'
    print('核电变量清单核对通过')

    nu.add_constraints(m, inp)
    m.update()
    exp_rows = 4*N*T                      # 4 个 N×T 块
    exp_nz = 2*N*T + 2*(N + (T-1)*N*2)    # Pmin/Pmax 各 N*T + 爬坡首列N、其余2元
    print('核电约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    print('核电非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    assert m.getAttr('NumNZs') == exp_nz, '非零元与手推账本不符'
    print('核电约束账本核对通过')
