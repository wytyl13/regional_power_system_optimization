#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/14 16:00
@Author  : weiyutao
@File    : reader.py
读取系统输入文件，截取系统公共输入，不负责特定能源模型输入读取。

结构：Inputs 由节次大类组合而成（time/econ/areas/wind_pv/profiles/lines/
prov/dr，另加检修计划与周峰值两个单值），叶级字段名与 MATLAB 一致；
load_x 读取并返回节次对象，依赖经入参显式传递。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, fields

import config


def _read(path, sheet, required):
    """读一个 sheet 并核对表头；缺列即报错。数据审查不做、不补值。

    Args:
        path: Excel 文件路径（Path）。
        sheet: sheet 名。
        required: 必须存在的列名清单。

    Returns:
        pd.DataFrame: 该 sheet 的原始表。
    """
    df = pd.read_excel(path, sheet_name=sheet)
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"{path.name}[{sheet}] 缺列: {missing}")
    return df


def _zstr(v):
    """分区编号统一成字符串：Excel 里 1（数字）与 '1'（文本）是同一分区。

    Args:
        v: 原始单元格值。

    Returns:
        str: 统一后的分区编号字符串。
    """
    if isinstance(v, (int, float, np.integer, np.floating)) and float(v) == int(v):
        return str(int(v))
    return str(v)


def _to_hours(s: pd.Series) -> pd.Series:
    """Excel 时间 → datetime；数字按 Excel 日期序列换算 → 舍入到最近整点。
    对应 MATLAB 82-85、277-288 行的 datetime/ConvertFrom + dateshift。

    Args:
        s (pd.Series): 原始时间列（datetime 或 Excel 日期序列数）。

    Returns:
        pd.Series: 舍入到整点的 datetime 序列。
    """
    if pd.api.types.is_numeric_dtype(s):
        s = pd.to_datetime(s, unit="D", origin="1899-12-30")
    else:
        s = pd.to_datetime(s)
    return s.dt.round("h")


def _match_rows(sheet_time: pd.Series, want: pd.DatetimeIndex, what: str) -> np.ndarray:
    """MATLAB ismember(Time, xxxTime)：按整点时刻找行号；缺时刻即报错。

    Args:
        sheet_time (pd.Series): 数据表时间列（整点 datetime）。
        want (pd.DatetimeIndex): 要匹配的时刻序列。
        what (str): 报错时点名用的表名。

    Returns:
        np.ndarray: want 各时刻在表中的行号。
    """
    pos = {ts: i for i, ts in enumerate(sheet_time)}
    try:
        return np.array([pos[t] for t in want])
    except KeyError as e:
        raise KeyError(f"{what} 曲线缺时刻 {e}（不补值，请查数据表）") from None


@dataclass
class TimeAxis:
    """时间轴（1.1）：调度步数、步长、整点时刻序列与去重日期表。"""
    T: int                # 调度时段数
    Delta_t: float        # 单位步长（小时）
    Time: pd.DatetimeIndex  # 整点时刻序列
    Day: list             # 去重日期表
    Nday: int             # 天数


@dataclass
class HeatingCalendar:
    """分区供暖日历（1.1 供暖期设置）：分区清单与逐时供暖标记矩阵。"""
    ZoneID: list                  # 分区编号（字符串，供暖期设置表行序）
    isHeatingPeriod: np.ndarray   # 分区×T 供暖期标记，True=供暖期（跨年取并集）


@dataclass
class EconomicParams:
    """系统经济参数（1.2）：货币折算、固定费用折算与惩罚/排放价格。"""
    costScale: float                  # 基准期原货币 → 元 折算系数
    fixedCostPeriodRatio: float       # 基准期 → 本周期小时数折算比
    fixedCostScale: float             # 基准期原货币 → 本周期元
    c_shed: float                     # 失负荷惩罚
    c_wind_curt: float; c_pv_curt: float; c_hyd_curt: float  # 弃风/弃光/弃水惩罚
    c_CO2: float; c_SO2: float; c_NOX: float; c_dust: float  # 碳/SO2/NOx/烟尘价格


@dataclass
class Areas:
    """分区信息（1.11）：分区清单、名称、省归属与省序。"""
    Narea: int            # 分区数
    AreaID: list          # 分区编号（字符串，去重稳定）
    AreaName: list        # 分区名称（与 AreaID 对齐）
    areaProvince: np.ndarray  # 各分区 → 省序号（0 基）
    ProvinceID: list      # 省编号清单（去重稳定）
    Nprovince: int        # 省数
    areaPos: dict         # 分区编号 → 行序（下游定位用，不进 MATLAB 镜像）


