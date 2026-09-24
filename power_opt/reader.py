#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/21 17:37
@Author  : weiyutao
@File    : reader.py
读取系统输入文件，截取系统公共输入，不负责特定能源模型输入读取。
对应 MATLAB §1 中的骨架表（1.2/1.10-1.15/1.20/1.22）；煤电表已迁往
model/units/thermal.py。空表电源（气电/水电/储能/光热等）行为与
MATLAB 等价（N_*=0 → 零维变量），各轮次在各自电源文件中补齐。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass

import config


def _read(path, sheet, required):
    """读一个 sheet 并核对表头；缺列即报错。数据审查不做、不补值。"""
    df = pd.read_excel(path, sheet_name=sheet)
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"{path.name}[{sheet}] 缺列: {missing}")
    return df


def _zstr(v):
    """分区编号统一成字符串：Excel 里 1（数字）与 '1'（文本）是同一分区。"""
    if isinstance(v, (int, float, np.integer, np.floating)) and float(v) == int(v):
        return str(int(v))
    return str(v)


def _to_hours(s: pd.Series) -> pd.Series:
    """Excel 时间 → datetime；数字按 Excel 日期序列换算 → 舍入到最近整点。
    对应 MATLAB 82-85、277-288 行的 datetime/ConvertFrom + dateshift。"""
    if pd.api.types.is_numeric_dtype(s):
        s = pd.to_datetime(s, unit="D", origin="1899-12-30")
    else:
        s = pd.to_datetime(s)
    return s.dt.round("h")


def _match_rows(sheet_time: pd.Series, want: pd.DatetimeIndex, what: str) -> np.ndarray:
    """MATLAB ismember(Time, xxxTime)：按整点时刻找行号；缺时刻即报错。"""
    pos = {ts: i for i, ts in enumerate(sheet_time)}
    try:
        return np.array([pos[t] for t in want])
    except KeyError as e:
        raise KeyError(f"{what} 曲线缺时刻 {e}（不补值，请查数据表）") from None


@dataclass
class Inputs:
    """MATLAB 工作区输入变量的 Python 侧镜像，字段名与 MATLAB 一致。"""
    # 时间（1.1）
    T: int
    Delta_t: float
    Time: pd.DatetimeIndex
    Day: list
    Nday: int
    # 系统经济参数（1.2）
    costScale: float
    fixedCostPeriodRatio: float
    fixedCostScale: float
    c_shed: float; c_wind_curt: float; c_pv_curt: float; c_hyd_curt: float
    c_CO2: float; c_SO2: float; c_NOX: float; c_dust: float
    # 分区（1.11）
    Narea: int; AreaID: list; AreaName: list
    areaProvince: np.ndarray          # 各分区 → 省序号（0 基）
    ProvinceID: list; Nprovince: int
    # 检修计划（共用总表，各电源文件按 Type 领取）
    maintenance: pd.DataFrame
    # 风光装机（1.8/1.9，行空间）
    area_wind: np.ndarray; W_capacity: np.ndarray; c_wind_FOM: np.ndarray
    area_pv: np.ndarray; PV_capacity: np.ndarray; c_pv_FOM: np.ndarray
    # 分区曲线（1.12）
    P_load: np.ndarray                # Narea×T
    P_wind_available: np.ndarray; P_pv_available: np.ndarray
    # 省内断面（1.13）
    N_line: int; line_from: np.ndarray; line_to: np.ndarray
    P_line_forward: np.ndarray; P_line_reverse: np.ndarray
    # 省级参数（1.15）
    reserveDuration: np.ndarray; adequacyFactor: np.ndarray
    coldReserveRatio: np.ndarray; renewableUtilizationMin: np.ndarray
    CO2Limit: np.ndarray
    # 分区负荷参数（1.20）
    ShedMaxRatio: np.ndarray; ShiftWindow_h: np.ndarray
    ShiftCost: np.ndarray; CutCost: np.ndarray
    DRFixedCost: np.ndarray; AnnualCutEnergy_MWh: np.ndarray
    # 需求响应（1.20）
    P_shift_out_max: np.ndarray; P_shift_in_max: np.ndarray; P_cut_max: np.ndarray
    E_cut_day_max: np.ndarray          # Narea×Nday
    shiftFrom: list; shiftTo: list     # 每分区的转移边端点（小时序号）
    # 周负荷峰值（1.22）
    P_week_peak: np.ndarray           # Nprovince×T


