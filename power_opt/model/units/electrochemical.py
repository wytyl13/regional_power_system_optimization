#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/24 09:10
@Author  : weiyutao
@File    : electrochemical.py
电化学储能模块：一比一对照 MATLAB 1.16 / 2.5 / 3.5 / 4.5。
读取的表：分区机组信息表[电化学储能]（AreaID 分区列，非 SUBSTR）。
与抽蓄的差异：约束 3.5 带 if N_es>0 守卫（坑 1 镜像，守卫逐行照抄不增不删）；
SOC 递推含自放电 (1-loss)；成本有 degrade 变动项（吞吐量单价，公式含除法）。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
import gurobipy as gp

import config
from reader import _read, _zstr, Inputs


@dataclass
class ESInputs:
    """MATLAB 1.16 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_es: int
    UnitKey_es: list
    name_es: list                # 机组名（八~十一节分析表用，MATLAB esData.NAME）
    area_es: np.ndarray           # 机组 → 分区（0 基，AreaID 列）
    P_es_max: np.ndarray          # MW
    E_es_max: np.ndarray; E_es_min: np.ndarray; E_es_upper: np.ndarray
    E_es_initial: np.ndarray      # = E_es_max × SOC_INITIAL（MATLAB 386）
    eta_es_ch: np.ndarray; eta_es_dis: np.ndarray
    loss_es: np.ndarray           # 自放电率
    c_es_degrade: np.ndarray      # 元/MWh 吞吐量（MATLAB 388-389，公式含除法）
    c_es_FOM: np.ndarray          # 本周期功率运维，元/MW
    c_es_energy_FOM: np.ndarray   # 本周期能量运维，元/MWh


def load_es(inp: Inputs) -> ESInputs:
    """镜像 MATLAB 1.16 节（380-396）。缺列缺值即报错。"""
    req_es = ["UnitKey", "AreaID", "CAP", "ENERGY_MWH", "SOC_MIN", "SOC_MAX",
              "SOC_INITIAL", "ETA_CHARGE", "ETA_DISCHARGE", "SELF_DISCHARGE",
              "POWER_COST", "ENERGY_COST", "REPLACE_FACTOR", "MAX_CYCLES",
              "FOM_COST_PERIOD", "FOM_ENERGY_PERIOD", "NAME"]
    esData = _read(config.UNIT_FILE, "电化学储能", req_es)
    N_es = len(esData)
    zone_es = [_zstr(v) for v in esData["AreaID"]]
    areaPos = {a: i for i, a in enumerate(inp.AreaID)}
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
    c_es_FOM = inp.fixedCostScale * f("FOM_COST_PERIOD")            # 391
    c_es_energy_FOM = inp.fixedCostScale * f("FOM_ENERGY_PERIOD")   # 392

    for name, arr in [("P_es_max", P_es_max), ("E_es_initial", E_es_initial),
                      ("c_es_degrade", c_es_degrade), ("eta_es_ch", eta_es_ch)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查电储能输入表")

    return ESInputs(
        N_es=N_es, UnitKey_es=UnitKey_es, name_es=name_es, area_es=area_es, P_es_max=P_es_max,
        E_es_max=E_es_max, E_es_min=E_es_min, E_es_upper=E_es_upper,
        E_es_initial=E_es_initial, eta_es_ch=eta_es_ch, eta_es_dis=eta_es_dis,
        loss_es=loss_es, c_es_degrade=c_es_degrade, c_es_FOM=c_es_FOM,
        c_es_energy_FOM=c_es_energy_FOM,
    )


# ------------------- 2.5 决策变量（MATLAB 613-619 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class ESVars:
    """MATLAB 2.5 节决策变量的镜像，共 6 个。"""
    P_es_charge: gp.MVar; P_es_discharge: gp.MVar   # 充/放电功率，MW
    u_es_ch: gp.MVar; u_es_dis: gp.MVar             # 充/放电状态，二值
    E_es: gp.MVar                                    # 首列初始，后续时段末
    R_es_hot: gp.MVar                                # 热备用，MW


def add_vars(m: gp.Model, es: ESInputs, T: int) -> ESVars:
    """连续变量 lb=-inf、二值变量照 MATLAB 614-619。"""
    N = es.N_es
    P_es_charge    = m.addMVar((N, T), lb=-INF, name="P_es_charge")     # 614
    P_es_discharge = m.addMVar((N, T), lb=-INF, name="P_es_discharge")  # 615
    u_es_ch  = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_es_ch")   # 616
    u_es_dis = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_es_dis")  # 617
    E_es     = m.addMVar((N, T + 1), lb=-INF, name="E_es")              # 618
    R_es_hot = m.addMVar((N, T), lb=-INF, name="R_es_hot")              # 619
    return ESVars(P_es_charge=P_es_charge, P_es_discharge=P_es_discharge,
                  u_es_ch=u_es_ch, u_es_dis=u_es_dis, E_es=E_es, R_es_hot=R_es_hot)


# ------------------- 3.5 电储能约束（MATLAB 817-831 行） -------------------

def add_constraints(m: gp.Model, es: ESInputs, var: ESVars,
                    inp: Inputs, T: int) -> None:
    """一比一对照 MATLAB 3.5 节。if N_es>0 守卫逐行照抄（坑 4：T=1 也会暴露）。
    行数账本（T=24, N=1 → 268 行）：9 个 N×T 块 216（820 上下限×2 对、821 同、
    822 互斥、823 SOC 递推、827/828 备用、829 备用能量）+ E 界 25×2 + 首末 1×2。
    非零元账本（484）：24(Pch_lb)+48(Pch_ub)+24(Pdis_lb)+48(Pdis_ub)+48(互斥)
    +96(递推 每行4元)+50(E界)+2(首末)+24(R_lb)+72(R_ub 三元)+48(R_energy 二元)。
    对账来源：MATLAB log 减法 192,410 − 183,042 − 9,100(抽蓄) = 268。"""
    if es.N_es == 0:                                              # MATLAB 819 守卫
        return
    N, Delta_t = es.N_es, inp.Delta_t
    Pch, Pdis = var.P_es_charge, var.P_es_discharge
    uch, udis = var.u_es_ch, var.u_es_dis
    E, R = var.E_es, var.R_es_hot
    Pmax = np.repeat(es.P_es_max[:, None], T, axis=1)

    # ---- MATLAB 820：0 <= P_ch、P_ch <= Pmax·u_ch ----
    m.addConstr(0 <= Pch, name="Pch_lb")
    m.addConstr(Pch <= Pmax * uch, name="Pch_ub")
    # ---- MATLAB 821：0 <= P_dis、P_dis <= Pmax·u_dis ----
    m.addConstr(0 <= Pdis, name="Pdis_lb")
    m.addConstr(Pdis <= Pmax * udis, name="Pdis_ub")
    # ---- MATLAB 822：充放互斥 ----
    m.addConstr(uch + udis <= 1, name="mutex")
    # ---- MATLAB 823：SOC 递推（含自放电 1-loss；充 +、放 −）----
    loss = np.repeat(es.loss_es[:, None], T, axis=1)
    eta_ch = np.repeat(es.eta_es_ch[:, None], T, axis=1)
    inv_eta_dis = np.repeat((1.0 / es.eta_es_dis)[:, None], T, axis=1)
    m.addConstr(E[:, 1:] == (1 - loss) * E[:, :-1]
                + eta_ch * Pch * Delta_t
                - inv_eta_dis * Pdis * Delta_t, name="soc")
    # ---- MATLAB 825：E 界（E_es(:) 展开 N×(T+1) 行）----
    Emin = np.repeat(es.E_es_min[:, None], T + 1, axis=1)
    Eup = np.repeat(es.E_es_upper[:, None], T + 1, axis=1)
    m.addConstr(Emin <= E, name="E_lb")
    m.addConstr(E <= Eup, name="E_ub")
    # ---- MATLAB 826：首末能量相等 ----
    E0 = es.E_es_initial[:, None]
    m.addConstr(E[:, 0:1] == E0, name="E_init")
    m.addConstr(E[:, T:T + 1] == E0, name="E_end")
    # ---- MATLAB 827/828：热备用受放电余量限制（上界 Pmax·(1-u_ch)）----
    m.addConstr(0 <= R, name="R_lb")
    m.addConstr(Pdis + R <= Pmax * (1 - uch), name="R_ub")
    # ---- MATLAB 829：备用能量限制 rd·R <= eta_dis·(E(2:T+1)-Emin) ----
    rd = inp.reserveDuration[inp.areaProvince[es.area_es]]        # 省级参数领行
    rdT = np.repeat(np.asarray(rd, float)[:, None], T, axis=1)
    eta_dis = np.repeat(es.eta_es_dis[:, None], T, axis=1)
    EminT = np.repeat(es.E_es_min[:, None], T, axis=1)
    m.addConstr(rdT * R <= eta_dis * (E[:, 1:] - EminT), name="R_energy")


if __name__ == "__main__":
    import reader
    inp = reader.load_inputs()
    es = load_es(inp)
    T = inp.T
    print('输入自检: N_es=%d, degrade=%.2f 元/MWh吞吐, FOM常数=%.2f'
          % (es.N_es, float(es.c_es_degrade[0]),
             float(np.sum(es.c_es_FOM * es.P_es_max + es.c_es_energy_FOM * es.E_es_max))))

    m = gp.Model()
    Ev = add_vars(m, es, T)
    m.update()
    N = es.N_es
    exp_cols = 4 * N * T + N * (T + 1) + N * T   # 4 个 N×T + E_es + R_hot
    exp_bin = 2 * N * T
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (exp_bin, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == exp_bin, '变量清单与推导不符'
    print('电储能变量清单核对通过')

    add_constraints(m, es, Ev, inp, T)
    m.update()
    exp_rows = 9 * N * T + 2 * N * (T + 1) + 2 * N   # 9 块 + E 界×2 + 首末×2
    # 逐块每行元数：Pch/Pdis lb=1、ub=2、互斥=2、递推=4、R_lb=1、R_ub=3、
    # R_energy=2 → 18/行；江苏 N=1,T=24→484、海南 N=1,T=168→3364 均吻合
    exp_nz = 18 * N * T + 2 * N * (T + 1) + 2 * N
    print('电储能约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    print('电储能非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.getAttr('NumNZs') == exp_nz, '非零元与手推账本不符'
    print('电储能约束账本核对通过')