@dataclass
class WindPv:
    """风光装机（1.8/1.9，行空间）：分区归属、容量与周期 FOM 成本。"""
    area_wind: np.ndarray; W_capacity: np.ndarray; c_wind_FOM: np.ndarray
    area_pv: np.ndarray; PV_capacity: np.ndarray; c_pv_FOM: np.ndarray
    W_on_cap_area: np.ndarray = None   # Narea 陆上风电分区装机（新版 1.12 拆分）
    W_off_cap_area: np.ndarray = None  # Narea 海上风电分区装机


@dataclass
class Profiles:
    """分区曲线（1.10-1.12）：逐时功率矩阵 + 供周峰值复用的原始表。"""
    P_load: np.ndarray                # Narea×T 逐时负荷
    P_wind_available: np.ndarray      # Narea×T 风电可用出力（陆海之和）
    P_pv_available: np.ndarray        # Narea×T 光伏可用出力
    loadData: pd.DataFrame            # 负荷原始表（周峰值复用）
    loadTime: pd.Series               # 负荷原始表时间列（同上）
    baseMap: dict                     # 分区 → 负荷全年最大值（同上）
    P_wind_on_available: np.ndarray = None   # Narea×T 陆上风电可用出力
    P_wind_off_available: np.ndarray = None  # Narea×T 海上风电可用出力


@dataclass
class Lines:
    """省内断面（1.13）：通道端点分区与正反向功率上限。"""
    N_line: int            # 启用断面数
    line_from: np.ndarray  # 断面送端分区序
    line_to: np.ndarray    # 断面受端分区序
    P_line_forward: np.ndarray  # 送电功率上限
    P_line_reverse: np.ndarray  # 受电功率上限


@dataclass
class ProvinceConstraints:
    """省级参数（1.15）：充裕度、冷/热备用、消纳、碳预算、备用时长。"""
    reserveDuration: np.ndarray; adequacyFactor: np.ndarray
    coldReserveRatio: np.ndarray; hotReserveRatio: np.ndarray
    renewableUtilizationMin: np.ndarray
    CO2Limit: np.ndarray


@dataclass
class DemandResponse:
    """分区负荷参数 + 需求响应（1.20）：比例/成本参数、限额矩阵与转移边。"""
    ShedMaxRatio: np.ndarray; ShiftWindow_h: np.ndarray
    ShiftCost: np.ndarray; CutCost: np.ndarray
    DRFixedCost: np.ndarray; AnnualCutEnergy_MWh: np.ndarray
    P_shift_out_max: np.ndarray; P_shift_in_max: np.ndarray; P_cut_max: np.ndarray
    E_cut_day_max: np.ndarray          # Narea×Nday
    shiftFrom: list; shiftTo: list     # 每分区的转移边端点（小时序号）


@dataclass
class Inputs:
    """系统公共输入的组合体：8 个节次大类 + 检修计划/周峰值两个单值。
    节次类内部字段名与 MATLAB 工作区变量一致（叶级一一对应）。
    """
    time: TimeAxis                     # 时间轴（1.1）
    heating: HeatingCalendar           # 供暖日历（1.1 供暖期设置，煤/气/生物质共用）
    econ: EconomicParams                # 系统经济参数（1.2）
    areas: Areas                        # 分区（1.11）
    maintenance: pd.DataFrame          # 检修计划总表（各电源文件按 Type 领取）
    wind_pv: WindPv                     # 风光装机（1.8/1.9）
    profiles: Profiles                  # 分区曲线（1.10-1.12）
    lines: Lines                        # 省内断面（1.13）
    prov: ProvinceConstraints           # 省级参数（1.15）
    dr: DemandResponse                  # 分区负荷参数 + 需求响应（1.20）
    week_peak: np.ndarray               # 周负荷峰值（1.22，Nprovince×T）