def load_inputs() -> Inputs:
    T = config.T
    Delta_t = float(config.DELTA_T)
    Time = pd.date_range(pd.Timestamp(config.START_TIME), periods=T, freq="h")

    # ---- 1.2 系统经济参数（MATLAB 66-75）----
    sd = _read(config.SYSTEM_FILE, "系统参数", ["Parameter", "Value"])
    p = dict(zip(sd["Parameter"], sd["Value"]))
    need = ["costScaleToYuan", "fixedCostReferenceHours", "shedCost", "windCurtCost",
            "pvCurtCost", "hydroCurtCost", "co2Price", "so2Price", "noxPrice", "dustPrice"]
    if miss := [k for k in need if k not in p]:
        raise KeyError(f"系统参数表缺少参数行: {miss}")
    costScale = float(p["costScaleToYuan"])
    fixedCostPeriodRatio = T * Delta_t / float(p["fixedCostReferenceHours"])  # MATLAB 73
    fixedCostScale = costScale * fixedCostPeriodRatio                         # MATLAB 74
    c_shed = float(p["shedCost"]); c_wind_curt = float(p["windCurtCost"])
    c_pv_curt = float(p["pvCurtCost"]); c_hyd_curt = float(p["hydroCurtCost"])
    c_CO2 = float(p["co2Price"]); c_SO2 = float(p["so2Price"])
    c_NOX = float(p["noxPrice"]); c_dust = float(p["dustPrice"])

    # ---- 1.11 分区表 + 省序（MATLAB 293-315、358-365）----
    areaData = _read(config.UNIT_FILE, "分区表", ["AreaID", "AreaName", "ProvinceID"])
    ProvinceID = list(dict.fromkeys(areaData["ProvinceID"].astype(str)))  # unique stable
    provPos = {s: i for i, s in enumerate(ProvinceID)}
    areaProvince = areaData["ProvinceID"].astype(str).map(provPos).to_numpy()
    AreaID = list(dict.fromkeys([_zstr(v) for v in areaData["AreaID"]]))
    nameMap = dict(zip(areaData["AreaID"].map(_zstr), areaData["AreaName"].astype(str)))
    AreaName = [nameMap[a] for a in AreaID]
    Narea = len(AreaID)
    Nprovince = len(ProvinceID)
    areaPos = {a: i for i, a in enumerate(AreaID)}

    # ---- 检修计划（MATLAB 79-85）：多电源共用一张表，骨架层读取、各电源文件按 Type 领取 ----
    mtn = _read(config.UNIT_FILE, "检修计划",
                ["Type", "UnitKey", "StartTime", "EndTime", "Enabled"])
    mtn = mtn[mtn["Enabled"] == 1].copy()
    mtn["StartTime"] = _to_hours(mtn["StartTime"])
    mtn["EndTime"] = _to_hours(mtn["EndTime"])

    # ---- 1.8/1.9 风电光伏装机（行空间，MATLAB 254-268）----
    req_w = ["分区编号", "分区名称", "装机容量_MW", "FOM_COST_PERIOD"]
    windData = _read(config.UNIT_FILE, "风电", req_w)
    zone_wind = [_zstr(v) for v in windData["分区编号"]]
    if miss := [z for z in zone_wind if z not in areaPos]:
        raise KeyError(f"风电分区编号 {set(miss)} 不在分区表中")
    area_wind = np.array([areaPos[z] for z in zone_wind])
    W_capacity = windData["装机容量_MW"].to_numpy(float)
    c_wind_FOM = fixedCostScale * windData["FOM_COST_PERIOD"].to_numpy(float)  # MATLAB 262
    pvData = _read(config.UNIT_FILE, "光伏", req_w)
    zone_pv = [_zstr(v) for v in pvData["分区编号"]]
    if miss := [z for z in zone_pv if z not in areaPos]:
        raise KeyError(f"光伏分区编号 {set(miss)} 不在分区表中")
    area_pv = np.array([areaPos[z] for z in zone_pv])
    PV_capacity = pvData["装机容量_MW"].to_numpy(float)
    c_pv_FOM = fixedCostScale * pvData["FOM_COST_PERIOD"].to_numpy(float)      # MATLAB 270

    # ---- 1.10-1.12 负荷/风光曲线 → 分区功率（MATLAB 270-330）----
    loadData = _read(config.PROFILE_FILE, "负荷", ["时间"] + AreaName)
    loadTime = _to_hours(loadData["时间"])
    loadRows = _match_rows(loadTime, Time, "负荷")
    windCurve = _read(config.PROFILE_FILE, "风电", ["时间"] + AreaName)
    windRows = _match_rows(_to_hours(windCurve["时间"]), Time, "风电")
    pvCurve = _read(config.PROFILE_FILE, "光伏", ["时间"] + AreaName)
    pvRows = _match_rows(_to_hours(pvCurve["时间"]), Time, "光伏")
    baseData = _read(config.PROFILE_FILE, "归一化基准", ["类型", "分区", "全年最大值(基准)"])
    baseRows = baseData[(baseData["类型"].astype(str) == "负荷")
                        & (baseData["分区"].astype(str).isin(AreaName))]
    baseMap = {r["分区"]: float(r["全年最大值(基准)"]) for _, r in baseRows.iterrows()}
    if miss := [n for n in AreaName if n not in baseMap]:
        raise KeyError(f"归一化基准缺分区行: {miss}")
    W_cap_area = np.zeros(Narea); W_cap_area[area_wind] = W_capacity   # MATLAB 323
    PV_cap_area = np.zeros(Narea); PV_cap_area[area_pv] = PV_capacity  # MATLAB 324
    P_load = np.zeros((Narea, T))
    P_wind_available = np.zeros((Narea, T))
    P_pv_available = np.zeros((Narea, T))
    for r in range(Narea):                                              # MATLAB 325-330
        name = AreaName[r]
        P_load[r, :] = loadData.loc[loadRows, name].to_numpy(float) * baseMap[name]
        P_wind_available[r, :] = windCurve.loc[windRows, name].to_numpy(float) * W_cap_area[r]
        P_pv_available[r, :] = pvCurve.loc[pvRows, name].to_numpy(float) * PV_cap_area[r]

    # ---- 1.13 省内断面（MATLAB 332-342）----
    lineData = _read(config.SYSTEM_FILE, "省内断面",
                     ["FromArea", "ToArea", "ForwardMW", "ReverseMW", "Enabled"])
    lineData = lineData[lineData["Enabled"] == 1]
    fl = [_zstr(v) for v in lineData["FromArea"]]; tl = [_zstr(v) for v in lineData["ToArea"]]
    if miss := [z for z in fl + tl if z not in areaPos]:
        raise KeyError(f"省内断面分区编号 {set(miss)} 不在分区表中")
    N_line = len(lineData)
    line_from = np.array([areaPos[z] for z in fl]); line_to = np.array([areaPos[z] for z in tl])
    P_line_forward = lineData["ForwardMW"].to_numpy(float)
    P_line_reverse = lineData["ReverseMW"].to_numpy(float)

    # ---- 1.15 省级约束（MATLAB 358-369）----
    provData = _read(config.SYSTEM_FILE, "省级约束",
                     ["ProvinceID", "AdequacyFactor", "ColdReserveRatio",
                      "RenewableUtilizationMin", "CO2Limit_t", "ReserveDuration_h"])
    provData = provData[provData["ProvinceID"].astype(str).isin(ProvinceID)]
    provData = provData.set_index("ProvinceID").loc[ProvinceID]        # 与 MATLAB 同序
    if provData.isna().any().any():
        raise KeyError(f"省级约束缺值:\n{provData[provData.isna().any(axis=1)]}")
    adequacyFactor = provData["AdequacyFactor"].to_numpy(float)
    coldReserveRatio = provData["ColdReserveRatio"].to_numpy(float)
    renewableUtilizationMin = provData["RenewableUtilizationMin"].to_numpy(float)
    CO2Limit = provData["CO2Limit_t"].to_numpy(float)
    reserveDuration = provData["ReserveDuration_h"].to_numpy(float)

    # ---- 1.20 分区负荷参数 + 需求响应（MATLAB 488-530）----
    lp = _read(config.SYSTEM_FILE, "分区负荷参数",
               ["AreaID", "ShedMaxRatio", "ShiftWindow_h", "ShiftCost", "CutCost",
                "DRFixedCost", "AnnualCutEnergy_MWh"])
    lp = lp[lp["AreaID"].map(_zstr).isin(AreaID)]
    lp = lp.set_index(lp["AreaID"].map(_zstr)).loc[AreaID]
    ShedMaxRatio = lp["ShedMaxRatio"].to_numpy(float)
    ShiftWindow_h = lp["ShiftWindow_h"].to_numpy(float)
    ShiftCost = lp["ShiftCost"].to_numpy(float); CutCost = lp["CutCost"].to_numpy(float)
    DRFixedCost = lp["DRFixedCost"].to_numpy(float)
    AnnualCutEnergy_MWh = lp["AnnualCutEnergy_MWh"].to_numpy(float)

    drTime = _read(config.SYSTEM_FILE, "需求响应时序",
                   ["Time", "AreaID", "ShiftOutMax_MW", "ShiftInMax_MW", "CutMax_MW"])
    drDaily = _read(config.SYSTEM_FILE, "需求响应日限额", ["Date", "AreaID", "CutEnergyMax_MWh"])
    Day = list(dict.fromkeys(Time.normalize()))                         # MATLAB 500
    Nday = len(Day)
    P_shift_out_max = np.zeros((Narea, T)); P_shift_in_max = np.zeros((Narea, T))
    P_cut_max = np.zeros((Narea, T)); E_cut_day_max = np.zeros((Narea, Nday))
    shiftFrom = []; shiftTo = []
    for r in range(Narea):
        aid = AreaID[r]
        rows = drTime[drTime["AreaID"].map(_zstr) == aid]
        rows = rows.set_index(_to_hours(rows["Time"]))
        try:
            sel = rows.loc[Time]
        except KeyError as e:
            raise KeyError(f"需求响应时序缺分区 {aid} 的时刻 {e}") from None
        P_shift_out_max[r, :] = sel["ShiftOutMax_MW"].to_numpy(float)
        P_shift_in_max[r, :] = sel["ShiftInMax_MW"].to_numpy(float)
        P_cut_max[r, :] = sel["CutMax_MW"].to_numpy(float)
        drows = drDaily[drDaily["AreaID"].map(_zstr) == aid]
        drows = drows.set_index(drows["Date"].dt.normalize())
        try:
            dsel = drows.loc[Day]
        except KeyError as e:
            raise KeyError(f"需求响应日限额缺分区 {aid} 的日期 {e}") from None
        E_cut_day_max[r, :] = dsel["CutEnergyMax_MWh"].to_numpy(float)
        # 同日、非自身、时间窗内的有向转移边（MATLAB 517-529；避免稠密变量）
        area_from = []; area_to = []
        for d in range(Nday):
            dayHours = np.where(Time.normalize() == Day[d])[0]
            o, j = np.meshgrid(dayHours, dayHours, indexing="ij")
            ok = (o != j) & (np.abs(o - j) * Delta_t <= ShiftWindow_h[r])
            area_from.append(o[ok]); area_to.append(j[ok])
        shiftFrom.append(np.concatenate(area_from) if area_from else np.array([], int))
        shiftTo.append(np.concatenate(area_to) if area_to else np.array([], int))

    # ---- 1.22 每周负荷峰值（MATLAB 551-565；窗口按数据实际行截取）----
    weekStart = Time.normalize() - pd.to_timedelta(Time.dayofweek, unit="D")  # 周一起始
    Week = list(dict.fromkeys(weekStart))
    P_week_peak = np.zeros((Nprovince, T))
    for p_idx in range(Nprovince):
        provinceLoad = np.zeros(len(loadData))
        for r in np.where(areaProvince == p_idx)[0]:
            provinceLoad += loadData[AreaName[r]].to_numpy(float) * baseMap[AreaName[r]]
        for wk in Week:
            inWindow = (loadTime.to_numpy() >= np.datetime64(wk)) & \
                       (loadTime.to_numpy() < np.datetime64(wk + pd.Timedelta(days=7)))
            peak = provinceLoad[inWindow].max() if inWindow.any() else float("nan")
            if not np.isfinite(peak):
                raise KeyError(f"周 {wk} 落在负荷数据范围外，无峰值（不造数）")
            P_week_peak[p_idx, weekStart == wk] = peak

    # ---- 数值安全检查：禁止裸 NaN 进模型（code_basic_rule §6）----
    for name, arr in [("P_load", P_load), ("P_wind_available", P_wind_available),
                      ("P_pv_available", P_pv_available),
                      ("P_shift_out_max", P_shift_out_max), ("P_shift_in_max", P_shift_in_max),
                      ("P_cut_max", P_cut_max), ("E_cut_day_max", E_cut_day_max),
                      ("P_week_peak", P_week_peak)]:
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{name} 含 NaN/Inf，检查上游数据")

    return Inputs(
        T=T, Delta_t=Delta_t, Time=Time, Day=Day, Nday=Nday,
        costScale=costScale, fixedCostPeriodRatio=fixedCostPeriodRatio,
        fixedCostScale=fixedCostScale,
        c_shed=c_shed, c_wind_curt=c_wind_curt, c_pv_curt=c_pv_curt, c_hyd_curt=c_hyd_curt,
        c_CO2=c_CO2, c_SO2=c_SO2, c_NOX=c_NOX, c_dust=c_dust,
        Narea=Narea, AreaID=AreaID, AreaName=AreaName, areaProvince=areaProvince,
        ProvinceID=ProvinceID, Nprovince=Nprovince,
        maintenance=mtn,
        area_wind=area_wind, W_capacity=W_capacity, c_wind_FOM=c_wind_FOM,
        area_pv=area_pv, PV_capacity=PV_capacity, c_pv_FOM=c_pv_FOM,
        P_load=P_load, P_wind_available=P_wind_available, P_pv_available=P_pv_available,
        N_line=N_line, line_from=line_from, line_to=line_to,
        P_line_forward=P_line_forward, P_line_reverse=P_line_reverse,
        reserveDuration=reserveDuration, adequacyFactor=adequacyFactor,
        coldReserveRatio=coldReserveRatio, renewableUtilizationMin=renewableUtilizationMin,
        CO2Limit=CO2Limit,
        ShedMaxRatio=ShedMaxRatio, ShiftWindow_h=ShiftWindow_h, ShiftCost=ShiftCost,
        CutCost=CutCost, DRFixedCost=DRFixedCost, AnnualCutEnergy_MWh=AnnualCutEnergy_MWh,
        P_shift_out_max=P_shift_out_max, P_shift_in_max=P_shift_in_max, P_cut_max=P_cut_max,
        E_cut_day_max=E_cut_day_max, shiftFrom=shiftFrom, shiftTo=shiftTo,
        P_week_peak=P_week_peak,
    )

if __name__ == "__main__":
    import numpy as np
    inp = load_inputs()
    print('T,Narea,N_line,Nprovince:', inp.T, inp.Narea, inp.N_line, inp.Nprovince)
    print('P_load[:,0]:', np.round(inp.P_load[:,0], 2))
    print('week peak equal:', np.allclose(inp.P_week_peak[0], inp.P_week_peak[0,0]),
          'val: %.2f' % inp.P_week_peak[0,0])
    print('DR edges:', [len(e) for e in inp.shiftFrom])
    print('maintenance rows:', len(inp.maintenance))
    print('FOM常数（风光+DR固定）: %.2f' % float(np.sum(inp.c_wind_FOM * inp.W_capacity)
          + np.sum(inp.c_pv_FOM * inp.PV_capacity) + np.sum(inp.DRFixedCost)))
