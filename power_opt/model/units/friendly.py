#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/28 10:20
@Author  : weiyutao
@File    : friendly.py
新能源友好电站模块：一比一对照 MATLAB 1.19 / 2.9 / 3.9 / 4.9（海南 v2 新增）。
读取的表：分区机组信息表[新能源友好电站]、算例系统参数[友好电站时序]。
风/光分直送、站内充电、弃电三部分；储能仅由本站风光充电（不从电网吸功率）；
无检修、无爬坡、不进热备用（MATLAB 1006-1007 无 A_fr 项，照抄）。
成本段（c_fr_degrade 载入、C_fr_FOM/C_fr_curt 组合）留 solve.py 4.9。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
import gurobipy as gp

import config
from reader import _read, _zstr, _to_hours, Inputs


@dataclass
class FrInputs:
    """MATLAB 1.19 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_fr: int
    UnitKey_fr: list
    name_fr: list                # 机组名（八~十一节分析表用，MATLAB friendlyData.NAME）
    area_fr: np.ndarray           # 电站 → 分区（0 基，AreaID 列）
    P_fr_es_max: np.ndarray       # 储能功率上限，MW（ES_POWER）
    E_fr_max: np.ndarray; E_fr_min: np.ndarray; E_fr_upper: np.ndarray
    E_fr_initial: np.ndarray      # = E_fr_max × SOC_INITIAL（MATLAB 486）
    eta_fr_ch: np.ndarray; eta_fr_dis: np.ndarray
    loss_fr: np.ndarray           # 自放电率（SELF_DISCHARGE）
    c_fr_degrade: np.ndarray      # 元/MWh 吞吐量（MATLAB 497-498，公式含除法）
    P_fr_wind_available: np.ndarray   # N_fr×T，MW
    P_fr_pv_available: np.ndarray     # N_fr×T
    wind_cap: np.ndarray; pv_cap: np.ndarray          # 4.9 FOM 用（原始值，不乘折算）
    fom_wind: np.ndarray; fom_pv: np.ndarray
    fom_power: np.ndarray; fom_energy: np.ndarray


def load_fr(inp: Inputs) -> FrInputs:
    """镜像 MATLAB 1.19 节（476-498）。缺列缺值即报错。"""
    req_fr = ["UnitKey", "AreaID", "WIND_CAP", "PV_CAP", "ES_POWER", "ENERGY_MWH",
              "SOC_MIN", "SOC_MAX", "SOC_INITIAL", "ETA_CHARGE", "ETA_DISCHARGE",
              "SELF_DISCHARGE", "POWER_COST", "ENERGY_COST", "REPLACE_FACTOR",
              "MAX_CYCLES", "FOM_COST_PERIOD", "FOM_ENERGY_PERIOD",
              "WIND_FOM_PERIOD", "PV_FOM_PERIOD", "NAME"]
    frData = _read(config.UNIT_FILE, "新能源友好电站", req_fr)
    N_fr = len(frData)
    zone_fr = [_zstr(v) for v in frData["AreaID"]]
    areaPos = {a: i for i, a in enumerate(inp.AreaID)}
    if miss := [z for z in zone_fr if z not in areaPos]:
        raise KeyError(f"友好电站分区编号 {set(miss)} 不在分区表中")
    area_fr = np.array([areaPos[z] for z in zone_fr], dtype=int)
    f = lambda c: frData[c].to_numpy(float)
    UnitKey_fr = frData["UnitKey"].astype(str).tolist()
    name_fr = frData["NAME"].astype(str).tolist()                 # 八~十一节分析表用
    P_fr_es_max = f("ES_POWER"); E_fr_max = f("ENERGY_MWH")
    E_fr_min = E_fr_max * f("SOC_MIN")                      # MATLAB 484
    E_fr_upper = E_fr_max * f("SOC_MAX")                    # 485
    E_fr_initial = E_fr_max * f("SOC_INITIAL")              # 486
    eta_fr_ch = f("ETA_CHARGE"); eta_fr_dis = f("ETA_DISCHARGE")
    loss_fr = f("SELF_DISCHARGE")
    # MATLAB 497-498：c_fr_degrade = (P_max·POWER_COST + E_max·ENERGY_COST)
    #                           × REPLACE_FACTOR / (2·E_max·MAX_CYCLES)
    c_fr_degrade = ((P_fr_es_max * f("POWER_COST") + E_fr_max * f("ENERGY_COST"))
                    * f("REPLACE_FACTOR") / (2 * E_fr_max * f("MAX_CYCLES")))

    # ---- 友好电站时序（MATLAB 487-493）：逐时匹配，缺时刻即报错 ----
    frTime = _read(config.SYSTEM_FILE, "友好电站时序",
                   ["Time", "UnitKey", "WindAvailable_MW", "PVAvailable_MW"])
    T = inp.T
    P_wind_av = np.zeros((N_fr, T)); P_pv_av = np.zeros((N_fr, T))
    for i, key in enumerate(UnitKey_fr):
        rows = frTime[frTime["UnitKey"].astype(str) == key]
        rows = rows.set_index(_to_hours(rows["Time"]))
        try:
            sel = rows.loc[inp.Time]
        except KeyError as e:
            raise KeyError(f"友好电站时序缺电站 {key} 的时刻 {e}（不补值）") from None
        P_wind_av[i, :] = sel["WindAvailable_MW"].to_numpy(float)   # MATLAB 491
        P_pv_av[i, :] = sel["PVAvailable_MW"].to_numpy(float)       # 492

    for name, arr in [("P_fr_es_max", P_fr_es_max), ("E_fr_initial", E_fr_initial),
                      ("c_fr_degrade", c_fr_degrade),
                      ("P_fr_wind_available", P_wind_av), ("P_fr_pv_available", P_pv_av)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查友好电站输入表")

    return FrInputs(
        N_fr=N_fr, UnitKey_fr=UnitKey_fr, name_fr=name_fr, area_fr=area_fr,
        P_fr_es_max=P_fr_es_max, E_fr_max=E_fr_max, E_fr_min=E_fr_min,
        E_fr_upper=E_fr_upper, E_fr_initial=E_fr_initial,
        eta_fr_ch=eta_fr_ch, eta_fr_dis=eta_fr_dis, loss_fr=loss_fr,
        c_fr_degrade=c_fr_degrade,
        P_fr_wind_available=P_wind_av, P_fr_pv_available=P_pv_av,
        wind_cap=f("WIND_CAP"), pv_cap=f("PV_CAP"),
        fom_wind=f("WIND_FOM_PERIOD"), fom_pv=f("PV_FOM_PERIOD"),
        fom_power=f("FOM_COST_PERIOD"), fom_energy=f("FOM_ENERGY_PERIOD"),
    )


# ------------------- 2.9 决策变量（MATLAB 648-658 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class FrVars:
    """MATLAB 2.9 节决策变量的镜像，共 11 个。"""
    P_fr_wind_out: gp.MVar; P_fr_wind_ch: gp.MVar; P_fr_wind_curt: gp.MVar
    P_fr_pv_out: gp.MVar; P_fr_pv_ch: gp.MVar; P_fr_pv_curt: gp.MVar
    P_fr_charge: gp.MVar; P_fr_discharge: gp.MVar
    u_fr_ch: gp.MVar; u_fr_dis: gp.MVar
    E_fr: gp.MVar                  # 首列初始，后续时段末（N_fr×(T+1)）


def add_vars(m: gp.Model, fr: FrInputs, T: int) -> FrVars:
    """连续变量 lb=-inf、二值变量照 MATLAB 649-658。"""
    N = fr.N_fr
    P_fr_wind_out  = m.addMVar((N, T), lb=-INF, name="P_fr_wind_out")    # 649
    P_fr_wind_ch   = m.addMVar((N, T), lb=-INF, name="P_fr_wind_ch")     # 650
    P_fr_wind_curt = m.addMVar((N, T), lb=-INF, name="P_fr_wind_curt")   # 651
    P_fr_pv_out    = m.addMVar((N, T), lb=-INF, name="P_fr_pv_out")      # 652
    P_fr_pv_ch     = m.addMVar((N, T), lb=-INF, name="P_fr_pv_ch")       # 653
    P_fr_pv_curt   = m.addMVar((N, T), lb=-INF, name="P_fr_pv_curt")     # 654
    P_fr_charge    = m.addMVar((N, T), lb=-INF, name="P_fr_charge")      # 655
    P_fr_discharge = m.addMVar((N, T), lb=-INF, name="P_fr_discharge")   # 656
    u_fr_ch  = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_fr_ch")    # 657
    u_fr_dis = m.addMVar((N, T), vtype=gp.GRB.BINARY, name="u_fr_dis")   # 658
    E_fr     = m.addMVar((N, T + 1), lb=-INF, name="E_fr")               # 659
    return FrVars(P_fr_wind_out=P_fr_wind_out, P_fr_wind_ch=P_fr_wind_ch,
                  P_fr_wind_curt=P_fr_wind_curt, P_fr_pv_out=P_fr_pv_out,
                  P_fr_pv_ch=P_fr_pv_ch, P_fr_pv_curt=P_fr_pv_curt,
                  P_fr_charge=P_fr_charge, P_fr_discharge=P_fr_discharge,
                  u_fr_ch=u_fr_ch, u_fr_dis=u_fr_dis, E_fr=E_fr)


# ------------------- 3.9 友好电站约束（MATLAB 879-898 行） -------------------

def add_constraints(m: gp.Model, fr: FrInputs, var: FrVars,
                    inp: Inputs, T: int) -> None:
    """一比一对照 MATLAB 3.9 节。if N_fr>0 守卫照抄（880）。
    行数账本（15 个 N×T 块 + E 界 2·N×(T+1) + 首末 2N）：
    风三项非负 3、光三项非负 3、风平衡、光平衡、充电定义、充上下、放上下、
    互斥、SOC 递推各 1 → 15 块。
    非零元账本：风/光非负各 3×NT（每行 1 元）、平衡/充电定义各 3 元、
    充放 lb 1 元 + ub 2 元、互斥 2 元、递推 4 元 → 27×NT + E 界 + 首末。"""
    if fr.N_fr == 0:                                              # MATLAB 880 守卫
        return
    N, Delta_t = fr.N_fr, inp.Delta_t
    Pw_out, Pw_ch, Pw_curt = var.P_fr_wind_out, var.P_fr_wind_ch, var.P_fr_wind_curt
    Pv_out, Pv_ch, Pv_curt = var.P_fr_pv_out, var.P_fr_pv_ch, var.P_fr_pv_curt
    Pch, Pdis = var.P_fr_charge, var.P_fr_discharge
    uch, udis = var.u_fr_ch, var.u_fr_dis
    E = var.E_fr

    # ---- MATLAB 881-882：风/光三变量非负 ----
    m.addConstr(Pw_out >= 0, name="fr_wout_lb")
    m.addConstr(Pw_ch >= 0, name="fr_wch_lb")
    m.addConstr(Pw_curt >= 0, name="fr_wcurt_lb")
    m.addConstr(Pv_out >= 0, name="fr_pout_lb")
    m.addConstr(Pv_ch >= 0, name="fr_pch_lb")
    m.addConstr(Pv_curt >= 0, name="fr_pcurt_lb")
    # ---- MATLAB 883-884：可用 = 直送 + 站内充 + 弃 ----
    m.addConstr(Pw_out + Pw_ch + Pw_curt == fr.P_fr_wind_available, name="fr_w_bal")
    m.addConstr(Pv_out + Pv_ch + Pv_curt == fr.P_fr_pv_available, name="fr_p_bal")
    # ---- MATLAB 886：储能仅由本站风光充电，不从电网吸收功率 ----
    m.addConstr(Pch == Pw_ch + Pv_ch, name="fr_ch_def")
    # ---- MATLAB 887：0 <= P_ch <= Pmax·u_ch ----
    Pmax = np.repeat(fr.P_fr_es_max[:, None], T, axis=1)
    m.addConstr(0 <= Pch, name="fr_Pch_lb")
    m.addConstr(Pch <= Pmax * uch, name="fr_Pch_ub")
    # ---- MATLAB 888：0 <= P_dis <= Pmax·u_dis ----
    m.addConstr(0 <= Pdis, name="fr_Pdis_lb")
    m.addConstr(Pdis <= Pmax * udis, name="fr_Pdis_ub")
    # ---- MATLAB 889：充放互斥 ----
    m.addConstr(uch + udis <= 1, name="fr_mutex")
    # ---- MATLAB 890-892：SOC 递推（自放电 1-loss；充 η_ch、放 1/η_dis）----
    loss = np.repeat(fr.loss_fr[:, None], T, axis=1)
    eta_ch = np.repeat(fr.eta_fr_ch[:, None], T, axis=1)
    inv_eta_dis = np.repeat((1.0 / fr.eta_fr_dis)[:, None], T, axis=1)
    m.addConstr(E[:, 1:] == (1 - loss) * E[:, :-1]
                + eta_ch * Pch * Delta_t
                - inv_eta_dis * Pdis * Delta_t, name="fr_soc")
    # ---- MATLAB 893：E 界（E_fr(:) 展开 N×(T+1) 行）----
    Emin = np.repeat(fr.E_fr_min[:, None], T + 1, axis=1)
    Eup = np.repeat(fr.E_fr_upper[:, None], T + 1, axis=1)
    m.addConstr(Emin <= E, name="fr_E_lb")
    m.addConstr(E <= Eup, name="fr_E_ub")
    # ---- MATLAB 894：首末能量相等 ----
    E0 = fr.E_fr_initial[:, None]
    m.addConstr(E[:, 0:1] == E0, name="fr_E_init")
    m.addConstr(E[:, T:T + 1] == E0, name="fr_E_end")


if __name__ == "__main__":
    import reader
    inp = reader.load_inputs()
    fr = load_fr(inp)
    T = inp.T
    print('输入自检: N_fr=%d, degrade=%.2f 元/MWh吞吐, FOM常数=%.2f'
          % (fr.N_fr, float(fr.c_fr_degrade[0]),
             float(inp.fixedCostScale * np.sum(fr.fom_wind * fr.wind_cap
                                               + fr.fom_pv * fr.pv_cap
                                               + fr.fom_power * fr.P_fr_es_max
                                               + fr.fom_energy * fr.E_fr_max))))  # MATLAB 1063-1066
    print('  风/光可用[0,:2]=%s / %s' % (fr.P_fr_wind_available[0, :2],
                                        fr.P_fr_pv_available[0, :2]))

    m = gp.Model()
    Fv = add_vars(m, fr, T)
    m.update()
    N = fr.N_fr
    exp_cols = 10 * N * T + N * (T + 1)     # 8 连续 + 2 二值 + E_fr
    exp_bins = 2 * N * T
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    print('二值变量  期望=%d  实际=%d' % (exp_bins, m.NumBinVars))
    assert m.NumVars == exp_cols and m.NumBinVars == exp_bins, '变量清单与推导不符'
    print('友好电站变量清单核对通过')

    add_constraints(m, fr, Fv, inp, T)
    m.update()
    exp_rows = 15 * N * T + 2 * N * (T + 1) + 2 * N   # 15 块 + E 界×2 + 首末×2
    exp_nz = 27 * N * T + 2 * N * (T + 1) + 2 * N
    print('友好电站约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    print('友好电站非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    assert m.getAttr('NumNZs') == exp_nz, '非零元与手推账本不符'
    print('友好电站账本核对通过（全模型四项以 gurobi_hainan_last.log 为锚）')