class InputReader:
    """系统公共输入读取器：一个大类一个 load_x 方法，返回节次 dataclass。

    方法对应 MATLAB §1 各节次，跨节依赖（fixedCostScale、areaPos、负荷
    原始表等）经入参显式传递；load() 按依赖顺序执行并组装组合式 Inputs。
    检修计划与周峰值为单值，直接返回不包壳。
    """

    # ---- 1.1 时间轴 ----
    def load_time(self) -> TimeAxis:
        """读时间配置，返回时间轴：步数 T、步长 Delta_t、整点时刻序列
        Time、去重日期表 Day 与天数 Nday。

        Returns:
            TimeAxis: 时间轴（1.1）。
        """
        T = config.T
        Delta_t = float(config.DELTA_T)  # 单位长度，即1小时
        Time = pd.date_range(pd.Timestamp(config.START_TIME), periods=T, freq="h")
        # Day/Nday 原在 1.20 段计算，仅依赖 Time，归入时间轴（数值不变）
        Day = list(dict.fromkeys(Time.normalize()))
        Nday = len(Day)
        return TimeAxis(T=T, Delta_t=Delta_t, Time=Time, Day=Day, Nday=Nday)

    # ---- 1.1 供暖日历（MATLAB 46-61）：Enabled=0 全年非供暖，=1 按起止月日切换（含端点，可跨年）----
    def load_heating_calendar(self, time: TimeAxis) -> HeatingCalendar:
        """读供暖期设置表并按时间轴展开，返回供暖日历：分区清单 ZoneID、
        分区×T 供暖标记 isHeatingPeriod（起始月日晚于结束月日表示跨年，
        取两段并集；Enabled=0 的行全年 False）。

        Args:
            time (TimeAxis): 时间轴。

        Returns:
            HeatingCalendar: 供暖日历。
        """
        hd = _read(config.UNIT_FILE, "供暖期设置",
                   ["ZoneID", "Enabled", "StartMonth", "StartDay", "EndMonth", "EndDay"])
        ZoneID = [_zstr(v) for v in hd["ZoneID"]]
        # MATLAB 50：calendarDay = 100*month+day，逐小时一个整数
        calendarDay = np.array([100 * t.month + t.day for t in time.Time])
        isHeatingPeriod = np.zeros((len(hd), time.T), dtype=bool)   # 行为分区，列为仿真小时
        for z, row in hd.iterrows():
            if int(row["Enabled"]) == 0:
                continue
            firstDay = 100 * int(row["StartMonth"]) + int(row["StartDay"])
            lastDay = 100 * int(row["EndMonth"]) + int(row["EndDay"])
            if firstDay <= lastDay:
                isHeatingPeriod[z, :] = (calendarDay >= firstDay) & (calendarDay <= lastDay)
            else:
                isHeatingPeriod[z, :] = (calendarDay >= firstDay) | (calendarDay <= lastDay)
        return HeatingCalendar(ZoneID=ZoneID, isHeatingPeriod=isHeatingPeriod)

    # ---- 1.2 系统经济参数（MATLAB 66-75）----
    def load_economic_params(self, time: TimeAxis) -> EconomicParams:
        """
        读系统参数表，返回经济参数：货币折算系数 costScale、固定费用
        折算 fixedCostPeriodRatio/fixedCostScale、失负荷与弃电惩罚、
        四项排放价格（CO2/SO2/NOx/烟尘）。

        Args:
            time (TimeAxis): 时间轴（折算本周期小时数用）。

        Returns:
            EconomicParams: 系统经济参数。
        """
        sd = _read(config.SYSTEM_FILE, "系统参数", ["Parameter", "Value"])
        p = dict(zip(sd["Parameter"], sd["Value"]))

        """
        [
            "机组成本统一采用人民币元", "固定运维年度基准，按仿真小时折算",
            "失负荷惩罚", "弃风惩罚", "弃光惩罚", "弃水惩罚",
            "碳价格情景值，非2030预测价格", "SO2排放成本，非海南税率",
            "NOx排放成本，非海南税率", "烟尘排放成本，非海南税率"
        ]
        """
        need = ["costScaleToYuan", "fixedCostReferenceHours", "shedCost", "windCurtCost",
                "pvCurtCost", "hydroCurtCost", "co2Price", "so2Price", "noxPrice", "dustPrice"]
        if miss := [k for k in need if k not in p]:
            raise KeyError(f"系统参数表缺少参数行: {miss}")

        # 系统参数
        costScale = float(p["costScaleToYuan"])

        # 基准期固定费用折算到本次小时数 (MATLAB 73)
        fixedCostPeriodRatio = time.T * time.Delta_t / float(p["fixedCostReferenceHours"])

        # 基准期原货币 → 本周期元 (MATLAB 74)
        fixedCostScale = costScale * fixedCostPeriodRatio
        c_shed = float(p["shedCost"])
        c_wind_curt = float(p["windCurtCost"])
        c_pv_curt = float(p["pvCurtCost"])
        c_hyd_curt = float(p["hydroCurtCost"])
        c_CO2 = float(p["co2Price"])
        c_SO2 = float(p["so2Price"])
        c_NOX = float(p["noxPrice"])
        c_dust = float(p["dustPrice"])
        return EconomicParams(
            costScale=costScale, fixedCostPeriodRatio=fixedCostPeriodRatio,
            fixedCostScale=fixedCostScale,
            c_shed=c_shed, c_wind_curt=c_wind_curt, c_pv_curt=c_pv_curt,
            c_hyd_curt=c_hyd_curt,
            c_CO2=c_CO2, c_SO2=c_SO2, c_NOX=c_NOX, c_dust=c_dust,
        )

    # ---- 1.11 分区表 + 省序（MATLAB 293-315、358-365）----
    def load_areas(self) -> Areas:
        """
        读分区表，返回分区信息：分区编号/名称清单、分区→省序映射
        areaProvince、省清单 ProvinceID（areaPos 供下游分区号定位）。

        Returns:
            Areas: 分区信息。
        """
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
        return Areas(Narea=Narea, AreaID=AreaID, AreaName=AreaName,
                     areaProvince=areaProvince, ProvinceID=ProvinceID,
                     Nprovince=Nprovince, areaPos=areaPos)

    # ---- 检修计划（MATLAB 79-85）：多电源共用一张表，骨架层读取、各电源文件按 Type 领取 ----
    def load_maintenance(self) -> pd.DataFrame:
        """读检修计划总表，返回启用行 DataFrame：各机组的检修起止时间
        （各电源文件按 Type 领取）。

        Returns:
            pd.DataFrame: 检修计划启用行（Type/UnitKey/StartTime/EndTime）。
        """
        # 主要包含：各个机组的检修起始和结束时间节点
        mtn = _read(config.UNIT_FILE, "检修计划",
                    ["Type", "UnitKey", "StartTime", "EndTime", "Enabled"])
        mtn = mtn[mtn["Enabled"] == 1].copy()
        mtn["StartTime"] = _to_hours(mtn["StartTime"])
        mtn["EndTime"] = _to_hours(mtn["EndTime"])
        return mtn

    # ---- 1.8/1.9 风电光伏装机（行空间，MATLAB 254-268）----
    def load_wind_pv_capacity(self, econ: EconomicParams, areas: Areas) -> WindPv:
        """读风电/光伏装机表，返回风光装机：分区归属 area_wind/area_pv、
        装机容量 W_capacity/PV_capacity、折算到本周期的 FOM 成本
        c_wind_FOM/c_pv_FOM。

        Args:
            econ (EconomicParams): 系统经济参数（FOM 折算用）。
            areas (Areas): 分区信息。

        Returns:
            WindPv: 风光装机。
        """
        # 主要包含：风电装机容量、风电周期运维成本、光伏装机容量（MW）、光伏周期运维成本（元）
        req_w = ["分区编号", "分区名称", "装机容量_MW", "FOM_COST_PERIOD"]
        # 新版 1.8：陆上/海上风电分页读取，行空间拼接（MATLAB windData=[on;off]）
        windOnData = _read(config.UNIT_FILE, "陆上风电", req_w)
        windOffData = _read(config.UNIT_FILE, "海上风电", req_w)
        windData = pd.concat([windOnData, windOffData], ignore_index=True)
        zone_wind = [_zstr(v) for v in windData["分区编号"]]
        if miss := [z for z in zone_wind if z not in areas.areaPos]:
            raise KeyError(f"风电分区编号 {set(miss)} 不在分区表中")
        area_wind = np.array([areas.areaPos[z] for z in zone_wind])
        W_capacity = windData["装机容量_MW"].to_numpy(float)

        # FOM_COST_PERIOD (年度功率运维成本)
        # c_wind_FOM (换算为T时刻周期的风电年度运维成本)
        c_wind_FOM = econ.fixedCostScale * windData["FOM_COST_PERIOD"].to_numpy(float)

        # 新版 1.12：分区陆/海装机，同分区多行取和（MATLAB 398-399）
        n_on = len(windOnData)
        W_on_cap_area = np.zeros(areas.Narea)
        np.add.at(W_on_cap_area, area_wind[:n_on], W_capacity[:n_on])
        W_off_cap_area = np.zeros(areas.Narea)
        np.add.at(W_off_cap_area, area_wind[n_on:], W_capacity[n_on:])

        pvData = _read(config.UNIT_FILE, "光伏", req_w)
        zone_pv = [_zstr(v) for v in pvData["分区编号"]]
        if miss := [z for z in zone_pv if z not in areas.areaPos]:
            raise KeyError(f"光伏分区编号 {set(miss)} 不在分区表中")
        area_pv = np.array([areas.areaPos[z] for z in zone_pv])
        PV_capacity = pvData["装机容量_MW"].to_numpy(float)

        # c_pv_FOM (换算为T时刻周期的光伏年度运维成本)
        c_pv_FOM = econ.fixedCostScale * pvData["FOM_COST_PERIOD"].to_numpy(float)
        return WindPv(area_wind=area_wind, W_capacity=W_capacity, c_wind_FOM=c_wind_FOM,
                      area_pv=area_pv, PV_capacity=PV_capacity, c_pv_FOM=c_pv_FOM,
                      W_on_cap_area=W_on_cap_area, W_off_cap_area=W_off_cap_area)

    # ---- 1.10-1.12 负荷/风光曲线 → 分区功率（MATLAB 270-330）----
    def load_profiles(self, time: TimeAxis, areas: Areas, wind_pv: WindPv) -> Profiles:
        """读负荷/风光曲线与归一化基准，返回分区曲线：逐时负荷/风电/光伏
        功率矩阵 P_load/P_wind_available/P_pv_available（loadData/loadTime/
        baseMap 为原始表，供 1.22 周峰值复用）。

        Args:
            time (TimeAxis): 时间轴。
            areas (Areas): 分区信息。
            wind_pv (WindPv): 风光装机（可用出力按装机折算）。

        Returns:
            Profiles: 分区曲线。
        """
        loadData = _read(config.PROFILE_FILE, "负荷", ["时间"] + areas.AreaName)
        loadTime = _to_hours(loadData["时间"])
        loadRows = _match_rows(loadTime, time.Time, "负荷")

        # 新版 1.12：陆上/海上风电曲线分页读取，分别折算分区可用出力
        windOnCurve = _read(config.PROFILE_FILE, "陆上风电", ["时间"] + areas.AreaName)
        windOnRows = _match_rows(_to_hours(windOnCurve["时间"]), time.Time, "陆上风电")
        windOffCurve = _read(config.PROFILE_FILE, "海上风电", ["时间"] + areas.AreaName)
        windOffRows = _match_rows(_to_hours(windOffCurve["时间"]), time.Time, "海上风电")

        pvCurve = _read(config.PROFILE_FILE, "光伏", ["时间"] + areas.AreaName)
        pvRows = _match_rows(_to_hours(pvCurve["时间"]), time.Time, "光伏")

        # 全年最大负荷值-baseMap: {'海南': 14210.0}
        baseData = _read(config.PROFILE_FILE, "归一化基准", ["类型", "分区", "全年最大值(基准)"])
        baseRows = baseData[(baseData["类型"].astype(str) == "负荷")
                            & (baseData["分区"].astype(str).isin(areas.AreaName))]
        baseMap = {r["分区"]: float(r["全年最大值(基准)"]) for _, r in baseRows.iterrows()}
        if miss := [n for n in areas.AreaName if n not in baseMap]:
            raise KeyError(f"归一化基准缺分区行: {miss}")

        # 分区域风电光伏装机容量数组（陆海分列，同分区多行取和；MATLAB 393-410）
        W_on_cap_area = wind_pv.W_on_cap_area
        W_off_cap_area = wind_pv.W_off_cap_area
        PV_cap_area = np.zeros(areas.Narea)
        PV_cap_area[wind_pv.area_pv] = wind_pv.PV_capacity

        # 负荷曲线=负荷逐小时比率*负荷全年最大值（注意分区域）
        # 风电逐小时出力=风电逐小时比率*风电装机容量（注意分区域，陆海分开）
        # 光伏逐小时出力=光伏逐小时比率*光伏装机容量（注意分区域）
        P_load = np.zeros((areas.Narea, time.T))
        P_wind_on_available = np.zeros((areas.Narea, time.T))
        P_wind_off_available = np.zeros((areas.Narea, time.T))
        P_pv_available = np.zeros((areas.Narea, time.T))
        for r in range(areas.Narea):
            name = areas.AreaName[r]
            P_load[r, :] = loadData.loc[loadRows, name].to_numpy(float) * baseMap[name]
            if W_on_cap_area[r] > 0:
                P_wind_on_available[r, :] = windOnCurve.loc[windOnRows, name].to_numpy(float) * W_on_cap_area[r]
            if W_off_cap_area[r] > 0:
                P_wind_off_available[r, :] = windOffCurve.loc[windOffRows, name].to_numpy(float) * W_off_cap_area[r]
            P_pv_available[r, :] = pvCurve.loc[pvRows, name].to_numpy(float) * PV_cap_area[r]

        # 平衡及新能源总量口径取陆海之和（MATLAB 411）
        P_wind_available = P_wind_on_available + P_wind_off_available

        return Profiles(P_load=P_load, P_wind_available=P_wind_available,
                        P_pv_available=P_pv_available,
                        loadData=loadData, loadTime=loadTime, baseMap=baseMap,
                        P_wind_on_available=P_wind_on_available,
                        P_wind_off_available=P_wind_off_available)

    # ---- 1.13 省内断面（MATLAB 332-342）----
    def load_lines(self, areas: Areas) -> Lines:
        """读省内断面表，返回断面信息：启用条数 N_line、端点分区序
        line_from/line_to、送/受电功率上限 P_line_forward/P_line_reverse。

        Args:
            areas (Areas): 分区信息。

        Returns:
            Lines: 省内断面。
        """
        # 即送受电：送电功率上限 (ForwardMW)，受电功率上限 (ReverseMW)
        lineData = _read(config.SYSTEM_FILE, "省内断面",
                         ["FromArea", "ToArea", "ForwardMW", "ReverseMW", "Enabled"])
        lineData = lineData[lineData["Enabled"] == 1]
        fl = [_zstr(v) for v in lineData["FromArea"]]
        tl = [_zstr(v) for v in lineData["ToArea"]]
        if miss := [z for z in fl + tl if z not in areas.areaPos]:
            raise KeyError(f"省内断面分区编号 {set(miss)} 不在分区表中")
        N_line = len(lineData)

        # dtype=int：空断面（N_line=0）时 np.array([]) 默认 float，下游花式索引会拒收
        line_from = np.array([areas.areaPos[z] for z in fl], dtype=int)
        line_to = np.array([areas.areaPos[z] for z in tl], dtype=int)
        P_line_forward = lineData["ForwardMW"].to_numpy(float)
        P_line_reverse = lineData["ReverseMW"].to_numpy(float)
        return Lines(N_line=N_line, line_from=line_from, line_to=line_to,
                     P_line_forward=P_line_forward, P_line_reverse=P_line_reverse)

    # ---- 1.15 省级约束（MATLAB 358-381）----
    def load_province_constraints(self, areas: Areas) -> ProvinceConstraints:
        """读省级约束表，返回省级参数：充裕度 adequacyFactor、冷/热备用
        比例、新能源利用率下限、碳预算 CO2Limit、备用持续时间
        reserveDuration（均按省序对齐）。

        Args:
            areas (Areas): 分区信息（省清单与省序对齐用）。

        Returns:
            ProvinceConstraints: 省级参数。
        """
        """
        AdequacyFactor: 容量充裕度系数（系统在第 w 周的最小备用容量需求公式为 (AdequacyFactor - 1) × 该周系统最大负荷，各调度时段的系统总备用容量必须大于等于该下限）
        ColdReserveRatio：冷备用比例（冷备用由停运且具备启动条件的煤电和气电提供，冷备用至少达到该周最大负荷的特定比例）
        HotReserveRatio：热备用比例（热备用由在线的煤电、气电、水电、抽蓄、储能提供，热备用至少达到该周最大负荷的特定比例）
        RenewableUtilizationMin：新能源利用率下限（限制系统的新能源弃电水平，保障新能源消纳目标的实现）
        CO2Limit_t：碳预算上限（汇总煤电机组（根据出力水平分段计算）和燃气机组在整个调度周期内的总二氧化碳排放量，要求该总量不得超过设定的最大CO₂排放总量上限）
        ReserveDuration_h：备用持续时间（当储能或抽水蓄能系统向电网提供热备用功率时，必须确保其当前剩余的储能量（或上水库可用水量）足以支撑其以该备用功率连续放电长达 ReserveDuration_h 小时，防止因电量耗尽而导致备用失效）
        """
        provData = _read(config.SYSTEM_FILE, "省级约束",
                         ["ProvinceID", "AdequacyFactor", "ColdReserveRatio", "HotReserveRatio",
                          "RenewableUtilizationMin", "CO2Limit_t", "ReserveDuration_h"])
        provData = provData[provData["ProvinceID"].astype(str).isin(areas.ProvinceID)]
        provData = provData.set_index("ProvinceID").loc[areas.ProvinceID]
        if provData.isna().any().any():
            raise KeyError(f"省级约束缺值:\n{provData[provData.isna().any(axis=1)]}")
        return ProvinceConstraints(
            adequacyFactor=provData["AdequacyFactor"].to_numpy(float),
            coldReserveRatio=provData["ColdReserveRatio"].to_numpy(float),
            hotReserveRatio=provData["HotReserveRatio"].to_numpy(float),
            renewableUtilizationMin=provData["RenewableUtilizationMin"].to_numpy(float),
            CO2Limit=provData["CO2Limit_t"].to_numpy(float),
            reserveDuration=provData["ReserveDuration_h"].to_numpy(float),
        )

    # ---- 1.20 分区负荷参数 + 需求响应（MATLAB 488-530）----
    def load_demand_response(self, time: TimeAxis, areas: Areas) -> DemandResponse:
        """读分区负荷参数与需求响应时序/日限额表，返回需求响应：失负荷/
        转移/削减参数（比例、窗口、成本）、逐时限额矩阵 P_shift_out_max/
        P_shift_in_max/P_cut_max、逐日削减电量上限 E_cut_day_max 与同日
        转移边端点 shiftFrom/shiftTo。

        Args:
            time (TimeAxis): 时间轴。
            areas (Areas): 分区信息。

        Returns:
            DemandResponse: 分区负荷参数 + 需求响应。
        """
        """
        ShedMaxRatio：失负荷比例（当系统遇到极端情况，怎么凑也发不出足够的电时，最多允许停掉百分之几的用电需求）
        ShiftWindow_h：可转移负荷允许的最大时间转移范围（规定了最多能让人提前或推迟几个小时用电）
        ShiftCost：可转移负荷单位调用成本（计入系统总运行成本中的需求响应调用成本，用户的总用电量一点没少，只是配合电网换到了非高峰时间去用，电网每让人挪动1度电，需要补偿给用户的钱）
        CutCost：可削减负荷单位调用成本（计入系统总运行成本中的需求响应调用成本，电网每让人削减1度电，需要付给用户的补偿钱数）
        DRFixedCost：需求响应固定运维成本（表示可转移负荷和可削减负荷资源的固定运行维护成本）
        AnnualCutEnergy_MWh：全年最大允许削减电量（该参数用于表征分区需求响应资源的年度可调用规模，避免优化模型为降低运行成本而在全年长期、大规模地削减用户用电需求）
        """
        lp = _read(config.SYSTEM_FILE, "分区负荷参数",
                   ["AreaID", "ShedMaxRatio", "ShiftWindow_h", "ShiftCost", "CutCost",
                    "DRFixedCost", "AnnualCutEnergy_MWh"])
        lp = lp[lp["AreaID"].map(_zstr).isin(areas.AreaID)]
        lp = lp.set_index(lp["AreaID"].map(_zstr)).loc[areas.AreaID]
        ShedMaxRatio = lp["ShedMaxRatio"].to_numpy(float)
        ShiftWindow_h = lp["ShiftWindow_h"].to_numpy(float)
        ShiftCost = lp["ShiftCost"].to_numpy(float)
        CutCost = lp["CutCost"].to_numpy(float)
        DRFixedCost = lp["DRFixedCost"].to_numpy(float)
        AnnualCutEnergy_MWh = lp["AnnualCutEnergy_MWh"].to_numpy(float)

        """
        ShiftOutMax_MW：时段最大允许转出负荷功率（单个小时最多能劝走多少电）
        ShiftInMax_MW：时段最大允许接收转入负荷功率（单个小时最多能额外塞进多少电）
        CutMax_MW（P_cut_max）：时段最大可削减负荷功率（单个小时最多能砍掉多少电）
        CutEnergyMax_MWh（drows）：单日允许的最大削减电量（单日最多能砍掉多少电，和CutMax_MW配合使用）
        """
        drTime = _read(config.SYSTEM_FILE, "需求响应时序",
                       ["Time", "AreaID", "ShiftOutMax_MW", "ShiftInMax_MW", "CutMax_MW"])
        drDaily = _read(config.SYSTEM_FILE, "需求响应日限额", ["Date", "AreaID", "CutEnergyMax_MWh"])
        P_shift_out_max = np.zeros((areas.Narea, time.T))
        P_shift_in_max = np.zeros((areas.Narea, time.T))
        P_cut_max = np.zeros((areas.Narea, time.T))
        E_cut_day_max = np.zeros((areas.Narea, time.Nday))
        shiftFrom = []
        shiftTo = []
        for r in range(areas.Narea):
            aid = areas.AreaID[r]
            rows = drTime[drTime["AreaID"].map(_zstr) == aid]
            rows = rows.set_index(_to_hours(rows["Time"]))
            try:
                sel = rows.loc[time.Time]
            except KeyError as e:
                raise KeyError(f"需求响应时序缺分区 {aid} 的时刻 {e}") from None
            P_shift_out_max[r, :] = sel["ShiftOutMax_MW"].to_numpy(float)
            P_shift_in_max[r, :] = sel["ShiftInMax_MW"].to_numpy(float)
            P_cut_max[r, :] = sel["CutMax_MW"].to_numpy(float)
            drows = drDaily[drDaily["AreaID"].map(_zstr) == aid]
            drows = drows.set_index(drows["Date"].dt.normalize())
            try:
                dsel = drows.loc[time.Day]
            except KeyError as e:
                raise KeyError(f"需求响应日限额缺分区 {aid} 的日期 {e}") from None
            E_cut_day_max[r, :] = dsel["CutEnergyMax_MWh"].to_numpy(float)
            # 同日、非自身、时间窗内的有向转移边（MATLAB 517-529；避免稠密变量）
            area_from = []
            area_to = []
            for d in range(time.Nday):
                dayHours = np.where(time.Time.normalize() == time.Day[d])[0]
                o, j = np.meshgrid(dayHours, dayHours, indexing="ij")
                ok = (o != j) & (np.abs(o - j) * time.Delta_t <= ShiftWindow_h[r])
                area_from.append(o[ok])
                area_to.append(j[ok])
            shiftFrom.append(np.concatenate(area_from) if area_from else np.array([], int))
            shiftTo.append(np.concatenate(area_to) if area_to else np.array([], int))

        return DemandResponse(
            ShedMaxRatio=ShedMaxRatio, ShiftWindow_h=ShiftWindow_h,
            ShiftCost=ShiftCost, CutCost=CutCost,
            DRFixedCost=DRFixedCost, AnnualCutEnergy_MWh=AnnualCutEnergy_MWh,
            P_shift_out_max=P_shift_out_max, P_shift_in_max=P_shift_in_max,
            P_cut_max=P_cut_max,
            E_cut_day_max=E_cut_day_max, shiftFrom=shiftFrom, shiftTo=shiftTo,
        )

    # ---- 1.22 每周负荷峰值（MATLAB 551-565；窗口按数据实际行截取）----
    def load_week_peak(self, time: TimeAxis, areas: Areas, profiles: Profiles) -> np.ndarray:
        """按省聚合分区负荷并逐周取峰值，返回周负荷峰值矩阵 P_week_peak
        （Nprovince×T，每省每周一个峰值）。

        Args:
            time (TimeAxis): 时间轴。
            areas (Areas): 分区信息。
            profiles (Profiles): 分区曲线（负荷原始表复用）。

        Returns:
            np.ndarray: 周负荷峰值（Nprovince×T，MW）。
        """
        weekStart = time.Time.normalize() - pd.to_timedelta(time.Time.dayofweek, unit="D")  # 周一起始
        Week = list(dict.fromkeys(weekStart))
        P_week_peak = np.zeros((areas.Nprovince, time.T))
        for p_idx in range(areas.Nprovince):
            provinceLoad = np.zeros(len(profiles.loadData))
            for r in np.where(areas.areaProvince == p_idx)[0]:
                provinceLoad += profiles.loadData[areas.AreaName[r]].to_numpy(float) * profiles.baseMap[areas.AreaName[r]]
            for wk in Week:
                inWindow = (profiles.loadTime.to_numpy() >= np.datetime64(wk)) & \
                           (profiles.loadTime.to_numpy() < np.datetime64(wk + pd.Timedelta(days=7)))
                peak = provinceLoad[inWindow].max() if inWindow.any() else float("nan")
                if not np.isfinite(peak):
                    raise KeyError(f"周 {wk} 落在负荷数据范围外，无峰值（不造数）")
                P_week_peak[p_idx, weekStart == wk] = peak
        return P_week_peak

    def load(self) -> Inputs:
        """按依赖顺序执行各 load_x 方法，数值安全检查后组装组合式 Inputs。

        Returns:
            Inputs: 系统公共输入组合体。
        """
        time = self.load_time()                                # 时间轴
        heating = self.load_heating_calendar(time)             # 供暖日历
        econ = self.load_economic_params(time)                 # 系统经济参数
        areas = self.load_areas()                              # 分区
        maintenance = self.load_maintenance()                 # 检修计划
        wind_pv = self.load_wind_pv_capacity(econ, areas)      # 风光装机
        profiles = self.load_profiles(time, areas, wind_pv)    # 分区曲线
        lines = self.load_lines(areas)                         # 省内断面
        prov = self.load_province_constraints(areas)           # 省级约束
        dr = self.load_demand_response(time, areas)            # 需求响应
        week_peak = self.load_week_peak(time, areas, profiles)  # 周负荷峰值

        # ---- 数值安全检查：禁止裸 NaN 进模型（code_basic_rule §6）----
        for name, arr in [("P_load", profiles.P_load), ("P_wind_available", profiles.P_wind_available),
                          ("P_pv_available", profiles.P_pv_available),
                          ("P_shift_out_max", dr.P_shift_out_max), ("P_shift_in_max", dr.P_shift_in_max),
                          ("P_cut_max", dr.P_cut_max), ("E_cut_day_max", dr.E_cut_day_max),
                          ("P_week_peak", week_peak)]:
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"{name} 含 NaN/Inf，检查上游数据")

        return Inputs(time=time, heating=heating, econ=econ, areas=areas, maintenance=maintenance,
                      wind_pv=wind_pv, profiles=profiles, lines=lines,
                      prov=prov, dr=dr, week_peak=week_peak)


def load_inputs() -> Inputs:
    """模块级入口：构造 InputReader 并执行完整读取，返回 Inputs。

    Returns:
        Inputs: 系统公共输入组合体。
    """
    return InputReader().load()

def _flatten(inp: Inputs) -> dict:
    """组合式 Inputs → 扁平字段字典（节次字段名与旧版 Inputs 一致）。

    Args:
        inp (Inputs): 系统公共输入组合体。

    Returns:
        dict: 扁平字段字典（键=叶级字段名）。
    """
    flat = {}
    for name in ("time", "heating", "econ", "areas", "wind_pv", "profiles", "lines", "prov", "dr"):
        section = getattr(inp, name)
        for f in fields(section):
            flat[f.name] = getattr(section, f.name)
    flat["maintenance"] = inp.maintenance
    flat["P_week_peak"] = inp.week_peak
    return flat

if __name__ == "__main__":
    import numpy as np
    new_flat = _flatten(load_inputs())
    print(new_flat)