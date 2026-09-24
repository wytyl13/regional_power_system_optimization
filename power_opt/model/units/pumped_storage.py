#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/24 09:05
@Author  : weiyutao
@File    : pumped_storage.py
抽水蓄能模块：一比一对照 MATLAB 1.5 / 2.6 / 3.6 / 4.6。
读取的表：分区机组信息表[抽水蓄能]（SUBSTR(C.ID，12) 分区列）、检修计划（Type=抽水蓄能 行）。
与电储能的差异：无 if 守卫全直建（坑 2 主角，N_ps=0 时 MATLAB 也建 843 行 120 条，
此处 N_ps=25 实体）；SOC 递推无自放电；成本仅 FOM 无变动；u_ps_area 两件在系统层
（context.py），本模块 844/845 耦合约束借 Sv 传入。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
import gurobipy as gp

import config
from reader import _read, _zstr, Inputs


@dataclass
class PSInputs:
    """MATLAB 1.5 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_ps: int
    UnitKey_ps: list
    area_ps: np.ndarray           # 机组 → 分区（0 基）
    P_ps_max: np.ndarray
    m_ps: np.ndarray              # 检修：1 检修，0 可用（N_ps×T）
    c_ps_FOM: np.ndarray          # 本周期元/MW
    P_ps_pump_min: np.ndarray     # 抽水功率下限（PUMP_MIN_MW，正值）
    P_ps_pump_max: np.ndarray     # 抽水功率上限（MAX_POWER_CHARGE）
    eta_ps_gen: np.ndarray        # 发电效率
    eta_ps_pump: np.ndarray       # 抽水效率
    E_ps_max: np.ndarray          # MWh
    SOC_ps_min: np.ndarray; SOC_ps_max: np.ndarray; SOC_ps_initial: np.ndarray
    E_ps_initial: np.ndarray      # = E_ps_max × SOC_initial（MATLAB 190）


def load_ps(inp: Inputs) -> PSInputs:
    """镜像 MATLAB 1.5 节（176-200）。缺列缺值即报错。"""
    T = inp.T
    mtn_ps = inp.maintenance[inp.maintenance["Type"].astype(str) == "抽水蓄能"]

    req_ps = ["UnitKey", "SUBSTR(C.ID，12)", "CAP", "PUMP_MIN_MW", "MAX_POWER_CHARGE",
              "GEN_AFFI_FACTOR", "CHAEGE_AFFI_FACTOR", "ENERGY_MWH",
              "SOC_MIN", "SOC_MAX", "SOC_INITIAL", "FOM_COST_PERIOD"]
    psData = _read(config.UNIT_FILE, "抽水蓄能", req_ps)
    N_ps = len(psData)
    zone_ps = [_zstr(v) for v in psData["SUBSTR(C.ID，12)"]]
    areaPos = {a: i for i, a in enumerate(inp.AreaID)}
    if miss := [z for z in zone_ps if z not in areaPos]:
        raise KeyError(f"抽蓄分区编号 {set(miss)} 不在分区表中")
    area_ps = np.array([areaPos[z] for z in zone_ps], dtype=int)
    f = lambda c: psData[c].to_numpy(float)
    UnitKey_ps = psData["UnitKey"].astype(str).tolist()
    P_ps_max = f("CAP")
    P_ps_pump_min = f("PUMP_MIN_MW"); P_ps_pump_max = f("MAX_POWER_CHARGE")
    eta_ps_gen = f("GEN_AFFI_FACTOR"); eta_ps_pump = f("CHAEGE_AFFI_FACTOR")
    E_ps_max = f("ENERGY_MWH")
    SOC_ps_min = f("SOC_MIN"); SOC_ps_max = f("SOC_MAX"); SOC_ps_initial = f("SOC_INITIAL")
    E_ps_initial = E_ps_max * SOC_ps_initial                     # MATLAB 190
    c_ps_FOM = inp.fixedCostScale * f("FOM_COST_PERIOD")

    m_ps = np.zeros((N_ps, T))
    keyPos = {k: i for i, k in enumerate(UnitKey_ps)}
    for _, plan in mtn_ps.iterrows():
        key = str(plan["UnitKey"])
        if key not in keyPos:
            raise KeyError(f"检修计划机组 {key} 不在抽蓄表中（不猜、不忽略）")
        periods = (inp.Time >= plan["StartTime"]) & (inp.Time < plan["EndTime"])
        m_ps[keyPos[key], periods] = 1

    for name, arr in [("P_ps_max", P_ps_max), ("E_ps_initial", E_ps_initial),
                      ("eta_ps_gen", eta_ps_gen), ("eta_ps_pump", eta_ps_pump), ("m_ps", m_ps)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查抽蓄输入表")

    return PSInputs(
        N_ps=N_ps, UnitKey_ps=UnitKey_ps, area_ps=area_ps, P_ps_max=P_ps_max,
        m_ps=m_ps, c_ps_FOM=c_ps_FOM, P_ps_pump_min=P_ps_pump_min,
        P_ps_pump_max=P_ps_pump_max, eta_ps_gen=eta_ps_gen, eta_ps_pump=eta_ps_pump,
        E_ps_max=E_ps_max, SOC_ps_min=SOC_ps_min, SOC_ps_max=SOC_ps_max,
        SOC_ps_initial=SOC_ps_initial, E_ps_initial=E_ps_initial,
    )


# ------------------- 2.6 决策变量（MATLAB 621-629 行；u_ps_area 两件在系统层） -------------------

INF = gp.GRB.INFINITY

@dataclass
class PSVars:
    """MATLAB 2.6 节决策变量的镜像（单机量 6 个；u_ps_area_gen/pump 见 context.py）。"""
    P_ps_gen: gp.MVar; P_ps_pump: gp.MVar     # 发电/抽水功率，MW
    u_ps_gen: gp.MVar; u_ps_pump: gp.MVar     # 单机发电/抽水状态，二值
    R_ps_hot: gp.MVar                          # 热备用，MW
    E_ps: gp.MVar                              # 第 1 列初始能量，后续列时段末能量


def add_vars(m: gp.Model, ps: PSInputs) -> PSVars:
    """连续变量 lb=-inf、二值变量照 MATLAB 622-629；界限由 3.6 节约束钉。"""
    N, T = ps.N_ps, ps.m_ps.shape[1]
    P_ps_gen  = m.addMVar((N, T), lb=-INF, name="P_ps_gen")       # 622
    P_ps_pump = m.addMVar((N, T), lb=-INF, name="P_ps_pump")      # 623
    u_ps_gen  = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_ps_gen")    # 624
    u_ps_pump = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_ps_pump")   # 625
    R_ps_hot  = m.addMVar((N, T), lb=-INF, name="R_ps_hot")       # 628
    E_ps      = m.addMVar((N, T + 1), lb=-INF, name="E_ps")       # 629
    return PSVars(P_ps_gen=P_ps_gen, P_ps_pump=P_ps_pump, u_ps_gen=u_ps_gen,
                  u_ps_pump=u_ps_pump, R_ps_hot=R_ps_hot, E_ps=E_ps)


# ------------------- 3.6 抽蓄约束（MATLAB 835-859 行；843 行 120 条在系统层） -------------------

def add_constraints(m: gp.Model, ps: PSInputs, var: PSVars,
                    inp: Inputs, Sv) -> None:
    """一比一对照 MATLAB 3.6 节（无 if 守卫，全直建）。
    行数账本（T=24, N=25 → 9,100 行）：13 个 N×T 块 7,800
    （837 上下限、838/839 抽水限、840 互斥、841 检修×2、844/845 分区方向×2、
    847 SOC 递推、856/857 备用、858 备用能量）+ E 界 625×2 + 首末 25×2。
    非零元账本（16,300）：600(837a)+1200×7(837b/838/839/840/841×2块600、
    844/845)+2400(847 每行4元)+1250(E界)+50(首末)+600(856)+1800(857)+1200(858)。
    数据特性（已核）：25 台 3 档容量 200/400/50；PUMP_MIN=MAX_CHARGE=CAP；
    eta 0.8/0.8；检修 4 月/11 月不在 1 月窗口 → m_ps 全 0（841 右端常数 1）。
    对账来源：MATLAB log 减法 192,410 − 183,042 − 268(电储能) = 9,100。"""
    N, T, Delta_t = ps.N_ps, ps.m_ps.shape[1], inp.Delta_t
    Pgen, Ppump = var.P_ps_gen, var.P_ps_pump
    ugen, upump = var.u_ps_gen, var.u_ps_pump
    R, E = var.R_ps_hot, var.E_ps
    Pmax = np.repeat(ps.P_ps_max[:, None], T, axis=1)

    # ---- MATLAB 837：0 <= P_gen、P_gen <= Pmax·u_gen ----
    m.addConstr(0 <= Pgen, name="Pgen_lb")
    m.addConstr(Pgen <= Pmax * ugen, name="Pgen_ub")
    # ---- MATLAB 838/839：Pmin_pump·u_pump <= P_pump <= Pmax_pump·u_pump ----
    Pmin_pump = np.repeat(ps.P_ps_pump_min[:, None], T, axis=1)
    Pmax_pump = np.repeat(ps.P_ps_pump_max[:, None], T, axis=1)
    m.addConstr(Pmin_pump * upump <= Ppump, name="Ppump_lb")
    m.addConstr(Ppump <= Pmax_pump * upump, name="Ppump_ub")
    # ---- MATLAB 840：抽发互斥 ----
    m.addConstr(ugen + upump <= 1, name="mutex")
    # ---- MATLAB 841：检修不可用（右端 1-m_ps 常数矩阵）----
    m.addConstr(ugen <= 1 - ps.m_ps, name="u_gen_maint")
    m.addConstr(upump <= 1 - ps.m_ps, name="u_pump_maint")
    # ---- MATLAB 844/845：单机方向 ≤ 所在分区方向（借系统层 u_ps_area）----
    m.addConstr(ugen <= Sv.u_ps_area_gen[ps.area_ps, :], name="u_gen_area")
    m.addConstr(upump <= Sv.u_ps_area_pump[ps.area_ps, :], name="u_pump_area")
    # ---- MATLAB 847：SOC 递推（无自放电；抽水 +、发电 −）----
    eta_pump = np.repeat(ps.eta_ps_pump[:, None], T, axis=1)
    inv_eta_gen = np.repeat((1.0 / ps.eta_ps_gen)[:, None], T, axis=1)
    m.addConstr(E[:, 1:] == E[:, :-1]
                + eta_pump * Ppump * Delta_t
                - inv_eta_gen * Pgen * Delta_t, name="soc")
    # ---- MATLAB 851/852：E 界（E_ps(:) 展开 N×(T+1) 行）----
    Emin = np.repeat((ps.E_ps_max * ps.SOC_ps_min)[:, None], T + 1, axis=1)
    Emax = np.repeat((ps.E_ps_max * ps.SOC_ps_max)[:, None], T + 1, axis=1)
    m.addConstr(Emin <= E, name="E_lb")
    m.addConstr(E <= Emax, name="E_ub")
    # ---- MATLAB 853：首末能量相等 ----
    E0 = ps.E_ps_initial[:, None]
    m.addConstr(E[:, 0:1] == E0, name="E_init")
    m.addConstr(E[:, T:T + 1] == E0, name="E_end")
    # ---- MATLAB 856/857：热备用受发电状态与功率余量限制 ----
    m.addConstr(0 <= R, name="R_lb")
    m.addConstr(Pgen + R <= Pmax * ugen, name="R_ub")
    # ---- MATLAB 858：备用能量限制 rd·R <= eta_gen·(E(2:T+1)-Emin) ----
    rd = inp.reserveDuration[inp.areaProvince[ps.area_ps]]        # 省级参数领行
    rdT = np.repeat(np.asarray(rd, float)[:, None], T, axis=1)
    eta_gen = np.repeat(ps.eta_ps_gen[:, None], T, axis=1)
    EminT = np.repeat((ps.E_ps_max * ps.SOC_ps_min)[:, None], T, axis=1)
    m.addConstr(rdT * R <= eta_gen * (E[:, 1:] - EminT), name="R_energy")


if __name__ == "__main__":
    import reader
    inp = reader.load_inputs()
    ps = load_ps(inp)
    print('输入自检: N_ps=%d, m_ps sum=%s, FOM常数=%.2f, 容量和=%.0f MW'
          % (ps.N_ps, ps.m_ps.sum(), float(np.sum(ps.c_ps_FOM * ps.P_ps_max)),
             float(ps.P_ps_max.sum())))

    m = gp.Model()
    Pv = add_vars(m, ps)
    m.update()
    N, T = ps.N_ps, ps.m_ps.shape[1]
    exp_cols = 5 * N * T + N * (T + 1)     # 5 个 N×T + E_ps N×(T+1)
    exp_bin = 2 * N * T                    # u_gen + u_pump
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (exp_bin, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == exp_bin, '变量清单与推导不符'
    print('抽蓄变量清单核对通过')

    # 系统层变量占位（844/845 耦合需要 u_ps_area；add_vars 顺带建 840 行互斥 1 条）
    from model import context as CT
    Sv = CT.add_vars(m, inp)
    add_constraints(m, ps, Pv, inp, Sv)
    m.update()
    exp_rows = 13 * N * T + 2 * N * (T + 1) + 2 * N   # 13 块 + E 界×2 + 首末×2
    actual_rows = m.NumConstrs - 120                  # 减系统层 u_ps_area_mu 120 行
    print('抽蓄约束行数  期望=%d  实际=%d' % (exp_rows, actual_rows))
    assert actual_rows == exp_rows, '行数与手推账本不符'
    exp_nz = 600 + 7 * 1200 + 2400 + 1250 + 50 + 600 + 1800 + 1200
    actual_nz = m.getAttr('NumNZs') - 240             # 减 u_ps_area_mu 的 240 元
    print('抽蓄非零元  期望=%d  实际=%d' % (exp_nz, actual_nz))
    assert actual_nz == exp_nz, '非零元与手推账本不符'
    print('抽蓄约束账本核对通过')
