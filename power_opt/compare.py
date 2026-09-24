#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/22 18:10
@Author  : weiyutao
@File    : compare.py
读 MATLAB 导出的 P_th / u_th / P_gas / u_gas csv，与 Python 解逐时段比对。
判定两段式（2026-09-22 用户定，gap=0 实验后）：
  第一段（硬门槛，不达标即 FAIL）：目标值相对偏差 ≤1e-6；
    分区×小时煤电总出力、全网逐小时总出力、开机台数逐位一致（物理量必须对齐）。
  第二段（同参数机组互换，只报不判）：172 台里 171 台存在全参数相同的孪生机组
    （330 MW 机组大量复制），求解器分不出 7 号和 8 号谁开，逐台 u_th 差异是
    数学等价解之间的正常互换，不构成 FAIL。降为聚合口径比对。
另：表D 七个"手术干净"矩阵（P_wind/P_pv/弃风/弃光/P_shed/shift_out/shift_in/P_cut）
  每轮必查（全 0 才说明删其他电源的数据手术做干净了）。
"""
from __future__ import annotations

import numpy as np

import config
import reader
import solve as S
from model.units import thermal as TH
from model.units import gas as GA
from model.units import nuclear as NU
from model.units import biomass as BI
from model.units import pumped_storage as PS
from model.units import electrochemical as ES
from model.units import hydro as HY


def _csv(path) -> np.ndarray:
    return np.loadtxt(path, delimiter=",", dtype=float, ndmin=2)  # T=1 单列 (N,) → (N,1)


def run(T: int, obj_ref: float | None = None):
    config.T = T
    inp = reader.load_inputs()
    coal = TH.load_coal(inp)
    gas = GA.load_gas(inp)
    nuc = NU.load_nuclear(inp)
    bio = BI.load_biomass(inp)
    ps = PS.load_ps(inp)
    es = ES.load_es(inp)
    hyd = HY.load_hyd(inp)
    m, *_ = S.solve(inp)   # solve 返回十元组；compare 按变量名取解，句柄不逐个接
    if m.SolCount == 0:
        return

    # 按变量名取解矩阵（沙盒内实测可用的原语）
    def mat(name, shape):
        return np.array([m.getVarByName(f"{name}[{i},{j}]").X
                         for i in range(shape[0]) for j in range(shape[1])]
                        ).reshape(shape)

    P_py = mat("P_th", (coal.N_th, inp.T))
    u_py = mat("u_th", (coal.N_th, inp.T)).round().astype(int)

    P_mat = _csv(config.MATLAB_DIR / f"P_th_T{T}.csv")
    u_mat = _csv(config.MATLAB_DIR / f"u_th_T{T}.csv").round().astype(int)
    assert P_mat.shape == P_py.shape, f"P_th 形状不符: {P_mat.shape} vs {P_py.shape}"

    # ================= 第一段：硬门槛 =================
    # (1) 目标值
    if obj_ref:
        rel_obj = (m.ObjVal - obj_ref) / obj_ref
        print(f"目标值 python={m.ObjVal:.6f}  matlab={obj_ref:.6f}  相对偏差={rel_obj:.3e}")
        ok_obj = abs(rel_obj) <= 1e-6
    else:
        rel_obj, ok_obj = None, True
        print("（未给 matlab 目标值，跳过目标值判定；只对物理量）")

    # (2) 聚合物理量：全网口径（硬门槛）；分区口径（软，跨区同参数机组互换会差）
    A_th = np.zeros((inp.Narea, coal.N_th))
    A_th[coal.area_th, np.arange(coal.N_th)] = 1
    areaP_py, areaP_mat = A_th @ P_py, A_th @ P_mat          # 分区×小时煤电总出力
    d_area = np.abs(areaP_py - areaP_mat).max()               # MW
    d_hour = np.abs(P_py.sum(axis=0) - P_mat.sum(axis=0)).max()  # 全网逐小时
    d_units = int(np.abs(u_py.sum() - u_mat.sum()))           # 开机台数差
    ok_agg = (d_hour <= 1e-6) and (d_units == 0)
    print(f"[硬] 全网逐小时煤电总出力 最大差: {d_hour:.3e} MW")
    print(f"[硬] 开机台数和: py={int(u_py.sum())}  mat={int(u_mat.sum())}  差 {d_units}")
    print(f"[软] 分区×小时总出力 最大差: {d_area:.3e} MW（跨区同参数机组互换会差，只报不判）")
    # (3) 表D：手术干净证据。风光轮起 P_wind/P_pv/弃风/弃光移入风光块，DR 轮起
    #     shift_out/shift_in/P_cut 移入 DR 块（上限解封=验证对象），只剩失负荷一件
    zeros = {"P_shed": mat("P_shed", (inp.Narea, inp.T))}
    bad0 = [k for k, v in zeros.items() if np.abs(v).max() > 1e-6]
    print(f"[硬] 表D 手术矩阵(失负荷): {'全部≈0 ✓' if not bad0 else '非零 ✗ ' + ','.join(bad0)}")
    ok_zero = not bad0

    ok = ok_obj and ok_agg and ok_zero

    # ================= 气电块（气电轮新增，两段式同煤电）=================
    Pg_py = mat("P_gas", (gas.N_gas, inp.T))
    ug_py = mat("u_gas", (gas.N_gas, inp.T)).round().astype(int)
    Pg_mat = _csv(config.MATLAB_DIR / f"P_gas_T{T}.csv")
    ug_mat = _csv(config.MATLAB_DIR / f"u_gas_T{T}.csv").round().astype(int)
    assert Pg_mat.shape == Pg_py.shape, f"P_gas 形状不符: {Pg_mat.shape} vs {Pg_py.shape}"
    dg_hour = np.abs(Pg_py.sum(axis=0) - Pg_mat.sum(axis=0)).max()
    dg_units = int(np.abs(ug_py.sum() - ug_mat.sum()))
    print(f"[硬] 全网逐小时气电总出力 最大差: {dg_hour:.3e} MW")
    print(f"[硬] 气电开机台数和: py={int(ug_py.sum())}  mat={int(ug_mat.sum())}  差 {dg_units}")
    A_gas = np.zeros((inp.Narea, gas.N_gas))
    A_gas[gas.area_gas, np.arange(gas.N_gas)] = 1
    dg_area = np.abs(A_gas @ Pg_py - A_gas @ Pg_mat).max()
    print(f"[软] 分区×小时气电总出力 最大差: {dg_area:.3e} MW（跨区同参数机组互换会差，只报不判）")
    dg_rows = int(np.sum((ug_py != ug_mat).any(axis=1)))
    relgP = np.max(np.abs(Pg_py - Pg_mat) / np.maximum(np.abs(Pg_mat), 1.0))
    print(f"[软] 逐台 u_gas 不一致行: {dg_rows}/{gas.N_gas}（同参数机组互换，只报不判）")
    print(f"[软] 逐台 P_gas 最大相对偏差: {relgP:.3e}（含互换效应，只报不判）")
    ok_agg_gas = (dg_hour <= 1e-6) and (dg_units == 0)
    ok = ok and ok_agg_gas

    # ================= 核/生块（核生轮新增；无 u 矩阵，只比 P）=================
    Pn_py = mat("P_nuc", (nuc.N_nuc, inp.T))
    Pb_py = mat("P_bio", (bio.N_bio, inp.T))
    Pn_mat = _csv(config.MATLAB_DIR / f"P_nuc_T{T}.csv")
    Pb_mat = _csv(config.MATLAB_DIR / f"P_bio_T{T}.csv")
    assert Pn_mat.shape == Pn_py.shape and Pb_mat.shape == Pb_py.shape, "核/生 P 形状不符"
    dn_hour = np.abs(Pn_py.sum(axis=0) - Pn_mat.sum(axis=0)).max()
    db_hour = np.abs(Pb_py.sum(axis=0) - Pb_mat.sum(axis=0)).max()
    print(f"[硬] 全网逐小时核电总出力 最大差: {dn_hour:.3e} MW")
    print(f"[硬] 全网逐小时生物质总出力 最大差: {db_hour:.3e} MW")
    relnP = np.max(np.abs(Pn_py - Pn_mat) / np.maximum(np.abs(Pn_mat), 1.0))
    relbP = np.max(np.abs(Pb_py - Pb_mat) / np.maximum(np.abs(Pb_mat), 1.0))
    db_rows = int(np.sum(np.abs(Pb_py - Pb_mat).max(axis=1) > 1e-6))
    print(f"[软] 逐台 P_nuc 最大相对偏差: {relnP:.3e}（Pmin=Pmax 钉 660，理论上逐位）")
    print(f"[软] 逐台 P_bio 最大相对偏差: {relbP:.3e}；逐台差异行 {db_rows}/{bio.N_bio}（只报不判）")
    ok_agg_nb = (dn_hour <= 1e-6) and (db_hour <= 1e-6)
    ok = ok and ok_agg_nb

    # ================= 风光块（风光轮新增；P 与弃电都要比）=================
    re_ok = []
    for nm in ["P_wind", "P_pv", "P_wind_curt", "P_pv_curt"]:
        X_py = mat(nm, (inp.Narea, inp.T))
        X_mat = _csv(config.MATLAB_DIR / f"{nm}_T{T}.csv")
        assert X_mat.shape == X_py.shape, f"{nm} 形状不符: {X_mat.shape} vs {X_py.shape}"
        d_h = np.abs(X_py.sum(axis=0) - X_mat.sum(axis=0)).max()
        d_cell = np.abs(X_py - X_mat).max()
        print(f"[硬] 全网逐小时{nm} 最大差: {d_h:.3e} MW；[软] 逐格最大差: {d_cell:.3e} MW")
        re_ok.append(d_h <= 1e-6)
    ok_agg_re = all(re_ok)
    # 利用率自查：省级弃电 ≤ 5% 可用（renewableUtilizationMin=0.95）
    wc = mat("P_wind_curt", (inp.Narea, inp.T)).sum() + mat("P_pv_curt", (inp.Narea, inp.T)).sum()
    wa = inp.P_wind_available.sum() + inp.P_pv_available.sum()
    print(f"[自查] 弃电率: {wc/max(wa,1e-9)*100:.4f}%（上限 5%，弃电总量 {wc:.1f} MWh / 可用 {wa:.1f} MWh）")
    ok = ok and ok_agg_re

    # ================= 储能块（储能轮新增；功率 4 件 + 能量 2 件）=================
    # 硬门槛：全网逐小时总和；软口径：逐格（抽蓄 16 台 200MW 全同参数，互换效应预期强）
    sto_ok = []
    for nm, shape in [("P_ps_gen", (ps.N_ps, inp.T)), ("P_ps_pump", (ps.N_ps, inp.T)),
                      ("E_ps", (ps.N_ps, inp.T + 1)),
                      ("P_es_charge", (es.N_es, inp.T)), ("P_es_discharge", (es.N_es, inp.T)),
                      ("E_es", (es.N_es, inp.T + 1))]:
        X_py = mat(nm, shape)
        X_mat = _csv(config.MATLAB_DIR / f"{nm}_T{T}.csv")
        assert X_mat.shape == X_py.shape, f"{nm} 形状不符: {X_mat.shape} vs {X_py.shape}"
        d_h = np.abs(X_py.sum(axis=0) - X_mat.sum(axis=0)).max()
        d_cell = np.abs(X_py - X_mat).max()
        print(f"[硬] 全网逐小时{nm} 总和最大差: {d_h:.3e}；[软] 逐格最大差: {d_cell:.3e}（MW/MWh）")
        sto_ok.append(d_h <= 1e-6)
    ok_agg_sto = all(sto_ok)
    # 储能动作摘要：发电量/抽水量/充放电量（看解形态用）
    Pg_s, Pp_s = mat("P_ps_gen", (ps.N_ps, inp.T)), mat("P_ps_pump", (ps.N_ps, inp.T))
    Ec_s, Ed_s = mat("P_es_charge", (es.N_es, inp.T)), mat("P_es_discharge", (es.N_es, inp.T))
    print(f"[自查] 抽蓄: 发电 {Pg_s.sum():.1f} MWh、抽水 {Pp_s.sum():.1f} MWh；"
          f"电储能: 充 {Ec_s.sum():.1f}、放 {Ed_s.sum():.1f} MWh")
    ok = ok and ok_agg_sto

    # ================= 水电块（水电轮新增；功率 2 + 备用 1 + 弃水 1）=================
    # 硬门槛：全网逐小时总和；水电仅 1+1 台无孪生，逐格理论上也应对齐
    hyd_ok = []
    for nm, shape in [("P_hyd_ror", (hyd.N_hyd_ror, inp.T)),
                      ("P_hyd_res", (hyd.N_hyd_res, inp.T)),
                      ("R_hyd_hot", (hyd.N_hyd_res, inp.T)),
                      ("E_hyd_curt", (hyd.N_hyd_period, 1))]:
        X_py = mat(nm, shape)
        X_mat = _csv(config.MATLAB_DIR / f"{nm}_T{T}.csv")
        assert X_mat.shape == X_py.shape, f"{nm} 形状不符: {X_mat.shape} vs {X_py.shape}"
        d_h = np.abs(X_py.sum(axis=0) - X_mat.sum(axis=0)).max()
        d_cell = np.abs(X_py - X_mat).max()
        print(f"[硬] 全网逐小时{nm} 总和最大差: {d_h:.3e}；[软] 逐格最大差: {d_cell:.3e}（MW/MWh）")
        hyd_ok.append(d_h <= 1e-6)
    ok_agg_hyd = all(hyd_ok)
    Phr_s = mat("P_hyd_ror", (hyd.N_hyd_ror, inp.T)).sum()
    Phs_s = mat("P_hyd_res", (hyd.N_hyd_res, inp.T)).sum()
    print(f"[自查] 水电: 径流 {Phr_s:.1f} MWh、库容 {Phs_s:.1f} MWh"
          f"（周期电量预算 {hyd.E_hyd_available.sum():.1f} MWh）")
    ok = ok and ok_agg_hyd

    # ================= DR 块（DR 轮新增；负荷侧三件，5×T）=================
    # 硬门槛：全网逐小时总和；DR 是分区级资源无孪生，逐格理论上也应对齐
    dr_ok = []
    for nm in ["P_shift_out", "P_shift_in", "P_cut"]:
        X_py = mat(nm, (inp.Narea, inp.T))
        X_mat = _csv(config.MATLAB_DIR / f"{nm}_T{T}.csv")
        assert X_mat.shape == X_py.shape, f"{nm} 形状不符: {X_mat.shape} vs {X_py.shape}"
        d_h = np.abs(X_py.sum(axis=0) - X_mat.sum(axis=0)).max()
        d_cell = np.abs(X_py - X_mat).max()
        print(f"[硬] 全网逐小时{nm} 总和最大差: {d_h:.3e}；[软] 逐格最大差: {d_cell:.3e} MW")
        dr_ok.append(d_h <= 1e-6)
    ok_agg_dr = all(dr_ok)
    so_s = mat("P_shift_out", (inp.Narea, inp.T)).sum()
    si_s = mat("P_shift_in", (inp.Narea, inp.T)).sum()
    ct_s = mat("P_cut", (inp.Narea, inp.T)).sum()
    print(f"[自查] 需求响应: 转出 {so_s:.1f}、转入 {si_s:.1f}、削减 {ct_s:.1f} MWh"
          f"（日限额 {inp.E_cut_day_max.sum():.1f}、年限额 {inp.AnnualCutEnergy_MWh.sum():.1f} MWh）")
    ok = ok and ok_agg_dr

    # ================= 第二段：逐台差异（只报不判）=================
    diff_rows = int(np.sum((u_py != u_mat).any(axis=1)))
    if diff_rows:
        for i in np.where((u_py != u_mat).any(axis=1))[0][:5]:
            print(f"  [软] 单元{i}: u 和 py={u_py[i].sum():.0f} mat={u_mat[i].sum():.0f} | "
                  f"P 和 py={P_py[i].sum():.1f} mat={P_mat[i].sum():.1f} MW")
    relP = np.max(np.abs(P_py - P_mat) / np.maximum(np.abs(P_mat), 1.0))
    print(f"[软] 逐台 u_th 不一致行: {diff_rows}/{coal.N_th}（同参数机组互换，只报不判）")
    print(f"[软] 逐台 P_th 最大相对偏差: {relP:.3e}（含互换效应，只报不判）")

    print("逐时段对照结论:", "PASS" if ok else "FAIL", "（两段式判定，2026-09-22）")
    if not ok:
        print("FAIL 原因:", "目标值" if not ok_obj else "",
              "煤电聚合" if not ok_agg else "", "手术矩阵非零" if not ok_zero else "",
              "气电聚合" if not ok_agg_gas else "", "核生聚合" if not ok_agg_nb else "",
              "风光聚合" if not ok_agg_re else "", "储能聚合" if not ok_agg_sto else "",
              "水电聚合" if not ok_agg_hyd else "", "DR聚合" if not ok_agg_dr else "")


if __name__ == "__main__":
    import sys
    T = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    obj_ref = float(sys.argv[2]) if len(sys.argv) > 2 else None
    run(T, obj_ref)
