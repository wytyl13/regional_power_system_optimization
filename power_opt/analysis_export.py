#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/12 19:40
@Author  : weiyutao
@File    : analysis_export.py
分析结果导出：一比一对照新版 MATLAB synergy_hainan_30.m 八~十二节。
产出（落 config.RESULT_DIR/分析结果/）：
  00_设备堆叠_N.png（plot.plot_operation 产物复制）+ 01~07 六张分析 PNG
  + 海南分析结果.mat（19 变量按 MATLAB :1545 save 清单逐一对齐）
  + 分析摘要.txt（:1509-1544 fprintf 模板逐行复刻）
五表数值计算（build_analysis）原在 service/assemble.py，上移至此供两侧共用；
camelCase JSON 输出形状不变（service/assemble.py 再导出，app.py 契约不动）。
已知差异（如实记录）：.fig 是 MATLAB 专有格式不生成；AnalysisRun 的 datetime/
string 字段与 table 类型在 scipy 写出的 .mat 中退化为 char 数组 / struct array
（MATLAB 端 load 后访问语法 CostSummary(k).Category 与 table.Cost_Yuan(k) 接近）。

v2 重构（2026-10-16 裁决）：对外入口收口为 AnalysisExport 类（metrics/build_analysis/
build_views/export）；compute_metrics 自 solution.py 并入（分析口径，非取值口径）；
views 不再含 positionChart（前端独有，移 service/position_chart.py 组装）。
"""
from __future__ import annotations

import shutil
from datetime import datetime

import numpy as np

import config
import plot as PL


def _aggregation(total_rows, areas):
    """机组 → 分区归属矩阵 (Narea,n)：第 i 台机组在第 area_i 行置 1。

    Args:
        total_rows: 分区数 Narea（矩阵行数；形参名沿用旧调用）。
        areas: 各机组 0 基分区索引数组（列数=机组数）。

    Returns:
        np.ndarray: 机组→分区归属矩阵 (Narea, n)。
    """
    matrix = np.zeros((total_rows, len(areas)))
    matrix[areas, np.arange(len(areas))] = 1
    return matrix


def _compute_metrics(inp, solution) -> dict:
    """组装期派生指标（MATLAB 1177-1190 自检 + report 专用量）。
    原在 solution.py，v2 并入本文件（分析口径）；write_result_json 与五张分析表从这里取数。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。

    Returns:
        dict: 指标字典（balanceErrorMW/renewableUtilization 等 + P_D/Rhot 中间量）。
    """
    V = solution.values
    U = solution.units
    F = solution.inputs_fields
    Delta_t = inp.time.Delta_t

    A_th = _aggregation(inp.areas.Narea, U["thermal"]["area"])
    A_gas = _aggregation(inp.areas.Narea, U["gas"]["area"])
    A_hyd_ror = _aggregation(inp.areas.Narea, U["hydro_ror"]["area"])
    A_hyd_res = _aggregation(inp.areas.Narea, U["hydro_reservoir"]["area"])
    A_ps = _aggregation(inp.areas.Narea, U["pumped_storage"]["area"])
    A_es = _aggregation(inp.areas.Narea, U["electrochemical"]["area"])
    A_fr = _aggregation(inp.areas.Narea, U["friendly"]["area"])
    A_line = np.zeros((inp.areas.Narea, inp.lines.N_line))
    A_line[inp.lines.line_from, np.arange(inp.lines.N_line)] = -1
    A_line[inp.lines.line_to, np.arange(inp.lines.N_line)] = 1

    # 平衡残差 = 发电 + 储能净出力 + 净受入 + 失负荷 − 响应后负荷（MATLAB 1178）
    P_G = (A_th @ V["P_th"] + A_gas @ V["P_gas"]
           + _aggregation(inp.areas.Narea, U["nuclear"]["area"]) @ V["P_nuc"]
           + _aggregation(inp.areas.Narea, U["biomass"]["area"]) @ V["P_bio"]
           + V["P_wind"] + V["P_pv"]
           + A_hyd_ror @ V["P_hyd_ror"] + A_hyd_res @ V["P_hyd_res"]
           + A_fr @ (V["P_fr_wind_out"] + V["P_fr_pv_out"] + V["P_fr_discharge"]))
    P_sto = (A_ps @ V["P_ps_gen"] - A_ps @ V["P_ps_pump"]
             + A_es @ V["P_es_discharge"] - A_es @ V["P_es_charge"])
    P_tr = A_line @ V["P_line"]
    P_D = (inp.profiles.P_load - V["P_shift_out"] + V["P_shift_in"] - V["P_cut"])
    resid = P_G + P_sto + P_tr + V["P_shed"] - P_D
    print("功率平衡残差 max: %.3g MW（MATLAB 1178 行同款自检，应≈0）"
          % np.abs(resid).max())

    # 新能源利用率（省级聚合含友好站，MATLAB 1181-1186）
    A_prov = np.zeros((inp.areas.Nprovince, inp.areas.Narea))
    A_prov[inp.areas.areaProvince, np.arange(inp.areas.Narea)] = 1
    RE_avail = A_prov @ (inp.profiles.P_wind_available + inp.profiles.P_pv_available
                         + A_fr @ (U["friendly"]["wind_available"]
                                   + U["friendly"]["pv_available"]))
    RE_curt = A_prov @ (V["P_wind_curt"] + V["P_pv_curt"]
                        + A_fr @ (V["P_fr_wind_curt"] + V["P_fr_pv_curt"]))
    available_energy = RE_avail.sum(axis=1) * Delta_t
    curtailed_energy = RE_curt.sum(axis=1) * Delta_t
    utilization = np.full(inp.areas.Nprovince, np.nan)
    has_re = available_energy > 0
    utilization[has_re] = 1 - curtailed_energy[has_re] / available_energy[has_re]
    energy_not_served = float(V["P_shed"].sum() * Delta_t)       # 1187
    loss_of_load_hours = float((V["P_shed"] > 1e-4).any(axis=0).sum()) * Delta_t  # 1188
    max_load_shortfall = float(V["P_shed"].sum(axis=0).max())    # 1189
    print("新能源利用率：%.2f%%，弃风光电量：%.3f MWh，失负荷：%.3f MWh"
          % (100 * utilization[0], curtailed_energy.sum(), energy_not_served))
    print("缺电小时：%.0f h，最大缺口：%.3f MW"
          % (loss_of_load_hours, max_load_shortfall))

    # 热/冷备用裕度（1002/1003 同式对自身比例约束的最小富余）
    Rhot = A_prov @ (A_th @ V["R_th_hot"] + A_gas @ V["R_gas_hot"]
                     + A_hyd_res @ V["R_hyd_hot"] + A_ps @ V["R_ps_hot"]
                     + A_es @ V["R_es_hot"])
    Rcold = A_prov @ (A_th @ V["R_th_cold"] + A_gas @ V["R_gas_cold"])
    hot_margin = float((Rhot - np.repeat(
        inp.prov.hotReserveRatio[:, None], inp.time.T, 1) * inp.week_peak).min())
    cold_margin = float((Rcold - np.repeat(
        inp.prov.coldReserveRatio[:, None], inp.time.T, 1) * inp.week_peak).min())

    # CO2 总量（1001 同式）/ 储能首末能量闭合
    co2_t = float(Delta_t * (V["CO2_th"].sum()
                  + (np.asarray(F["CO2_gas_factor"])[:, None] * V["P_gas"]).sum()))
    closure = float(max(np.abs(data[:, -1] - data[:, 0]).max(initial=0.0)
                        for data in (V["E_ps"], V["E_es"], V["E_fr"])))
    return {
        "P_D": P_D, "Rhot": Rhot,
        "balanceErrorMW": float(np.abs(resid).max()),
        "renewableUtilization": utilization,
        "curtailedEnergy": curtailed_energy,
        "energyNotServed": energy_not_served,
        "lossOfLoadHours": loss_of_load_hours,
        "maxLoadShortfall": max_load_shortfall,
        "hotMargin": hot_margin, "coldMargin": cold_margin,
        "CO2_t": co2_t, "storageClosure": closure,
    }


# ---------- 八、成本构成（1290-1312）----------

def cost_analysis(solution):
    """六项 + 十项两种分解；同一总成本的两个视角，不能相加。
    排放已计入发电项，不再单列六项之外；C_inter 海南无互济量 = 0。

    Args:
        solution (Solution): 求解结果。

    Returns:
        dict: {costSummary: 六项分解, costDetail: 十项分解}。
    """
    C = solution.costs
    total = float(solution.four_items["objective"]["value"])
    six_names = ["发电成本", "储能成本", "需求响应", "弃电惩罚", "失负荷惩罚", "省间互济"]
    six = [C["C_gen"], C["C_sto"], C["C_DR"], C["C_curt"], C["C_shed"], C["C_inter"]]
    cost_summary = {
        "totalYuan": total,
        "items": [{"category": n, "costYuan": float(v),
                   "costWanYuan": float(v) / 1e4,
                   "sharePercent": 100.0 * float(v) / total if abs(total) > 1e-8 else None}
                  for n, v in zip(six_names, six)],
    }
    # 十项性质分解（1302-1309）；光热 FOM 进固定、GEN_COST 进变动（1303/1305，
    # 2026-10-16 接入；海南无光热 → C_csp 各分量为 0）
    fixed_all = (C["C_th_FOM"] + C["C_gas_FOM"] + C["C_bio_FOM"] + C["C_nuc_FOM"]
                 + C["C_hyd"] + C["C_es_FOM"] + C["C_ps"]
                 + C["C_windFOM"] + C["C_pvFOM"] + C["C_fr_FOM"] + C["C_csp_FOM"])
    variable_all = (C["C_th_fuel"] + C["C_gas_gen"] + C["C_bio_gen"] + C["C_nuc_gen"]
                    + C["C_csp_gen"])
    ten_names = ["燃料及变动发电", "启动与停机", "固定运维", "碳排放", "大气污染物",
                 "电池老化", "需求响应", "弃电惩罚", "失负荷惩罚", "省间互济"]
    ten = [variable_all,
           C["C_th_start"] + C["C_th_stop"] + C["C_gas_start"] + C["C_gas_stop"],
           fixed_all,
           C["C_CO2"], C["C_pollution"],
           C["C_es_degrade"] + C["C_fr_degrade"],
           C["C_DR"], C["C_curt"], C["C_shed"], C["C_inter"]]
    cost_detail = {
        "items": [{"category": n, "costYuan": float(v), "costWanYuan": float(v) / 1e4}
                  for n, v in zip(ten_names, ten)],
        # MATLAB 1312：两种分解对目标函数的核对差额（应 ≈1e-7 量级）
        "reconciliationErrorYuan": [float(sum(six) - total),
                                    float(sum(ten) - total)],
    }
    return {"costSummary": cost_summary, "costDetail": cost_detail}


# ---------- 九、火电启停（1334-1352）----------

def thermal_analysis(inp, solution):
    """煤电+气电逐台：启动/停机次数直接取 v/w（不推断）、在线小时、检修小时、
    发电量与等效满负荷小时（=发电量/额定容量）。统计含首小时相对初态的切换。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。

    Returns:
        dict: {thermalSummary: 煤电+气电逐台统计行列表}。
    """
    V = solution.values
    U = solution.units
    rows = []
    for group, prefix, label in (("thermal", "th", "煤电"), ("gas", "gas", "气电")):
        keys = U[group]["keys"]
        names = U[group].get("names", keys)
        rated = np.asarray(U[group]["rated"])
        rows.extend({
            "unitKey": str(k), "name": str(n), "type": label,
            "starts": int(V[f"v_{prefix}"][i].sum()),
            "stops": int(V[f"w_{prefix}"][i].sum()),
            "onlineHours": float(V[f"u_{prefix}"][i].sum() * inp.time.Delta_t),
            "maintenanceHours": float(np.asarray(U[group]["maintenance"])[i].sum()
                                      * inp.time.Delta_t),
            "energyMWh": float(V[f"P_{prefix}"][i].sum() * inp.time.Delta_t),
            "equivalentFullLoadHours": float(
                V[f"P_{prefix}"][i].sum() * inp.time.Delta_t / rated[i]),
        } for i, (k, n) in enumerate(zip(keys, names)))
    return {"thermalSummary": rows}


# ---------- 十、储能充放电及 SOC（1370-1409）----------

def storage_analysis(inp, solution):
    """抽蓄/电储能/友好站逐台：充/放电量、SOC min/max%、放电侧等效循环
    （=放电量/额定能量，输出侧口径非寿命模型）、首末能量差。
    E 第 0 列 = 初始能量、第 k 列 = 第 k 小时末；SOC 轨迹含首尾 T+1 个边界点。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。

    Returns:
        dict: {storageSummary: 三类储能逐台统计行列表}。
    """
    V = solution.values
    U = solution.units
    rows = []
    spec = [
        ("抽水蓄能", "pumped_storage", "P_ps_pump", "P_ps_gen", "E_ps"),
        ("独立电化学储能", "electrochemical", "P_es_charge", "P_es_discharge", "E_es"),
        ("友好电站储能", "friendly", "P_fr_charge", "P_fr_discharge", "E_fr"),
    ]
    for label, group, ch_key, dis_key, e_key in spec:
        u = U[group]
        E = V[e_key]
        rated = np.asarray(u["energy_capacity"])
        if len(rated) == 0:
            continue
        soc = 100.0 * E / rated[:, None]
        keys = u["keys"]
        names = u.get("names", keys)
        for i, (k, n) in enumerate(zip(keys, names)):
            charge = float(V[ch_key][i].sum() * inp.time.Delta_t)
            discharge = float(V[dis_key][i].sum() * inp.time.Delta_t)
            rows.append({
                "type": label, "unitKey": str(k), "name": str(n),
                "chargeMWh": charge, "dischargeMWh": discharge,
                "minSocPercent": float(soc[i].min()),
                "maxSocPercent": float(soc[i].max()),
                "equivalentDischargeCycles": float(discharge / rated[i]),
                "terminalEnergyErrorMWh": float(E[i, -1] - E[i, 0]),
            })
    return {"storageSummary": rows}


# ---------- 十一、设备出力 + 风光消纳（1430-1462）----------

def device_analysis(inp, solution):
    """11 类设备全 T 小时电量和最大出力。放电量单列、不解释为一次能源结构；
    友好站只计净上网（风直送+光直送+放电），不与站内量重复计入。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。

    Returns:
        dict: {deviceSummary: 设备电量/峰值, renewableSummary: 风光消纳}。
    """
    V = solution.values
    dt = inp.time.Delta_t
    fr_net = V["P_fr_wind_out"] + V["P_fr_pv_out"] + V["P_fr_discharge"]
    hydro = np.concatenate([V["P_hyd_ror"], V["P_hyd_res"]], axis=0) \
        if V["P_hyd_ror"].shape[0] else V["P_hyd_res"]   # 空类直接用另一类
    device_power = [
        ("煤电", V["P_th"].sum(axis=0)), ("气电", V["P_gas"].sum(axis=0)),
        ("生物质", V["P_bio"].sum(axis=0)), ("核电", V["P_nuc"].sum(axis=0)),
        ("常规水电", hydro.sum(axis=0)), ("普通风电", V["P_wind"].sum(axis=0)),
        ("普通光伏", V["P_pv"].sum(axis=0)),
        ("光热", V["P_csp_unit"].sum(axis=0)),   # 2026-10-16 接入（N=0 → 0 行）
        ("新能源友好站净上网", fr_net.sum(axis=0)),
        ("抽蓄发电", V["P_ps_gen"].sum(axis=0)),
        ("独立储能放电", V["P_es_discharge"].sum(axis=0)),
    ]
    device_summary = {
        "items": [{"device": n, "outputEnergyMWh": float(np.sum(p) * dt),
                   "peakOutputMW": float(np.max(p))}
                  for n, p in device_power],
    }
    # 风光消纳含友好站；站内充电视为已利用，保持新能源约束原口径
    F = solution.inputs_fields
    U = solution.units
    fr_wind_av = np.asarray(U["friendly"]["wind_available"]).sum(axis=0)
    fr_pv_av = np.asarray(U["friendly"]["pv_available"]).sum(axis=0)
    p_wind_av = np.asarray(F["P_wind_available"]).sum(axis=0)
    p_pv_av = np.asarray(F["P_pv_available"]).sum(axis=0)
    spec = [
        ("风电", p_wind_av + fr_wind_av,
         (V["P_wind_curt"] + V["P_fr_wind_curt"]).sum(axis=0)),
        ("光伏", p_pv_av + fr_pv_av,
         (V["P_pv_curt"] + V["P_fr_pv_curt"]).sum(axis=0)),
    ]
    items = []
    for label, available, curtailed in spec:
        avail_energy = float(np.sum(available) * dt)
        curt_power = np.asarray(curtailed)
        curt_energy = float(np.sum(curt_power) * dt)
        items.append({
            "type": label, "availableMWh": avail_energy,
            "curtailedMWh": curt_energy,
            "utilizationPercent": 100.0 * (1 - curt_energy / avail_energy)
                                  if avail_energy > 0 else None,
        })
    renewable_summary = {"items": items}
    return {"deviceSummary": device_summary, "renewableSummary": renewable_summary}


def build_analysis(inp, solution):
    """五表总装：costSummary / costDetail / thermalSummary / storageSummary /
    deviceSummary / renewableSummary（camelCase，口径对齐 MATLAB 八~十一节）。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。

    Returns:
        dict: 五表总装（camelCase 键）。
    """
    analysis = {}
    analysis.update(cost_analysis(solution))
    analysis.update(thermal_analysis(inp, solution))
    analysis.update(storage_analysis(inp, solution))
    analysis.update(device_analysis(inp, solution))
    return analysis


# ---------- 前端 views 块（service 注入 solution.json 用；只算数不出图）----------
# 服务路径不执行绘图（plot_operation/export_analysis 只在本地 __main__ 跑），
# 前端自绘所需的视图数据在此组装，与 PNG/CSV 同一口径。
# 规范（2026-10-12 与用户定稿）：tab 一律 {title, note?, panels[]}；
# panel type 只有 4 种 stacked/line/bar/heatmap；标签直接中文进 JSON（无前端映射表）；
# table 不进 views（用户裁决：渲染层只出 PNG 有的东西）。
# time 轴不在 views 里：前端复用 result.time。
# v2（2026-10-16）：positionChart（27 列老 Excel 口径）是前端独有，不属算法口径，
# 自本块摘除，移 service/position_chart.py 组装（service 注入 views["positionChart"]）。

def build_views(inp, solution):
    """solution.json 的 result.views 块（前端唯一渲染入口，8 tab 自包含；
    positionChart 由 service 侧另行注入）。
    各 panel 形状（全 panel 可带 xLabel/yLabel，轴文字原文抄 PNG）：
      stacked  {series:{名:[T]}, lines?:{名:[T]}}      堆叠柱；lines 不参与堆叠
      line     {series:{名:[T]}, step?:true, hlines?:[{name,value}]}
      bar      {orientation, categories:[类目], series:{名:[逐类目值]}}
      heatmap  {matrix:[[0/1]], rowLabels:[行名], legendLabels:[状态名]}
    时序数组统一截 PNG 窗口（前 min(168,T) 小时，MATLAB 1195-1196），前端直接用。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。

    Returns:
        dict: views 块（tab 名 → {title, note?, panels[]}）。
    """
    V = solution.values
    U = solution.units
    F = solution.inputs_fields
    T = inp.time.T
    obj = solution.four_items["objective"]
    plot_end = min(168, T)
    P_D = inp.profiles.P_load - V["P_shift_out"] + V["P_shift_in"] - V["P_cut"]

    def ts(a):                              # 时序 → PNG 窗口 list
        a = np.asarray(a) + 0.0             # +0.0：负零归一，-0.0 不进 views
        return (a[:, :plot_end] if a.ndim > 1 else a[:plot_end]).tolist()

    import plot as PL
    views = {}

    # ==== tab1：设备运行（00 图口径 = plot.write_chart_csv；21 列 + 2 负荷线）====
    device = PL.build_device_data(inp, solution)
    assert np.abs(device.sum(axis=2) - P_D).max() < 1e-3, \
        "21 列合计≠响应后负荷：有设备列漏算"
    scopes = PL._scopes(inp)
    panels = []
    for scopeName, M in scopes:                   # 海南单分区 → 1 个 panel
        all_cols = {PL.DEVICE_NAMES[k]: (M @ device[:, :plot_end, k] + 0.0).ravel().tolist()
                    for k in range(len(PL.DEVICE_NAMES))}
        visible = [n for n, v in all_cols.items() if any(abs(x) > 0 for x in v)]
        panels.append({
            "type": "stacked", "title": "设备运行（%s）" % scopeName,
            "series": {n: all_cols[n] for n in visible},
            "lines": {"响应后负荷": (M @ P_D[:, :plot_end]).ravel().tolist(),
                      "原始负荷": (M @ inp.profiles.P_load[:, :plot_end]).ravel().tolist()},
            "xLabel": "时间（月/日 时:分）", "yLabel": "功率 / MW",
        })
    views["deviceOperation"] = {
        "title": "设备运行",
        "note": "%s 至 %s  |  逐小时出力；充电、抽水及净送出位于负半轴"
                % (inp.time.Time[0].strftime("%Y-%m-%d %H:%M"),
                   inp.time.Time[plot_end - 1].strftime("%Y-%m-%d %H:%M")),
        "panels": panels,
    }

    # ==== tab3：成本构成（01 图两横条）====
    analysis = build_analysis(inp, solution)
    views["cost"] = {
        "title": "成本构成｜全周期%d小时，总成本 %.2f 万元" % (T, obj["value"] / 1e4),
        "note": "两幅图为同一总成本的不同分解；固定运维按本次时长折算",
        "panels": [
            {"type": "bar", "orientation": "horizontal", "title": "目标函数六大项",
             "categories": [it["category"] for it in analysis["costSummary"]["items"]],
             "series": {"成本": [it["costWanYuan"] for it in
                               analysis["costSummary"]["items"]]},
             "xLabel": "成本 / 万元"},
            {"type": "bar", "orientation": "horizontal", "title": "按费用性质拆分",
             "categories": [it["category"] for it in analysis["costDetail"]["items"]],
             "series": {"成本": [it["costWanYuan"] for it in
                               analysis["costDetail"]["items"]]},
             "xLabel": "成本 / 万元"},
        ],
    }

    # ==== tab4：火电启停（02 图：状态热图 + 在线数阶梯线 + 启停成组柱）====
    u_all = np.vstack([V["u_th"], V["u_gas"]]).astype(int)
    mtn = np.vstack([np.asarray(U["thermal"]["maintenance"]),
                     np.asarray(U["gas"]["maintenance"])]).astype(int)
    state = u_all.copy()
    state[mtn > 0.5] = 2                          # 检修时段覆盖运行标记
    keys_all = [str(k) for k in U["thermal"]["keys"]] + \
               [str(k) for k in U["gas"]["keys"]]
    names_all = list(U["thermal"].get("names", U["thermal"]["keys"])) + \
                list(U["gas"].get("names", U["gas"]["keys"]))
    views["thermal"] = {
        "title": "火电运行与启停",
        "panels": [
            {"type": "heatmap", "title": "逐台运行状态",
             "matrix": ts(state), "rowLabels": [f"{k} {n}" for k, n in
                                               zip(keys_all, names_all)],
             "legendLabels": ["停机", "运行", "检修"],
             "xLabel": "时间（月/日 时:分）"},
            {"type": "line", "step": True, "title": "在线机组数量",
             "series": {"煤电": V["u_th"].astype(int).sum(axis=0)[:plot_end].tolist(),
                        "气电": V["u_gas"].astype(int).sum(axis=0)[:plot_end].tolist()},
             "yLabel": "在线机组数 / 台"},
            {"type": "bar", "orientation": "vertical",
             "title": "全部%d小时启停次数（含初态切换）" % T,
             "categories": ["煤电", "气电"],
             "series": {"启动": [int(V["v_th"].sum()), int(V["v_gas"].sum())],
                        "停机": [int(V["w_th"].sum()), int(V["w_gas"].sum())]},
             "yLabel": "次数"},
        ],
    }

    # ==== tab5~7：储能×3（03/04/05 图：功率堆叠 + SOC lines）====
    sto_spec = [
        ("storage_ps", "抽水蓄能运行分析", "pumped_storage",
         "P_ps_pump", "P_ps_gen", "E_ps"),
        ("storage_es", "独立电化学储能运行分析", "electrochemical",
         "P_es_charge", "P_es_discharge", "E_es"),
        ("storage_fr", "友好电站储能运行分析", "friendly",
         "P_fr_charge", "P_fr_discharge", "E_fr"),
    ]
    for tab, tab_title, group, ch_key, dis_key, e_key in sto_spec:
        if not len(U[group]["keys"]):
            continue
        rated = np.asarray(U[group]["energy_capacity"])
        names = list(U[group].get("names", U[group]["keys"]))
        soc = 100.0 * V[e_key] / rated[:, None]
        hlines = []
        for i, n in enumerate(names):
            hlines.append({"name": "%s 下限" % n,
                           "value": float(100 * U[group]["soc_min"][i])})
            hlines.append({"name": "%s 上限" % n,
                           "value": float(100 * U[group]["soc_max"][i])})
        views[tab] = {
            "title": tab_title,
            "panels": [
                {"type": "stacked", "title": "同类型功率汇总，充电为负",
                 "series": {"发电/放电": ts(V[dis_key].sum(axis=0)),
                            "抽水/充电": ts(-V[ch_key].sum(axis=0))},
                 "xLabel": "时间（月/日 时:分）", "yLabel": "功率 / MW"},
                {"type": "line", "title": "逐台SOC轨迹，虚线为各自上下限",
                 "series": {str(n): ts(soc[i]) for i, n in enumerate(names)},
                 "hlines": hlines,
                 "xLabel": "时段边界时间（月/日 时:分）", "yLabel": "SOC / %"},
            ],
        }

    # ==== tab8：设备出力统计（06 图两横条）====
    dev = analysis["deviceSummary"]["items"]
    views["deviceSummary"] = {
        "title": "设备出力统计｜全部%d小时" % T,
        "note": "储能放电单列；友好站计净上网量，不能将本图解释为一次能源结构",
        "panels": [
            {"type": "bar", "orientation": "horizontal", "title": "全周期电量",
             "categories": [it["device"] for it in dev],
             "series": {"电量": [it["outputEnergyMWh"] / 1e4 for it in dev]},
             "xLabel": "发电/放电量 / 万MWh"},
            {"type": "bar", "orientation": "horizontal", "title": "最大出力",
             "categories": [it["device"] for it in dev],
             "series": {"最大出力": [it["peakOutputMW"] for it in dev]},
             "xLabel": "最大出力 / MW"},
        ],
    }

    # ==== tab9：风光消纳（07 图：可用虚线 + 已利用实线 + 弃电柱）====
    wind_av = (np.asarray(F["P_wind_available"]).sum(axis=0)
               + np.asarray(U["friendly"]["wind_available"]).sum(axis=0))
    pv_av = (np.asarray(F["P_pv_available"]).sum(axis=0)
             + np.asarray(U["friendly"]["pv_available"]).sum(axis=0))
    wind_curt = (V["P_wind_curt"] + V["P_fr_wind_curt"]).sum(axis=0)
    pv_curt = (V["P_pv_curt"] + V["P_fr_pv_curt"]).sum(axis=0)
    av_e = lambda a: float(np.sum(a) * inp.time.Delta_t)
    panels = []
    for label, av, curt in [("风电", wind_av, wind_curt),
                            ("光伏", pv_av, pv_curt)]:
        util_pct = 100.0 * (1 - av_e(curt) / av_e(av)) if av_e(av) > 0 else None
        panels.append({
            "type": "stacked",
            "title": "%s｜全周期利用率 %s" % (label,
                  "%.2f%%" % util_pct if util_pct is not None else "无可用出力"),
            "series": {"弃电功率": ts(curt)},
            "lines": {"可用功率": ts(av), "已利用功率": ts(av - curt)},
            "xLabel": "时间（月/日 时:分）", "yLabel": "功率 / MW",
        })
    views["renewable"] = {"title": "风光消纳（包含友好电站）", "panels": panels}
    return views


# ---------- .mat 导出（MATLAB :1545 save 变量清单）----------

def _table(rows, fields, string_fields=(), int_fields=(), dtypes=None):
    """camelCase 行 dict 列表 → MATLAB struct array（近似 table 的 struct 化）。

    Args:
        rows: 行 dict 列表（camelCase 键）。
        fields: 字段名顺序（=struct 字段序）。
        string_fields: 按 str 写出的字段名。
        int_fields: 按 int 写出的字段名。
        dtypes: 预留参数，当前无调用方使用。

    Returns:
        np.ndarray: (n, 1) struct array。
    """
    n = len(rows)
    dtype = [(f, "O") for f in fields]
    arr = np.zeros((n, 1), dtype=dtype)
    for i, row in enumerate(rows):
        for f in fields:
            v = row[f]
            if f in string_fields:
                v = str(v)
            elif f in int_fields:
                v = int(v)
            arr[i, 0][f] = v
    return arr


def _cell(arrays):
    """1×N cell 数组（MATLAB {…} 元胞）。

    Args:
        arrays: 待装箱的数组序列。

    Returns:
        np.ndarray: 1×N object 数组。
    """
    cell = np.empty((1, len(arrays)), dtype=object)
    for i, a in enumerate(arrays):
        cell[0, i] = np.asarray(a)
    return cell


def write_mat(inp, solution, metrics, analysis, path) -> None:
    """海南分析结果.mat：19 个变量与 MATLAB :1545 save 清单逐一对齐。
    datetime/string/table 在 scipy 写出端拾遗：→ char / struct array。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。
        metrics (dict): 指标字典。
        analysis (dict): 五表总装。
        path: 输出 .mat 路径。

    Returns:
        None
    """
    from scipy.io import savemat

    V = solution.values
    U = solution.units
    T = inp.time.T
    obj = solution.four_items["objective"]
    four = solution.four_items

    analysis_run = np.zeros((1, 1), dtype=[(f, "O") for f in
        ("StartTime", "EndTime", "Hours", "Hydrology", "TotalCost_Yuan",
         "SolverInfo", "MIPGap", "PlotStartTime", "PlotEndTime",
         "EnergyNotServed_MWh")])
    plot_end_idx = min(168, T) - 1
    r0 = analysis_run[0, 0]
    r0["StartTime"] = inp.time.Time[0].strftime("%Y-%m-%d %H:%M")
    r0["EndTime"] = inp.time.Time[-1].strftime("%Y-%m-%d %H:%M")
    r0["Hours"] = T
    r0["Hydrology"] = config.HYDROLOGY_SCENARIO
    r0["TotalCost_Yuan"] = obj["value"]
    r0["SolverInfo"] = ("Successfully solved (GUROBI)" if four["status"] == "OPTIMAL"
                        else four["status"])
    r0["MIPGap"] = obj["mipGapPct"] / 100
    r0["PlotStartTime"] = inp.time.Time[0].strftime("%Y-%m-%d %H:%M")
    r0["PlotEndTime"] = inp.time.Time[plot_end_idx].strftime("%Y-%m-%d %H:%M")
    r0["EnergyNotServed_MWh"] = metrics["energyNotServed"]

    cost_summary = _table(
        analysis["costSummary"]["items"],
        ("category", "costYuan", "costWanYuan", "sharePercent"),
        string_fields=("category",))
    cost_detail = _table(
        analysis["costDetail"]["items"], ("category", "costYuan", "costWanYuan"),
        string_fields=("category",))
    reconciliation = np.asarray(
        analysis["costDetail"]["reconciliationErrorYuan"], dtype=float).reshape(2, 1)
    thermal_summary = _table(
        analysis["thermalSummary"],
        ("unitKey", "name", "type", "starts", "stops", "onlineHours",
         "maintenanceHours", "energyMWh", "equivalentFullLoadHours"),
        string_fields=("unitKey", "name", "type"),
        int_fields=("starts", "stops"))
    storage_summary = _table(
        analysis["storageSummary"],
        ("type", "unitKey", "name", "chargeMWh", "dischargeMWh", "minSocPercent",
         "maxSocPercent", "equivalentDischargeCycles", "terminalEnergyErrorMWh"),
        string_fields=("type", "unitKey", "name"))
    device_summary = _table(
        analysis["deviceSummary"]["items"], ("device", "outputEnergyMWh", "peakOutputMW"),
        string_fields=("device",))
    renewable_summary = _table(
        analysis["renewableSummary"]["items"],
        ("type", "availableMWh", "curtailedMWh", "utilizationPercent"),
        string_fields=("type",))

    # 火电合并矩阵（15×T，MATLAB [u_th;u_gas] 同序）
    u_thermal = np.vstack([V["u_th"], V["u_gas"]]).astype(np.uint8)
    v_thermal = np.vstack([V["v_th"], V["v_gas"]]).astype(np.uint8)
    w_thermal = np.vstack([V["w_th"], V["w_gas"]]).astype(np.uint8)
    thermal_maintenance = np.vstack([U["thermal"]["maintenance"],
                                     U["gas"]["maintenance"]]).astype(np.uint8)
    thermal_power = np.vstack([V["P_th"], V["P_gas"]]).astype(float)
    keys_cell = np.empty((1, 3), dtype=object)
    for i, g in enumerate(("pumped_storage", "electrochemical", "friendly")):
        keys_cell[0, i] = [str(k) for k in U[g]["keys"]]
    storage_energy = _cell([V["E_ps"], V["E_es"], V["E_fr"]])
    storage_rated = _cell([U["pumped_storage"]["energy_capacity"],
                           U["electrochemical"]["energy_capacity"],
                           U["friendly"]["energy_capacity"]])
    storage_charge = _cell([V["P_ps_pump"], V["P_es_charge"], V["P_fr_charge"]])
    storage_discharge = _cell([V["P_ps_gen"], V["P_es_discharge"], V["P_fr_discharge"]])
    # 风光可用/弃电（2×T，MATLAB sum(·,1) 汇总含友好站）
    renew_available = np.vstack([
        (np.asarray(solution.inputs_fields["P_wind_available"]).sum(axis=0)
         + np.asarray(U["friendly"]["wind_available"]).sum(axis=0)),
        (np.asarray(solution.inputs_fields["P_pv_available"]).sum(axis=0)
         + np.asarray(U["friendly"]["pv_available"]).sum(axis=0)),
    ])
    renew_curt = np.vstack([
        (V["P_wind_curt"] + V["P_fr_wind_curt"]).sum(axis=0),
        (V["P_pv_curt"] + V["P_fr_pv_curt"]).sum(axis=0),
    ])

    time_cell = np.empty((1, T), dtype=object)
    for t, ts in enumerate(inp.time.Time):
        time_cell[0, t] = ts.strftime("%Y-%m-%d %H:%M")

    payload = {
        "AnalysisRun": analysis_run,
        "CostSummary": cost_summary, "CostDetail": cost_detail,
        "CostReconciliationError": reconciliation,
        "ThermalSummary": thermal_summary, "StorageSummary": storage_summary,
        "DeviceSummary": device_summary, "RenewableSummary": renewable_summary,
        "Time": time_cell,
        "u_thermal_result": u_thermal, "v_thermal_result": v_thermal,
        "w_thermal_result": w_thermal,
        "thermalMaintenance": thermal_maintenance, "thermalPower": thermal_power,
        "storageEnergy": storage_energy, "storageRatedEnergy": storage_rated,
        "storageCharge": storage_charge, "storageDischarge": storage_discharge,
        "storageKeys": keys_cell,
        "renewAvailable": renew_available, "renewCurt": renew_curt,
        "P_load": np.asarray(solution.inputs_fields["P_load"], dtype=float),
        "P_shift_in_result": V["P_shift_in"], "P_shift_out_result": V["P_shift_out"],
        "P_cut_result": V["P_cut"], "P_shed_result": V["P_shed"],
    }
    savemat(path, payload, do_compression=True, oned_as="row")
    print("已导出: %s" % path)


# ---------- 分析摘要.txt（MATLAB :1509-1544 模板逐行复刻）----------

def write_summary_txt(inp, solution, metrics, analysis, path) -> None:
    """文案与 %.4f/%.2f 等格式化精度与 MATLAB fprintf 完全一致。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。
        metrics (dict): 指标字典。
        analysis (dict): 五表总装。
        path: 输出 .txt 路径。

    Returns:
        None
    """
    obj = solution.four_items["objective"]
    total = obj["value"]
    total_wan = total / 1e4
    cs = analysis["costSummary"]["items"]
    cd = analysis["costDetail"]["items"]
    text = []
    plot_end = min(168, inp.time.T) - 1
    text.append("海南算例分析")
    text.append("仿真起点：%s；共%d小时。"
                % (inp.time.Time[0].strftime("%Y-%m-%d %H:%M"), inp.time.T))
    text.append("总成本：%.4f万元；实际相对求解间隙：%.4f%%。"
                % (total_wan, obj["mipGapPct"]))
    text.append("时序图窗口：%s 至 %s（小时起点）；统计表覆盖全部求解时段。"
                % (inp.time.Time[0].strftime("%Y-%m-%d %H:%M"),
                   inp.time.Time[plot_end].strftime("%Y-%m-%d %H:%M")))
    text.append("")
    text.append("一、成本构成（与目标函数一致）")
    for it in cs:
        share = "%.2f%%" % it["sharePercent"] if it["sharePercent"] is not None else "NaN"
        text.append("%s：%.4f万元，占总成本%s。" % (it["category"], it["costWanYuan"], share))
    text.append("")
    text.append("按费用性质拆分如下，与上述六项为同一总成本的两种分解，不能重复相加。")
    for it in cd:
        text.append("%s：%.4f万元。" % (it["category"], it["costWanYuan"]))
    text.append("固定运维按本次仿真时长折算；碳排放及污染物费用已包含在发电成本中。")
    text.append("电池老化为模型中的退化费用；充放电效率损耗通过能量平衡影响调度，不另加一笔损耗费用。")
    err = analysis["costDetail"]["reconciliationErrorYuan"]
    text.append("成本分项与目标函数核对差额：%.6g / %.6g元。" % (err[0], err[1]))
    text.append("")
    text.append("二、火电运行")
    text.append("煤电和气电合计启动%d次、停机%d次。"
                % (sum(r["starts"] for r in analysis["thermalSummary"]),
                   sum(r["stops"] for r in analysis["thermalSummary"])))
    text.append("启停次数包含首时段相对初始状态的切换；检修单列，不将其全部归因于经济调峰。")
    text.append("逐台结果见MAT文件中的ThermalSummary。")
    text.append("")
    text.append("三、储能运行")
    for r in analysis["storageSummary"]:
        text.append("%s / %s：充电%.2fMWh，放电%.2fMWh，SOC范围%.2f%%—%.2f%%，"
                    "放电侧等效循环%.3f次，首末能量差%.6gMWh。"
                    % (r["type"], r["name"], r["chargeMWh"], r["dischargeMWh"],
                       r["minSocPercent"], r["maxSocPercent"],
                       r["equivalentDischargeCycles"], r["terminalEnergyErrorMWh"]))
    text.append("SOC=储存能量/额定能量；抽蓄为等效能量比例，并非实际水位。"
                "等效循环=放电量/额定能量，不等同于寿命模型循环次数。")
    text.append("")
    text.append("四、风光消纳与供电")
    for it in analysis["renewableSummary"]["items"]:
        rate = "%.2f%%" % it["utilizationPercent"] if it["utilizationPercent"] is not None else "NaN"
        text.append("%s：可用电量%.2fMWh，弃电%.2fMWh，利用率%s。"
                    % (it["type"], it["availableMWh"], it["curtailedMWh"], rate))
    text.append("风光统计包含友好电站，站内充电计入已利用电量；未供电电量%.2fMWh。"
                % metrics["energyNotServed"])
    text.append("以上仅对应本次周期与参数，不代表全年运行结论；"
                "有限求解间隙下，逐时调度可能存在多个近优方案。")
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    print("已导出: %s" % path)


# ---------- 七张分析图（MATLAB 八~十一节 figure 段逐参数对照）----------
# 图统一规格（:1490-1495）：FontName Microsoft YaHei / FontSize 10 / Box off /
# TickDir out / grid on alpha 0.12（02 火电启停不 grid）→ 180 dpi exportgraphics。

def _styled_axes(ax, grid=True):
    """分析图统一坐标轴样式（:1490-1495：Box off/TickDir out/grid/标题字号）。

    Args:
        ax: matplotlib 坐标轴。
        grid (bool): False 时不画网格（02 火电启停图不 grid）。

    Returns:
        None
    """
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(direction="out")
    ax.grid(alpha=0.12) if grid else ax.grid(False)
    ax.title.set_fontsize(11)


def _tick_rows(plot_rows):
    """MATLAB tickRows：round(linspace(0,n-1,min(9,n))) 去重。

    Args:
        plot_rows: 绘图窗口行序号数组（只取长度）。

    Returns:
        np.ndarray: 去重后的刻度行号。
    """
    return np.unique(np.round(np.linspace(
        0, len(plot_rows) - 1, min(9, len(plot_rows)))).astype(int))


def _fig_frame(fig, suptitle, subtitle):
    """图框总标题 + 副标题（:1490 规格）。

    Args:
        fig: matplotlib figure。
        suptitle: 总标题文字。
        subtitle: 副标题文字；None 不画。

    Returns:
        None
    """
    fig.suptitle(suptitle, fontsize=14, fontweight="bold")
    if subtitle:
        fig.text(0.5, 0.925, subtitle, ha="center", fontsize=10,
                 color=(0.40, 0.43, 0.48))


def plot_analysis_figures(inp, solution, analysis, out_dir) -> list:
    """生成 01~07 PNG（落 out_dir），返回文件名列表。00 设备堆叠由 plot_operation
    另行复制（同一张图 MATLAB 也归 operationFigures）。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。
        analysis (dict): 五表总装。
        out_dir: 输出目录（Path）。

    Returns:
        list: 已写出的 PNG 文件名列表。
    """
    plt = PL.plt
    plt.rcParams["font.family"] = ["Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False

    V = solution.values
    U = solution.units
    T = inp.time.T
    plot_rows = np.arange(0, min(168, T))
    plot_time = inp.time.Time[plot_rows]
    ticks = _tick_rows(plot_rows)
    tick_labels = [t.strftime("%m/%d %H:%M") for t in plot_time[ticks]]
    written = []
    obj = solution.four_items["objective"]

    # ---- 01_成本构成（1300-1328：1×2 横向条形，双色）----
    six = analysis["costSummary"]["items"]
    ten = analysis["costDetail"]["items"]
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 7.2), facecolor="w")
    for ax, items, color, xtitle in (
            (axes[0], six, (0.16, 0.43, 0.65), "目标函数六大项"),
            (axes[1], ten, (0.21, 0.60, 0.51), "按费用性质拆分")):
        y = np.arange(len(items)) + 1
        ax.barh(y, [it["costWanYuan"] for it in items], height=0.65,
                color=color, edgecolor="none")
        ax.set_yticks(y)
        ax.set_yticklabels([it["category"] for it in items])
        ax.invert_yaxis()
        ax.set_xlabel("成本 / 万元")
        ax.set_title(xtitle)
        _styled_axes(ax)
    fig.tight_layout(rect=(0, 0.02, 1, 0.88))
    _fig_frame(fig, "海南成本构成｜全周期%d小时，总成本 %.2f 万元" % (T, obj["value"] / 1e4),
               "两幅图为同一总成本的不同分解；固定运维按本次时长折算")
    fig.savefig(out_dir / "01_成本构成.png", dpi=180, bbox_inches="tight")
    plt.close(fig); written.append("01_成本构成.png")

    # ---- 02_火电启停（1338-1375：状态热图 + 在线数 stairs + 启停 bar）----
    n_th = len(U["thermal"]["keys"])
    u_thermal = np.vstack([V["u_th"], V["u_gas"]]).astype(int)
    v_thermal = np.vstack([V["v_th"], V["v_gas"]]).astype(int)
    w_thermal = np.vstack([V["w_th"], V["w_gas"]]).astype(int)
    mtn = np.vstack([np.asarray(U["thermal"]["maintenance"]),
                     np.asarray(U["gas"]["maintenance"])]).astype(int)
    keys = [str(k) for k in U["thermal"]["keys"]] + \
           [str(k) for k in U["gas"]["keys"]]
    names = list(U["thermal"].get("names", U["thermal"]["keys"])) + \
            list(U["gas"].get("names", U["gas"]["keys"]))
    state = u_thermal[:, plot_rows].copy()
    state[mtn[:, plot_rows] > 0.5] = 2
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), facecolor="w",
                             gridspec_kw={"width_ratios": [2, 1]})
    ax = axes[0, 0]
    cmap = [(0.91, 0.93, 0.95), (0.19, 0.55, 0.72), (0.92, 0.62, 0.27)]
    from matplotlib.colors import ListedColormap
    ax.imshow(state, aspect="auto", cmap=ListedColormap(cmap),
              vmin=0, vmax=2, interpolation="nearest")
    ax.set_yticks(np.arange(len(keys)))
    ax.set_yticklabels([f"{k} {n}" for k, n in zip(keys, names)], fontsize=9)
    ax.set_xticks(ticks)
    ax.set_xticklabels(tick_labels)
    cb = fig.colorbar(ax.images[0], ax=ax, ticks=[0, 1, 2], fraction=0.03)
    cb.ax.set_yticklabels(["停机", "运行", "检修"])
    ax.set_title("逐台运行状态"); ax.set_xlabel("时间（月/日 时:分）")
    _styled_axes(ax, grid=False)
    ax = axes[0, 1]
    ax.stairs(u_thermal[:n_th, plot_rows].sum(axis=0), np.arange(len(plot_rows) + 1),
              linewidth=1.7)
    ax.stairs(u_thermal[n_th:, plot_rows].sum(axis=0), np.arange(len(plot_rows) + 1),
              linewidth=1.7)
    ax.set_xlim(0.5, len(plot_rows) + 0.5)
    ax.set_xticks(ticks); ax.set_xticklabels(tick_labels)
    ax.set_ylabel("在线机组数 / 台"); ax.legend(["煤电", "气电"], loc="best")
    ax.set_title("在线机组数量"); _styled_axes(ax, grid=False)
    ax = axes[1, 0]
    events = np.array([[v_thermal[:n_th].sum(), w_thermal[:n_th].sum()],
                       [v_thermal[n_th:].sum(), w_thermal[n_th:].sum()]])
    x = np.arange(2)
    ax.bar(x - 0.2, events[:, 0], 0.4, label="启动")
    ax.bar(x + 0.2, events[:, 1], 0.4, label="停机")
    ax.set_xticks(x); ax.set_xticklabels(["煤电", "气电"])
    ax.set_ylabel("次数"); ax.legend(loc="best")
    ax.set_title("全部%d小时启停次数（含初态切换）" % T); _styled_axes(ax, grid=False)
    axes[1, 1].axis("off")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    _fig_frame(fig, "海南火电运行与启停", None)
    fig.savefig(out_dir / "02_火电启停.png", dpi=180, bbox_inches="tight")
    plt.close(fig); written.append("02_火电启停.png")

    # ---- 03/04/05 储能×3（1380-1435：功率上下两栏 stacked + SOC 轨迹）----
    sto_spec = [("抽水蓄能", "pumped_storage", "P_ps_pump", "P_ps_gen", "E_ps", "03_抽水蓄能"),
                ("独立电化学储能", "electrochemical", "P_es_charge", "P_es_discharge",
                 "E_es", "04_独立电化学储能"),
                ("友好电站储能", "friendly", "P_fr_charge", "P_fr_discharge",
                 "E_fr", "05_友好电站储能")]
    for label, group, ch_key, dis_key, e_key, fname in sto_spec:
        if not len(U[group]["keys"]):
            continue
        E = V[e_key]
        rated = np.asarray(U[group]["energy_capacity"])
        soc = 100.0 * E / rated[:, None]
        ch = V[ch_key][:, plot_rows]
        dis = V[dis_key][:, plot_rows]
        names = U[group].get("names", U[group]["keys"])
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14.5, 8), facecolor="w")
        x = np.arange(1, len(plot_rows) + 1)
        ax1.bar(x, dis.sum(axis=0), 0.9, facecolor=(0.18, 0.49, 0.70),
                edgecolor="none", label="发电/放电")
        ax1.bar(x, -ch.sum(axis=0), 0.9, facecolor=(0.25, 0.70, 0.65),
                edgecolor="none", label="抽水/充电")
        ax1.axhline(0, color=(0.4, 0.4, 0.4), lw=0.8)
        ax1.set_xlim(0.5, len(plot_rows) + 0.5)
        ax1.set_xticks(ticks + 1); ax1.set_xticklabels(tick_labels)
        ax1.set_ylabel("功率 / MW"); ax1.set_title("同类型功率汇总，充电为负")
        ax1.legend(loc="upper center", bbox_to_anchor=(0.5, 1.18), ncol=2,
                   frameon=False)
        _styled_axes(ax1)
        boundary = np.arange(0, len(plot_rows) + 1)   # T+1 边界点（E 列同宽）
        colors = plt.cm.tab10(np.arange(soc.shape[0]))
        for i, n in enumerate(names):
            ax2.plot(boundary, soc[i, plot_rows[0]:plot_rows[-1] + 2],
                     color=colors[i], lw=1.7, label=str(n))
            ax2.axhline(100 * U[group]["soc_min"][i], ls="--", color=colors[i], lw=0.9)
            ax2.axhline(100 * U[group]["soc_max"][i], ls="--", color=colors[i], lw=0.9)
        bticks = np.unique(np.round(np.linspace(
            0, len(plot_rows), min(9, len(plot_rows) + 1))).astype(int))
        ax2.set_xticks(bticks)
        # 边界 k = E 列号 k（k=0 是初值 E(:,1)），时刻 = 起点 + (plotRows(1)+k) 小时
        ax2.set_xticklabels([
            (inp.time.Time[0] + __import__("datetime").timedelta(
                hours=int(plot_rows[0] + b))).strftime("%m/%d %H:%M")
            for b in bticks])
        ax2.set_xlim(0, len(plot_rows)); ax2.set_ylim(0, 100)
        ax2.set_ylabel("SOC / %"); ax2.set_xlabel("时段边界时间（月/日 时:分）")
        ax2.set_title("逐台SOC轨迹，虚线为各自上下限")
        ax2.legend(loc="upper center", bbox_to_anchor=(0.5, 1.18), ncol=min(4, len(names)),
                   frameon=False)
        _styled_axes(ax2)
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        _fig_frame(fig, "海南%s运行分析" % label, None)
        fig.savefig(out_dir / (fname + ".png"), dpi=180, bbox_inches="tight")
        plt.close(fig); written.append(fname + ".png")

    # ---- 06_设备出力统计（1438-1455：1×2 barh）----
    dev = analysis["deviceSummary"]["items"]
    fig, axes = plt.subplots(1, 2, figsize=(14.0, 6.5), facecolor="w")
    y = np.arange(len(dev)) + 1
    axes[0].barh(y, [it["outputEnergyMWh"] / 1e4 for it in dev],
                 color=(0.19, 0.55, 0.72), edgecolor="none")
    axes[0].set_yticks(y); axes[0].set_yticklabels([it["device"] for it in dev])
    axes[0].invert_yaxis()
    axes[0].set_xlabel("发电/放电量 / 万MWh"); axes[0].set_title("全周期电量")
    _styled_axes(axes[0])
    axes[1].barh(y, [it["peakOutputMW"] for it in dev],
                 color=(0.30, 0.65, 0.52), edgecolor="none")
    axes[1].set_yticks(y); axes[1].set_yticklabels([it["device"] for it in dev])
    axes[1].invert_yaxis()
    axes[1].set_xlabel("最大出力 / MW"); axes[1].set_title("全周期最大出力")
    _styled_axes(axes[1])
    fig.tight_layout(rect=(0, 0.02, 1, 0.88))
    _fig_frame(fig, "海南设备出力统计｜全部%d小时" % T,
               "储能放电单列；友好站计净上网量，不能将本图解释为一次能源结构")
    fig.savefig(out_dir / "06_设备出力统计.png", dpi=180, bbox_inches="tight")
    plt.close(fig); written.append("06_设备出力统计.png")

    # ---- 07_风光消纳（1458-1481：2×1 可用/已利用/弃电）----
    F = solution.inputs_fields
    wind_av = (np.asarray(F["P_wind_available"]).sum(axis=0)
               + np.asarray(U["friendly"]["wind_available"]).sum(axis=0))
    pv_av = (np.asarray(F["P_pv_available"]).sum(axis=0)
             + np.asarray(U["friendly"]["pv_available"]).sum(axis=0))
    wind_curt = (V["P_wind_curt"] + V["P_fr_wind_curt"]).sum(axis=0)
    pv_curt = (V["P_pv_curt"] + V["P_fr_pv_curt"]).sum(axis=0)
    renew = [(wind_av, wind_curt, "风电"), (pv_av, pv_curt, "光伏")]
    ratios = [it["utilizationPercent"] for it in analysis["renewableSummary"]["items"]]
    fig, axes = plt.subplots(2, 1, figsize=(14.5, 7.5), facecolor="w")
    for k, (av, curt, label) in enumerate(renew):
        ax = axes[k]
        ax.plot(plot_rows + 1, av[plot_rows], "--", color=(0.55, 0.57, 0.61), lw=1.5)
        ax.plot(plot_rows + 1, (av - curt)[plot_rows], "-",
                color=(0.17, 0.57, 0.49), lw=1.7)
        ax.bar(plot_rows + 1, curt[plot_rows], 0.6, facecolor=(0.92, 0.57, 0.29),
               edgecolor="none", alpha=0.55)
        ax.set_xlim(0.5, len(plot_rows) + 0.5)
        ax.set_xticks(ticks + 1); ax.set_xticklabels(tick_labels)
        ax.set_ylabel("功率 / MW")
        ax.legend(["可用功率", "已利用功率", "弃电功率"], loc="upper center",
                  bbox_to_anchor=(0.5, 1.2), ncol=3, frameon=False)
        ax.set_title("%s｜全周期利用率 %.2f%%" % (label, ratios[k]))
        _styled_axes(ax)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    _fig_frame(fig, "海南风光消纳（包含友好电站）", None)
    fig.savefig(out_dir / "07_风光消纳.png", dpi=180, bbox_inches="tight")
    plt.close(fig); written.append("07_风光消纳.png")
    return written


# ---------- 十二、总入口：时间戳目录 + 全件落盘 ----------

def export_analysis(inp, solution, metrics, analysis=None, result_root=None) -> None:
    """分析结果四件套落盘：00 PNG + 01~07 PNG + 海南分析结果.mat + 分析摘要.txt。
    目录 = RESULT_DIR/分析结果/{起始 yyyymmdd_HHMM}_{T}h_{当前时间戳}（对齐 :1484-1486）。

    Args:
        inp (Inputs): 系统公共输入。
        solution (Solution): 求解结果。
        metrics (dict): 指标字典。
        analysis: 五表总装；None 时现算。
        result_root: 输出根目录；None 时用 RESULT_DIR。

    Returns:
        None
    """
    from pathlib import Path
    if analysis is None:
        analysis = build_analysis(inp, solution)
    root = Path(result_root) if result_root else Path(config.RESULT_DIR)
    out_dir = root / "分析结果" / ("%s_%dh_%s" % (
        inp.time.Time[0].strftime("%Y%m%d_%H%M"), inp.time.T,
        datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]))
    out_dir.mkdir(parents=True, exist_ok=True)

    # 00_设备堆叠_N.png：plot_operation 产物复制（MATLAB operationFigures 同归此地）
    for n, png in enumerate(sorted(Path(config.RESULT_DIR).glob("设备运行_*.png")), 1):
        shutil.copy(png, out_dir / ("00_设备堆叠_%d.png" % n))

    plot_analysis_figures(inp, solution, analysis, out_dir)
    write_mat(inp, solution, metrics, analysis, out_dir / "海南分析结果.mat")
    write_summary_txt(inp, solution, metrics, analysis, out_dir / "分析摘要.txt")
    print("分析结果目录: %s" % out_dir)


# ---------- 对外入口类（v2，2026-10-16）：四层架构之分析导出层 ----------

class AnalysisExport:
    """§八~十二分析导出入口：metrics（原 solution.compute_metrics 并入）/
    五表 / views（减 positionChart）/ 四件套落盘（01~07 PNG + .mat + 摘要.txt）。
    只读 Solution（下游零 gurobipy 依赖）；服务层经 run.py 转发使用本类。"""

    def __init__(self, inp, solution):
        """保存输入与求解结果；metrics 首次调用后缓存。

        Args:
            inp (Inputs): 系统公共输入。
            solution (Solution): 求解结果。

        Returns:
            None
        """
        self.inp = inp
        self.solution = solution
        self._metrics = None

    def metrics(self) -> dict:
        """组装期派生指标（残差/利用率/失负荷/备用裕度/CO₂/储能闭合）。

        Returns:
            dict: 指标字典（首次计算后缓存）。
        """
        if self._metrics is None:
            self._metrics = _compute_metrics(self.inp, self.solution)
        return self._metrics

    def build_analysis(self) -> dict:
        """五表总装（camelCase，口径对齐 MATLAB 八~十一节）。

        Returns:
            dict: 五表总装。
        """
        return build_analysis(self.inp, self.solution)

    def build_views(self) -> dict:
        """前端 views 块（8 tab；positionChart 由 service 侧组装注入）。

        Returns:
            dict: views 块。
        """
        return build_views(self.inp, self.solution)

    def export(self, metrics: dict | None = None, result_root=None) -> None:
        """分析结果四件套落盘（00 PNG + 01~07 PNG + .mat + 摘要.txt）。

        Args:
            metrics (dict | None): 指标字典；None 时用 self.metrics()。
            result_root: 输出根目录；None 时用 RESULT_DIR。

        Returns:
            None
        """
        export_analysis(self.inp, self.solution,
                        metrics if metrics is not None else self.metrics(),
                        result_root=result_root)
