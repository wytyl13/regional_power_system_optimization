#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/15 16:35
@Author  : weiyutao
@File    : electrochemical.py
电化学储能单元：一个类 Electrochemical 自包含四步生命周期
（load → add_vars → add_constraints → add_cost）。
读取的表：分区机组信息表[电化学储能]（AreaID 分区列，非 SUBSTR）。
与抽蓄的差异：约束 3.5 带 if N_es>0 ；
SOC 递推含自放电 (1-loss)；成本有 degrade 变动项。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
import gurobipy as gp

import config
from power_opt.reader import _read, _zstr, Inputs
from model.units.base import _wsum


@dataclass
class ESInputs:
    """MATLAB 1.16 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_es: int                                   # 机组台数
    UnitKey_es: list                            # 分区唯一编号
    name_es: list                               # 机组名（八~十一节分析表用，MATLAB esData.NAME）
    area_es: np.ndarray                         # 机组 → 分区（0 基，AreaID 列）
    P_es_max: np.ndarray                        # 功率上限，MW
    E_es_max: np.ndarray; E_es_min: np.ndarray; E_es_upper: np.ndarray  # 能量上限/下限/上界，MWh
    E_es_initial: np.ndarray                    # 初始能量 = E_es_max × SOC_INITIAL（MATLAB 386）
    eta_es_ch: np.ndarray; eta_es_dis: np.ndarray  # 充/放电效率
    loss_es: np.ndarray                         # 自放电率
    c_es_degrade: np.ndarray                    # 元/MWh 吞吐量（MATLAB 388-389，公式含除法）
    c_es_FOM: np.ndarray                        # 本周期功率运维，元/MW
    c_es_energy_FOM: np.ndarray                 # 本周期能量运维，元/MWh


# ------------------- 2.5 决策变量（MATLAB 613-619 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class ESVars:
    """MATLAB 2.5 节决策变量的镜像。新版 708：放电状态取表达式
    u_es_dis = 1 - u_es_ch（非充电即允许放电），不再单独建二值变量。"""
    P_es_charge: gp.MVar; P_es_discharge: gp.MVar   # 充/放电功率，MW
    u_es_ch: gp.MVar                                 # 充电状态，二值
    u_es_dis: object                                 # 放电状态表达式 1-u_es_ch
    E_es: gp.MVar                                    # 首列初始，后续时段末
    R_es_hot: gp.MVar                                # 热备用，MW


class Electrochemical:
    """电化学储能单元：inputs/vars 为实例属性，四步生命周期依次组装进模型。"""

    inputs: ESInputs      # 电储能输入（load 后生效）
    vars: ESVars          # 电储能决策变量（add_vars 后生效）

    def load(self, inp: Inputs) -> None:
        """镜像 MATLAB 1.16 节（380-396）加载电储能输入。缺列缺值即报错；
        结果存 self.inputs。

        Args:
            inp (Inputs): 系统公共输入。

        Returns:
            None
        """
        req_es = ["UnitKey", "AreaID", "CAP", "ENERGY_MWH", "SOC_MIN", "SOC_MAX",
                  "SOC_INITIAL", "ETA_CHARGE", "ETA_DISCHARGE", "SELF_DISCHARGE",
                  "POWER_COST", "ENERGY_COST", "REPLACE_FACTOR", "MAX_CYCLES",
                  "FOM_COST_PERIOD", "FOM_ENERGY_PERIOD", "NAME"]
        """
        AreaID：分区编号（分区表主键）
        CAP：额定容量（充/放功率上限）
        ENERGY_MWH：最大等效储能容量
        SOC_MIN：最小SOC，0~1
        SOC_MAX：最大SOC，0~1
        SOC_INITIAL：初始SOC，0~1
        ETA_CHARGE：充电效率，0~1
        ETA_DISCHARGE：放电效率，0~1
        SELF_DISCHARGE：每小时自放电率，1/h
        POWER_COST：单位功率投资成本，元/MW
        ENERGY_COST：单位能量投资成本，元/MWh
        REPLACE_FACTOR：替换系数，无量纲
        MAX_CYCLES：最大满循环次数，次
        FOM_COST_PERIOD：年度功率运维成本
        FOM_ENERGY_PERIOD：年度能量运维成本
        """
        esData = _read(config.UNIT_FILE, "电化学储能", req_es)
        N_es = len(esData)
        zone_es = [_zstr(v) for v in esData["AreaID"]]
        areaPos = {a: i for i, a in enumerate(inp.areas.AreaID)}
        if miss := [z for z in zone_es if z not in areaPos]:
            raise KeyError(f"电储能分区编号 {set(miss)} 不在分区表中")
        area_es = np.array([areaPos[z] for z in zone_es], dtype=int)
        f = lambda c: esData[c].to_numpy(float)
        UnitKey_es = esData["UnitKey"].astype(str).tolist()
        name_es = esData["NAME"].astype(str).tolist()                 # 八~十一节分析表用
        P_es_max = f("CAP"); E_es_max = f("ENERGY_MWH")
        E_es_min = E_es_max * f("SOC_MIN")                    # MATLAB 384
        E_es_upper = E_es_max * f("SOC_MAX")                  # 385
        E_es_initial = E_es_max * f("SOC_INITIAL")            # 386
        eta_es_ch = f("ETA_CHARGE"); eta_es_dis = f("ETA_DISCHARGE")
        loss_es = f("SELF_DISCHARGE")
        # MATLAB 388-389：c_es_degrade = (P_max·POWER_COST + E_max·ENERGY_COST)
        #                          × REPLACE_FACTOR / (2·E_max·MAX_CYCLES)
        c_es_degrade = ((P_es_max * f("POWER_COST") + E_es_max * f("ENERGY_COST"))
                        * f("REPLACE_FACTOR") / (2 * E_es_max * f("MAX_CYCLES")))
        c_es_FOM = inp.econ.fixedCostScale * f("FOM_COST_PERIOD")            # 391
        c_es_energy_FOM = inp.econ.fixedCostScale * f("FOM_ENERGY_PERIOD")   # 392

        for name, arr in [("P_es_max", P_es_max), ("E_es_initial", E_es_initial),
                          ("c_es_degrade", c_es_degrade), ("eta_es_ch", eta_es_ch)]:
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"{name} 含 NaN/Inf，检查电储能输入表")

        self.inputs = ESInputs(
            N_es=N_es, UnitKey_es=UnitKey_es, name_es=name_es, area_es=area_es, P_es_max=P_es_max,
            E_es_max=E_es_max, E_es_min=E_es_min, E_es_upper=E_es_upper,
            E_es_initial=E_es_initial, eta_es_ch=eta_es_ch, eta_es_dis=eta_es_dis,
            loss_es=loss_es, c_es_degrade=c_es_degrade, c_es_FOM=c_es_FOM,
            c_es_energy_FOM=c_es_energy_FOM,
        )
        # 无检修矩阵（ESInputs 不含 N×T 量），T 在 load 时缓存供 add_vars 建形
        self._T = inp.time.T

    def add_vars(self, m: gp.Model) -> ESVars:
        """镜像 MATLAB 2.5 节（613-619）创建电储能决策变量，存 self.vars。
        连续变量 lb=-inf、二值变量照 MATLAB 614-619。

        Args:
            m (gp.Model): Gurobi 模型。

        Returns:
            ESVars: 决策变量容器（同时存 self.vars，两处同引用）。
        """
        es = self.inputs
        N, T = es.N_es, self._T
        P_es_charge    = m.addMVar((N, T), lb=-INF, name="P_es_charge")
        P_es_discharge = m.addMVar((N, T), lb=-INF, name="P_es_discharge")
        u_es_ch  = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_es_ch")
        u_es_dis = 1 - u_es_ch                    # 新版 708：放电状态为表达式
        E_es     = m.addMVar((N, T + 1), lb=-INF, name="E_es")
        R_es_hot = m.addMVar((N, T), lb=-INF, name="R_es_hot")
        self.vars = ESVars(P_es_charge=P_es_charge, P_es_discharge=P_es_discharge,
                           u_es_ch=u_es_ch, u_es_dis=u_es_dis, E_es=E_es, R_es_hot=R_es_hot)
        return self.vars

    def add_constraints(self, m: gp.Model, inp: Inputs, sv=None) -> None:
        """一比一对照 MATLAB 3.5 节（817-831）添加电储能约束。
        if N_es>0 守卫逐行照抄（坑 4：T=1 也会暴露）。
        行数账本见 docstring 内注释。省级备用时长从 inp.prov 领行。

        Args:
            m (gp.Model): Gurobi 模型。
            inp (Inputs): 系统公共输入（Delta_t、省级备用时长）。
            sv (SystemVars): 系统层决策变量（本单元忽略）。

        Returns:
            None
        """
        es = self.inputs
        var = self.vars
        if es.N_es == 0:                                              # MATLAB 819 守卫
            return
        N, T, Delta_t = es.N_es, var.E_es.shape[1] - 1, inp.time.Delta_t
        Pch, Pdis = var.P_es_charge, var.P_es_discharge
        uch, udis = var.u_es_ch, var.u_es_dis
        E, R = var.E_es, var.R_es_hot
        Pmax = np.repeat(es.P_es_max[:, None], T, axis=1)

        # ---- MATLAB 820：0 <= P_ch、P_ch <= Pmax·u_ch ----
        m.addConstr(0 <= Pch, name="Pch_lb") # Math: P_{c,i,t} \ge 0（充电功率非负约束）
        m.addConstr(Pch <= Pmax * uch, name="Pch_ub") # Math: P_{c,i,t} \le P_{es,i}^{max} u_{ch,i,t}（充电状态功率上限约束）
        # ---- MATLAB 938：0 <= P_dis、P_dis <= Pmax·u_dis（u_dis=1-u_ch 表达式，互斥行已删）----
        m.addConstr(0 <= Pdis, name="Pdis_lb") # Math: P_{d,i,t} \ge 0（放电功率非负约束）
        m.addConstr(Pdis <= Pmax * udis, name="Pdis_ub") # Math: P_{d,i,t} \le P_{es,i}^{max} (1 - u_{ch,i,t})（放电状态功率上限约束，单整数互斥）
        # ---- MATLAB 939：SOC 递推（含自放电 1-loss；充 +、放 −）----
        loss = np.repeat(es.loss_es[:, None], T, axis=1)
        eta_ch = np.repeat(es.eta_es_ch[:, None], T, axis=1)
        inv_eta_dis = np.repeat((1.0 / es.eta_es_dis)[:, None], T, axis=1)
        m.addConstr(E[:, 1:] == (1 - loss) * E[:, :-1]
                    + eta_ch * Pch * Delta_t
                    - inv_eta_dis * Pdis * Delta_t, name="soc") # Math: E_{i,t} = (1 - \sigma_{i}) E_{i,t-1} + \eta_{ch,i} P_{c,i,t} \Delta t - P_{d,i,t} \Delta t / \eta_{dis,i}（储能能量递推平衡约束）
        # ---- MATLAB 825：E 界（E_es(:) 展开 N×(T+1) 行）----
        Emin = np.repeat(es.E_es_min[:, None], T + 1, axis=1)
        Eup = np.repeat(es.E_es_upper[:, None], T + 1, axis=1)
        m.addConstr(Emin <= E, name="E_lb") # Math: E_{i,t} \ge SOC_{i}^{min} E_{i}^{N}（储能能量下限约束）
        m.addConstr(E <= Eup, name="E_ub") # Math: E_{i,t} \le SOC_{i}^{max} E_{i}^{N}（储能能量上限约束）
        # ---- MATLAB 826：首末能量相等 ----
        E0 = es.E_es_initial[:, None]
        m.addConstr(E[:, 0:1] == E0, name="E_init") # Math: E_{i,0} = E_{i}^{ini}（周期能量初值约束）
        m.addConstr(E[:, T:T + 1] == E0, name="E_end") # Math: E_{i,T} = E_{i}^{ini}（周期末能量回归初值约束）
        # ---- MATLAB 827/828：热备用受放电余量限制（上界 Pmax·(1-u_ch)）----
        m.addConstr(0 <= R, name="R_lb") # Math: R_{es,i,t} \ge 0（热备用容量非负约束）
        m.addConstr(Pdis + R <= Pmax * (1 - uch), name="R_ub") # Math: P_{d,i,t} + R_{es,i,t} \le P_{es,i}^{max} (1 - u_{ch,i,t})（放电功率与热备用容量及充放互斥上限约束）
        # ---- MATLAB 829：备用能量限制 rd·R <= eta_dis·(E(2:T+1)-Emin) ----
        rd = inp.prov.reserveDuration[inp.areas.areaProvince[es.area_es]]        # 省级参数领行
        rdT = np.repeat(np.asarray(rd, float)[:, None], T, axis=1)
        eta_dis = np.repeat(es.eta_es_dis[:, None], T, axis=1)
        EminT = np.repeat(es.E_es_min[:, None], T, axis=1)
        m.addConstr(rdT * R <= eta_dis * (E[:, 1:] - EminT), name="R_energy") # Math: r_{d} R_{es,i,t} \le \eta_{dis,i} (E_{i,t} - E_{i}^{min})（热备用容量可用能量限制约束）

    def add_cost(self, dt: float, econ) -> dict:
        """镜像 MATLAB 4.5 节（1046-1049）汇总电储能成本表达式。
        1047: Delta_t*sum(c'*(P_ch+P_dis))，拆两次 _wsum 数值等价（MLinExpr 不进 _wsum）。
        加法顺序冻结，不得重排。键名是对外成本合同，冻结。

        Args:
            dt (float): 单位步长（小时）。
            econ (EconomicParams): 系统经济参数（本单元无外部价格项、未用）。

        Returns:
            dict: 电储能成本表达式与分项（键名与 solve 层成本 dict 一致）。
        """
        T = self.vars.E_es.shape[1] - 1
        var, es = self.vars, self.inputs
        C_es_degrade = dt * (_wsum(es.c_es_degrade, var.P_es_charge, T)
                             + _wsum(es.c_es_degrade, var.P_es_discharge, T)) # Math: C_{es,deg} = \Delta t \sum_{t=1}^{T} \sum_{i} c_{es,i}^{deg} (P_{c,i,t} + P_{d,i,t})（电储能总寿命损耗成本）
        C_es_FOM = float(np.sum(es.c_es_FOM * es.P_es_max
                                + es.c_es_energy_FOM * es.E_es_max)) # Math: C_{es,FOM} = \sum_{i} (c_{es,i}^{FOM} P_{es,i}^{max} + c_{es,i}^{E,FOM} E_{es,i}^{max})（电储能固定运行维护成本）
        C_es = C_es_degrade + C_es_FOM # Math: C_{es} = C_{es,deg} + C_{es,FOM}（电储能总运行成本）
        return {"C_es": C_es, "C_es_degrade": C_es_degrade, "C_es_FOM": C_es_FOM}


if __name__ == "__main__":
    import power_opt.reader as reader
    inp = reader.load_inputs()
    ech = Electrochemical()
    ech.load(inp)
    es = ech.inputs
    T = inp.time.T
    print('输入自检: N_es=%d, degrade=%.2f 元/MWh吞吐, FOM常数=%.2f'
          % (es.N_es, float(es.c_es_degrade[0]),
             float(np.sum(es.c_es_FOM * es.P_es_max + es.c_es_energy_FOM * es.E_es_max))))

    m = gp.Model()
    ech.add_vars(m)
    m.update()
    N = es.N_es
    exp_cols = 4 * N * T + N * (T + 1)           # 3 个 N×T 连续 + E_es + R_hot（u_dis 为表达式）
    exp_bin = N * T
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (exp_bin, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == exp_bin, '变量清单与推导不符'
    print('电储能变量清单核对通过')

    ech.add_constraints(m, inp)
    m.update()
    exp_rows = 8 * N * T + 2 * N * (T + 1) + 2 * N   # 8 块（互斥行已删） + E 界×2 + 首末×2
    # 逐块每行元数：Pch/Pdis lb=1、ub=2、递推=4、R_lb=1、R_ub=3、R_energy=2
    # → 16/行（新版：无互斥行）
    exp_nz = 16 * N * T + 2 * N * (T + 1) + 2 * N
    print('电储能约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    print('电储能非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.getAttr('NumNZs') == exp_nz, '非零元与手推账本不符'
    print('电储能约束账本核对通过')
