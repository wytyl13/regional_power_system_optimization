#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File    : convert.py
模版解析结果 + 生效配置 → 三份中间表（分区机组信息表 / 算例系统参数 / 运行数据）。
reader.py 及以下（model/solve）不改，本文件是它们唯一的上游。

全部换算与派生规则均已对海南2030算例人工三份表数值验证（service/ground_truth.txt）。
取值优先级：用户 config 显式键 > Excel 收资值 > defaults.DEFAULT_CONFIG。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from defaults import ENERGY_DIVISOR, merge_config

WAN = 10  # 万kW → MW

AREA_COL = "SUBSTR(C.ID，12)"   # 历史表头，保持原样（loader 按此名读列）

# ---- 各 sheet 精确表头（列名与列序 = 人工三份表，逐列对齐）----
COAL_COLS = ["UnitKey", "ID", "NAME", AREA_COL, "PLANT_ID", "CAP", "MIN_POWER_GEN",
             "MIN_DOWN_TIME", "MIN_UP_TIME", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
             "COST_A", "COST_B", "COST_C", "COST_START_UP", "COST_SHUT_DOWN",
             "MIN_POWER_CHARGE", "MAX_POWER_CHARGE", "GEN_AFFI_FACTOR", "CHAEGE_AFFI_FACTOR",
             "INIT_STATUS", "INIT_POWER", "FOM_COST_PERIOD", "INIT_HOURS",
             "START_RAMP", "STOP_RAMP", "SO2_FACTOR", "NOX_FACTOR", "DUST_FACTOR",
             "DataStatus", "SOURCE_COMMISSION", "SOURCE_STATUS",
             "DERATE_RATE", "SOURCE_MIN_RATIO", "SOURCE_DERATE",
             "MIN_RATIO_HEATING", "MIN_RATIO_NONHEATING"]
GAS_COLS = ["UnitKey", "ID", "NAME", AREA_COL, "PLANT_ID", "CAP", "MIN_POWER_GEN",
            "MIN_DOWN_TIME", "MIN_UP_TIME", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
            "COST_A", "COST_B", "COST_C", "COST_START_UP", "COST_SHUT_DOWN",
            "MIN_POWER_CHARGE", "MAX_POWER_CHARGE", "GEN_AFFI_FACTOR", "CHAEGE_AFFI_FACTOR",
            "INIT_STATUS", "INIT_POWER", "FOM_COST_PERIOD",
            "START_RAMP", "STOP_RAMP", "CO2_FACTOR", "GEN_COST",
            "DataStatus", "INIT_HOURS", "SOURCE_COMMISSION", "SOURCE_STATUS",
            "DERATE_RATE", "SOURCE_MIN_RATIO", "SOURCE_DERATE",
            "MIN_RATIO_HEATING", "MIN_RATIO_NONHEATING"]
NUC_COLS = ["UnitKey", "ID", "NAME", AREA_COL, "PLANT_ID", "CAP", "MIN_POWER_GEN",
            "MIN_DOWN_TIME", "MIN_UP_TIME", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
            "COST_A", "COST_B", "COST_C", "COST_START_UP", "COST_SHUT_DOWN",
            "MIN_POWER_CHARGE", "MAX_POWER_CHARGE", "GEN_AFFI_FACTOR", "CHAEGE_AFFI_FACTOR",
            "INIT_POWER", "FOM_COST_PERIOD", "INIT_MAINTENANCE", "GEN_COST",
            "DataStatus", "SOURCE_COMMISSION", "SOURCE_STATUS"]
BIO_COLS = ["UnitKey", "ID", "NAME", AREA_COL, "PLANT_ID", "CAP", "MIN_POWER_GEN",
            "MIN_DOWN_TIME", "MIN_UP_TIME", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
            "COST_A", "COST_B", "COST_C", "COST_START_UP", "COST_SHUT_DOWN",
            "MIN_POWER_CHARGE", "MAX_POWER_CHARGE", "GEN_AFFI_FACTOR", "CHAEGE_AFFI_FACTOR",
            "INIT_POWER", "FOM_COST_PERIOD", "INIT_MAINTENANCE",
            "START_RAMP", "STOP_RAMP", "GEN_COST",
            "DataStatus", "ENERGY_LIMIT_MWH", "SOURCE_COMMISSION", "SOURCE_STATUS",
            "DERATE_RATE", "SOURCE_MIN_RATIO", "SOURCE_DERATE",
            "MIN_RATIO_HEATING", "MIN_RATIO_NONHEATING"]
PS_COLS = ["UnitKey", "ID", "NAME", AREA_COL, "PLANT_ID", "CAP", "MIN_POWER_GEN",
           "MIN_DOWN_TIME", "MIN_UP_TIME", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
           "COST_A", "COST_B", "COST_C", "COST_START_UP", "COST_SHUT_DOWN",
           "MIN_POWER_CHARGE", "MAX_POWER_CHARGE", "GEN_AFFI_FACTOR", "CHAEGE_AFFI_FACTOR",
           "PUMP_MIN_MW", "ENERGY_MWH", "SOC_MIN", "SOC_MAX", "SOC_INITIAL",
           "FOM_COST_PERIOD", "DataStatus", "SOURCE_COMMISSION", "SOURCE_STATUS"]
ES_COLS = ["UnitKey", "NAME", "AreaID", "CAP", "ENERGY_MWH", "SOC_MIN", "SOC_MAX",
           "SOC_INITIAL", "ETA_CHARGE", "ETA_DISCHARGE", "SELF_DISCHARGE",
           "POWER_COST", "ENERGY_COST", "REPLACE_FACTOR", "MAX_CYCLES",
           "FOM_COST_PERIOD", "FOM_ENERGY_PERIOD"]
FR_COLS = ["UnitKey", "NAME", "AreaID", "WIND_CAP", "PV_CAP", "ES_POWER", "ENERGY_MWH",
           "SOC_MIN", "SOC_MAX", "SOC_INITIAL", "ETA_CHARGE", "ETA_DISCHARGE",
           "FOM_COST_PERIOD", "SELF_DISCHARGE", "POWER_COST", "ENERGY_COST",
           "REPLACE_FACTOR", "MAX_CYCLES", "FOM_ENERGY_PERIOD", "WIND_FOM_PERIOD", "PV_FOM_PERIOD"]
HYD_COLS = ["UnitKey", "NAME", "AreaID", "CAP", "GEN_COST", "FOM_COST_PERIOD", "SOURCE_REGULATION_FACTOR"]
ROR_COLS = ["UnitKey", "NAME", "AreaID", "CAP", "GEN_COST", "FOM_COST_PERIOD"]
CSP_COLS = ["UnitKey", "NAME", "AreaID", "CAP", "MIN_POWER_GEN", "SF_EFFICIENCY",
            "MIRROR_AREA", "SOLAR_MULTIPLE", "DNI_REFERENCE", "THERMAL_EFFICIENCY",
            "HEAT_ETA_IN", "HEAT_ETA_OUT", "HEAT_LOSS", "HEAT_CH_MAX", "HEAT_DIS_MAX",
            "HEAT_MIN", "HEAT_MAX", "HEAT_INITIAL", "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN",
            "START_RAMP", "STOP_RAMP", "GEN_COST", "FOM_COST_PERIOD", "INIT_STATUS", "INIT_POWER"]
RENEW_COLS = ["UnitKey", "分区编号", "分区名称", "装机容量_MW", "FOM_COST_PERIOD", "DataStatus"]
MTN_COLS = ["Year", "Type", "UnitKey", "UnitName", "AreaID", "StartTime", "EndTime", "Enabled", "Note"]

SYS_COLS = ["Parameter", "Value", "Unit", "Description", "Status", "ModelScope"]
PROV_COLS = ["ProvinceID", "AdequacyFactor", "ColdReserveRatio", "RenewableUtilizationMin",
             "CO2Limit_t", "ReserveDuration_h", "Status", "HotReserveRatio"]
LOADP_COLS = ["AreaID", "AreaName", "ShedMaxRatio", "ShiftWindow_h", "ShiftCost", "CutCost",
              "DRFixedCost", "AnnualCutEnergy_MWh", "Status"]
DR_COLS = ["Time", "AreaID", "ShiftOutMax_MW", "ShiftInMax_MW", "CutMax_MW"]
DRD_COLS = ["Date", "AreaID", "CutEnergyMax_MWh"]
HYDM_COLS = ["UnitKey", "Hydrology", "Month", "ForcedMW", "ExpectedMW", "AverageMW"]
FRT_COLS = ["Time", "UnitKey", "WindAvailable_MW", "PVAvailable_MW"]
EMIS_COLS = ["UnitKey", "NodeIndex", "CO2Rate_t_h"]
LINE_COLS = ["LineID", "FromArea", "ToArea", "ForwardMW", "ReverseMW", "Enabled", "Note"]
TIE_COLS = ["LineID", "LocalArea", "OtherProvince", "ForwardMW", "ReverseMW",
            "InEnergyMax_MWh", "OutEnergyMax_MWh", "PenaltyCost", "Enabled", "FromArea", "ToArea"]
CSP_T_COLS = ["Time", "UnitKey", "DNI_W_m2"]


def _pct(x: float) -> str:
    v = x * 100
    return f"{int(v)}%" if v == int(v) else f"{v:g}%"


def _pick(v, default):
    """None（前端显式传 null）视为未提供，回落收资值；0 是合法值，不能用 or。"""
    return default if v is None else v


def _num(x: float) -> str:
    return str(int(x)) if float(x) == int(x) else f"{x:g}"


def _energy_mwh(cap_mw: float, hours: float, efficiency: float) -> float:
    """储能量 = 功率 × 时长 × sqrt(转换效率) / 0.8（三处已数值验证）"""
    return cap_mw * hours * math.sqrt(efficiency) / ENERGY_DIVISOR


def _iso_date(v) -> str:
    """投产时间 → 'YYYY-MM-DD' 文本（对齐基准三份表的存法）。"""
    return str(pd.Timestamp(v).date())


def _included(row: dict, year: int) -> tuple[bool, str]:
    """目标年在场筛选：容量>0、投产≤年末、退役（若有）晚于年末。"""
    cap = row.get("装机容量")
    if cap is None or float(cap) <= 0:
        return False, "装机容量为 0"
    comm = row.get("投产时间")
    if comm is not None and pd.Timestamp(comm) > pd.Timestamp(year, 12, 31):
        return False, f"投产时间 {pd.Timestamp(comm).date()} 晚于目标年"
    ret = row.get("退役时间")
    if ret is not None and pd.Timestamp(ret) <= pd.Timestamp(year, 12, 31):
        return False, f"退役时间 {pd.Timestamp(ret).date()} 不晚于目标年"
    return True, ""


def _week_dates(weeks: str, year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """非运行周 '40-43' → [首周周一, 末周次周一) ，年初年末截断。
    周锚点：包含 1 月 1 日的那一周的周一为第 1 周（2030 即 2029-12-31，全年 53 周）。"""
    s = str(weeks).strip()
    a, b = (int(x) for x in s.split("-")) if "-" in s else (int(s), int(s))
    jan1 = pd.Timestamp(year, 1, 1)
    week1 = jan1 - pd.Timedelta(days=jan1.dayofweek)
    start = max(week1 + pd.Timedelta(weeks=a - 1), jan1)
    end = min(week1 + pd.Timedelta(weeks=b), pd.Timestamp(year + 1, 1, 1))
    return start, end


def convert(parsed: dict, user_config: dict | None, out_dir) -> dict:
    """生成三份中间表到 out_dir，返回摘要（含未纳入机组清单）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = merge_config(user_config)
    year = parsed["year"]
    area = parsed["area"]
    prov = cfg["province"]["provinceId"]
    baseLoadMW = parsed["maxLoadWan"] * WAN
    dataStatus = f"容量/投产/检修取自{area}源表；技术缺项为算例假设，见参数说明"
    renewStatus = "源表对应设备容量；友好电站单列，未从本表扣减"

    # 收资值优先于内置默认、用户键优先于收资值
    user = user_config or {}
    dr_u = user.get("demandResponse", {})
    shiftRatio = _pick(dr_u.get("shiftRatio"), parsed["drShiftRatio"])
    cutRatio = _pick(dr_u.get("cutRatio"), parsed["drCutRatio"])
    prov_u = user.get("province", {})
    hotRatio = _pick(prov_u.get("hotReserveRatio"), parsed["hotReserveRatio"])
    coldRatio = _pick(prov_u.get("coldReserveRatio"), parsed["coldReserveRatio"])

    u_cfg, e_cfg = cfg["units"], cfg["emission"]
    excluded: list[dict] = []   # 未纳入机组（记录原因，供 warnings）

    # ============ 机组表 ============
    def unit_rows(rows, type_code, type_name, name_fn, build_fn, extra_keys=()):
        """编号覆盖全部模版行（含剔除），返回 (行列表, {模版行 → UnitKey})。"""
        out, keyOf = [], {}
        for i, r in enumerate(rows, 1):
            key = f"{prov}_{type_code}_{i:03d}"
            ok, why = _included(r, year)
            if not ok:
                excluded.append({"设备": type_name, "UnitKey": key, "机组名称": name_fn(r),
                                 "容量_MW": float(r["装机容量"]) * WAN if r.get("装机容量") else 0,
                                 "未纳入原因": why})
                continue
            keyOf[id(r)] = key
            out.append(build_fn(key, r))
        return out, keyOf

    def _name_with_no(r):
        return f"{r['电站名称']} {r['机组编号']}"

    # ---- 煤电 ----
    c = u_cfg["coal"]

    def build_coal(key, r):
        cap = r["装机容量"] * WAN
        pmin = cap * r["非供暖期最小技术出力率"]
        return {"UnitKey": key, "ID": key, "NAME": _name_with_no(r), AREA_COL: 1,
                "PLANT_ID": r["电站名称"], "CAP": cap, "MIN_POWER_GEN": pmin,
                "MIN_DOWN_TIME": c["minUpDown_h"], "MIN_UP_TIME": c["minUpDown_h"],
                "RAMP_LIMIT_UP": c["rampRatio"] * cap, "RAMP_LIMIT_DOWN": c["rampRatio"] * cap,
                "COST_A": c["costA"], "COST_B": c["costB"], "COST_C": c["costCPerMW"] * cap,
                "COST_START_UP": c["startUpPerMW"] * cap, "COST_SHUT_DOWN": c["shutDownPerMW"] * cap,
                "MIN_POWER_CHARGE": 0, "MAX_POWER_CHARGE": 0,
                "GEN_AFFI_FACTOR": 1, "CHAEGE_AFFI_FACTOR": 1,
                "INIT_STATUS": int(bool(c["initialOn"])), "INIT_POWER": pmin,
                "FOM_COST_PERIOD": c["fom"], "INIT_HOURS": c["initialHours"],
                "START_RAMP": pmin, "STOP_RAMP": pmin,
                "SO2_FACTOR": e_cfg["so2Factor"], "NOX_FACTOR": e_cfg["noxFactor"],
                "DUST_FACTOR": e_cfg["dustFactor"],
                "DataStatus": dataStatus, "SOURCE_COMMISSION": _iso_date(r["投产时间"]),
                "SOURCE_STATUS": r["状态"],
                "DERATE_RATE": r["非供暖期受阻率"],
                "SOURCE_MIN_RATIO": r["最小技术出力率"], "SOURCE_DERATE": r["受阻率"],
                "MIN_RATIO_HEATING": r["供暖期最小技术出力率"],
                "MIN_RATIO_NONHEATING": r["非供暖期最小技术出力率"]}

    coal_rows, coal_keys = unit_rows(parsed["coal"], "TH", "煤电", _name_with_no, build_coal)
    coal_df = pd.DataFrame(coal_rows, columns=COAL_COLS)

    # ---- 煤电碳排节点：P_i = Pmin + i×(Pmax−Pmin)/5，Pmax = CAP×(1−受阻率) ----
    emis = []
    for u in coal_rows:
        eff = (e_cfg["coalEfficiencyLarge"] if u["CAP"] >= e_cfg["largeCapThresholdMW"]
               else e_cfg["coalEfficiencySmall"])
        pmin, pmax = u["MIN_POWER_GEN"], u["CAP"] * (1 - u["DERATE_RATE"])
        for i in range(6):
            p = pmin + i * (pmax - pmin) / 5
            rate = (e_cfg["coalCO2_kgPerGJ"] * 3.6 / eff / 1000
                    * p * (1 + e_cfg["partialLoadFactor"] * (1 - p / u["CAP"])))
            emis.append({"UnitKey": u["UnitKey"], "NodeIndex": i, "CO2Rate_t_h": rate})

    # ---- 气电 ----
    g = u_cfg["gas"]

    def build_gas(key, r):
        cap = r["装机容量"] * WAN
        pmin = cap * r["非供暖期最小技术出力率"]
        return {"UnitKey": key, "ID": key, "NAME": _name_with_no(r), AREA_COL: 1,
                "PLANT_ID": r["电站名称"], "CAP": cap, "MIN_POWER_GEN": pmin,
                "MIN_DOWN_TIME": g["minUpDown_h"], "MIN_UP_TIME": g["minUpDown_h"],
                "RAMP_LIMIT_UP": g["rampRatio"] * cap, "RAMP_LIMIT_DOWN": g["rampRatio"] * cap,
                "COST_A": 0, "COST_B": 0, "COST_C": 0,
                "COST_START_UP": g["startUpPerMW"] * cap, "COST_SHUT_DOWN": g["shutDownPerMW"] * cap,
                "MIN_POWER_CHARGE": 0, "MAX_POWER_CHARGE": 0,
                "GEN_AFFI_FACTOR": 1, "CHAEGE_AFFI_FACTOR": 1,
                "INIT_STATUS": int(bool(g["initialOn"])), "INIT_POWER": 0,
                "FOM_COST_PERIOD": g["fom"],
                "START_RAMP": g["startStopRampRatio"] * cap, "STOP_RAMP": g["startStopRampRatio"] * cap,
                "CO2_FACTOR": g["co2Factor"], "GEN_COST": g["genCost"],
                "DataStatus": dataStatus, "INIT_HOURS": g["initialHours"],
                "SOURCE_COMMISSION": _iso_date(r["投产时间"]), "SOURCE_STATUS": r["状态"],
                "DERATE_RATE": r["非供暖期受阻率"],
                "SOURCE_MIN_RATIO": r["最小技术出力率"], "SOURCE_DERATE": r["受阻率"],
                "MIN_RATIO_HEATING": r["供暖期最小技术出力率"],
                "MIN_RATIO_NONHEATING": r["非供暖期最小技术出力率"]}

    gas_rows, gas_keys = unit_rows(parsed["gas"], "GAS", "气电", _name_with_no, build_gas)

    # ---- 核电（收资无最小出力列，按基荷处理）----
    n = u_cfg["nuclear"]

    def build_nuc(key, r):
        cap = r["装机容量"] * WAN
        return {"UnitKey": key, "ID": key, "NAME": r["电站名称"], AREA_COL: 1,
                "PLANT_ID": r["电站名称"], "CAP": cap, "MIN_POWER_GEN": cap,
                "MIN_DOWN_TIME": 0, "MIN_UP_TIME": 0,
                "RAMP_LIMIT_UP": n["rampRatio"] * cap, "RAMP_LIMIT_DOWN": n["rampRatio"] * cap,
                "COST_A": 0, "COST_B": 0, "COST_C": 0, "COST_START_UP": 0, "COST_SHUT_DOWN": 0,
                "MIN_POWER_CHARGE": 0, "MAX_POWER_CHARGE": 0,
                "GEN_AFFI_FACTOR": 1, "CHAEGE_AFFI_FACTOR": 1,
                "INIT_POWER": cap, "FOM_COST_PERIOD": n["fom"], "INIT_MAINTENANCE": 0,
                "GEN_COST": n["genCost"],
                "DataStatus": dataStatus, "SOURCE_COMMISSION": _iso_date(r["投产时间"]),
                "SOURCE_STATUS": r["状态"]}

    nuc_rows, nuc_keys = unit_rows(parsed["nuclear"], "NUC", "核电", lambda r: r["电站名称"], build_nuc)

    # ---- 生物质 ----
    b = u_cfg["biomass"]

    def build_bio(key, r):
        cap = r["装机容量"] * WAN
        pmin = cap * r["非供暖期最小技术出力率"]
        derate = r["非供暖期受阻率"]
        return {"UnitKey": key, "ID": key, "NAME": _name_with_no(r), AREA_COL: 1,
                "PLANT_ID": r["电站名称"], "CAP": cap, "MIN_POWER_GEN": pmin,
                "MIN_DOWN_TIME": 0, "MIN_UP_TIME": 0,
                "RAMP_LIMIT_UP": b["rampRatio"] * cap, "RAMP_LIMIT_DOWN": b["rampRatio"] * cap,
                "COST_A": 0, "COST_B": 0, "COST_C": 0, "COST_START_UP": 0, "COST_SHUT_DOWN": 0,
                "MIN_POWER_CHARGE": 0, "MAX_POWER_CHARGE": 0,
                "GEN_AFFI_FACTOR": 1, "CHAEGE_AFFI_FACTOR": 1,
                "INIT_POWER": pmin, "FOM_COST_PERIOD": b["fom"], "INIT_MAINTENANCE": 0,
                "START_RAMP": cap, "STOP_RAMP": cap, "GEN_COST": b["genCost"],
                "DataStatus": dataStatus,
                "ENERGY_LIMIT_MWH": cap * (1 - derate) * b["annualHours"],
                "SOURCE_COMMISSION": _iso_date(r["投产时间"]), "SOURCE_STATUS": r["状态"],
                "DERATE_RATE": derate,
                "SOURCE_MIN_RATIO": r["最小技术出力率"], "SOURCE_DERATE": r["受阻率"],
                "MIN_RATIO_HEATING": r["供暖期最小技术出力率"],
                "MIN_RATIO_NONHEATING": r["非供暖期最小技术出力率"]}

    bio_rows, bio_keys = unit_rows(parsed["biomass"], "BIO", "生物质", _name_with_no, build_bio)

    # ---- 抽水蓄能 ----
    p = u_cfg["pumpedStorage"]

    def build_ps(key, r):
        cap = r["装机容量"] * WAN
        affi = math.sqrt(r["转换效率"])
        return {"UnitKey": key, "ID": key, "NAME": _name_with_no(r), AREA_COL: 1,
                "PLANT_ID": r["电站名称"], "CAP": cap, "MIN_POWER_GEN": 0,
                "MIN_DOWN_TIME": 0, "MIN_UP_TIME": 0,
                "RAMP_LIMIT_UP": cap, "RAMP_LIMIT_DOWN": cap,
                "COST_A": 0, "COST_B": 0, "COST_C": 0, "COST_START_UP": 0, "COST_SHUT_DOWN": 0,
                "MIN_POWER_CHARGE": -cap, "MAX_POWER_CHARGE": cap,
                "GEN_AFFI_FACTOR": affi, "CHAEGE_AFFI_FACTOR": affi,
                "PUMP_MIN_MW": cap, "ENERGY_MWH": _energy_mwh(cap, r["抽水时长"], r["转换效率"]),
                "SOC_MIN": p["socMin"], "SOC_MAX": p["socMax"], "SOC_INITIAL": p["socInitial"],
                "FOM_COST_PERIOD": p["fom"], "DataStatus": dataStatus,
                "SOURCE_COMMISSION": _iso_date(r["投产时间"]), "SOURCE_STATUS": r["状态"]}

    ps_rows, ps_keys = unit_rows(parsed["pumpedStorage"], "PS", "抽水蓄能", _name_with_no, build_ps)

    # ---- 常规水电（库容式）----
    h = u_cfg["hydro"]

    def build_hyd(key, r):
        return {"UnitKey": key, "NAME": r["电站名称"], "AreaID": 1,
                "CAP": r["装机容量"] * WAN, "GEN_COST": h["genCost"],
                "FOM_COST_PERIOD": h["fom"], "SOURCE_REGULATION_FACTOR": r["水库调节系数"]}

    hyd_rows, hyd_keys = unit_rows(parsed["hydro"], "HYD", "常规水电", lambda r: r["电站名称"], build_hyd)
    # 水电月参数：UnitKey → 平水年、枯水年 → 1..12 月（万kW→MW）
    hyd_month = []
    for r in parsed["hydro"]:
        if id(r) not in hyd_keys:
            continue
        key = hyd_keys[id(r)]
        for hydrology in ("平水年", "枯水年"):
            for m in range(1, 13):
                cv = parsed["hydroCurves"]
                hyd_month.append({
                    "UnitKey": key, "Hydrology": hydrology, "Month": m,
                    "ForcedMW": cv[(r["电站名称"], hydrology, "强迫出力")][m - 1] * WAN,
                    "ExpectedMW": cv[(r["电站名称"], hydrology, "预想出力")][m - 1] * WAN,
                    "AverageMW": cv[(r["电站名称"], hydrology, "平均出力")][m - 1] * WAN})

    # ---- 新型储能（电化学）----
    s = u_cfg["storage"]
    es_rows = []
    for i, r in enumerate(parsed["storage"], 1):
        key = f"{prov}_ES_{i:03d}"
        ok, why = _included(r, year)
        if not ok:
            excluded.append({"设备": "新型储能", "UnitKey": key, "机组名称": r["电站名称"],
                             "容量_MW": float(r["装机容量"]) * WAN, "未纳入原因": why})
            continue
        cap = r["装机容量"] * WAN
        eta = math.sqrt(r["转换效率"])
        es_rows.append({"UnitKey": key, "NAME": r["电站名称"], "AreaID": 1, "CAP": cap,
                        "ENERGY_MWH": _energy_mwh(cap, r["充电时长"], r["转换效率"]),
                        "SOC_MIN": s["socMin"], "SOC_MAX": s["socMax"], "SOC_INITIAL": s["socInitial"],
                        "ETA_CHARGE": eta, "ETA_DISCHARGE": eta, "SELF_DISCHARGE": s["selfDischarge"],
                        "POWER_COST": s["powerCost"], "ENERGY_COST": s["energyCost"],
                        "REPLACE_FACTOR": s["replaceFactor"], "MAX_CYCLES": s["maxCycles"],
                        "FOM_COST_PERIOD": s["fomPower"], "FOM_ENERGY_PERIOD": s["fomEnergy"]})

    # ---- 新能源友好电站（风光储；风光抽 v1 无算例，结构相同待真实数据）----
    fr_rows = []
    for i, r in enumerate(parsed["friendlyStation"], 1):
        f = u_cfg["friendlyStation"]
        windCap, pvCap = r["风电电站"] * WAN, r["光伏电站"] * WAN
        esPower = r["新型储能电站"] * WAN
        eta = math.sqrt(f["efficiency"])
        fr_rows.append({"UnitKey": f"{prov}_FR_{i:03d}", "NAME": r["名称"], "AreaID": 1,
                        "WIND_CAP": windCap, "PV_CAP": pvCap, "ES_POWER": esPower,
                        "ENERGY_MWH": _energy_mwh(esPower, f["storageHours"], f["efficiency"]),
                        "SOC_MIN": f["socMin"], "SOC_MAX": f["socMax"], "SOC_INITIAL": f["socInitial"],
                        "ETA_CHARGE": eta, "ETA_DISCHARGE": eta,
                        "FOM_COST_PERIOD": f["fomPower"], "SELF_DISCHARGE": f["selfDischarge"],
                        "POWER_COST": f["powerCost"], "ENERGY_COST": f["energyCost"],
                        "REPLACE_FACTOR": f["replaceFactor"], "MAX_CYCLES": f["maxCycles"],
                        "FOM_ENERGY_PERIOD": f["fomEnergy"],
                        "WIND_FOM_PERIOD": f["windFom"], "PV_FOM_PERIOD": f["pvFom"]})

    # ---- 风电 / 光伏（分区聚合，装机=纳入电站之和）----
    def renew_included(rows):
        out = []
        for r in rows:
            ok, why = _included(r, year)
            if not ok:
                excluded.append({"设备": "风电/光伏", "UnitKey": "", "机组名称": r["电站名称"],
                                 "容量_MW": float(r["装机容量"]) * WAN, "未纳入原因": why})
            else:
                out.append(r)
        return out

    wind_st = renew_included(parsed["windStations"])
    pv_st = renew_included(parsed["pvStations"])
    windCapTotal = sum(r["装机容量"] for r in wind_st) * WAN
    pvCapTotal = sum(r["装机容量"] for r in pv_st) * WAN
    wind_df = pd.DataFrame([{"UnitKey": f"{prov}_WIND", "分区编号": 1, "分区名称": area,
                             "装机容量_MW": windCapTotal, "FOM_COST_PERIOD": u_cfg["wind"]["fom"],
                             "DataStatus": renewStatus}], columns=RENEW_COLS)
    pv_df = pd.DataFrame([{"UnitKey": f"{prov}_PV", "分区编号": 1, "分区名称": area,
                           "装机容量_MW": pvCapTotal, "FOM_COST_PERIOD": u_cfg["pv"]["fom"],
                           "DataStatus": renewStatus}], columns=RENEW_COLS)

    # ============ 时间轴与曲线（8760，归一化因子口径）============
    dates = pd.date_range(f"{year}-01-01 00:00", periods=8760, freq="h")

    def _hourly(curve_getter, what):
        try:
            return np.array([curve_getter(d) for d in dates])
        except KeyError as e:
            raise KeyError(f"{what} 曲线缺日期/时刻 {e}（不补值）") from None

    loadFac = _hourly(lambda d: parsed["loadCurve"][d.strftime("%m-%d")][d.hour], "负荷")

    # 风电：纳入电站按容量加权合成容量因子。
    # 曲线 sheet 的电站名是装机表名的省略写法（"海风" ↔ "海风2024度夏"），按前缀匹配。
    curveNames = sorted({k[0] for k in parsed["windCurves"]}, key=len, reverse=True)
    st_caps = {}
    for r in wind_st:
        nm = r["电站名称"]
        cands = [cs for cs in curveNames if nm.startswith(cs)]
        if len(cands) != 1:
            raise ValueError(f"风电电站 [{nm}] 在出力曲线中匹配到 {len(cands)} 条曲线（{cands}），无法对应")
        st_caps[nm] = (r["装机容量"] * WAN, cands[0])
    if wind_st:
        def _blend(d):
            num = sum(cap * parsed["windCurves"][(cs, d.strftime("%m-%d"))][d.hour]
                      for cap, cs in st_caps.values())
            return num / windCapTotal
        windFac = _hourly(_blend, "风电")
    else:
        windFac = np.zeros(8760)
    pvFac = _hourly(lambda d: parsed["pvCurves"][d.strftime("%m-%d")][d.hour], "光伏")

    # ============ 需求响应（周一起始周；窗口按年内数据截断）============
    loadMW = loadFac * baseLoadMW
    weekStart = dates.normalize() - pd.to_timedelta(dates.dayofweek, unit="D")
    weekOut, weekCut = {}, {}
    for wk in dict.fromkeys(weekStart):
        peak = loadMW[np.asarray(weekStart == wk)].max()
        weekOut[wk] = shiftRatio * peak
        weekCut[wk] = cutRatio * peak
    dr_df = pd.DataFrame({
        "Time": dates, "AreaID": 1,
        "ShiftOutMax_MW": [weekOut[wk] for wk in weekStart],
        "ShiftInMax_MW": [weekOut[wk] for wk in weekStart],
        "CutMax_MW": [weekCut[wk] for wk in weekStart]})
    dr_u_cfg = cfg["demandResponse"]
    days = pd.Series(dates.normalize().unique())                       # 365 天
    dayMondays = days - pd.to_timedelta(days.dt.dayofweek, unit="D")  # 各天所在周的周一
    drd_df = pd.DataFrame({
        "Date": days,
        "AreaID": 1,
        "CutEnergyMax_MWh": [dr_u_cfg["dailyCutHours"] * weekOut[m] for m in dayMondays]})

    # ============ 检修计划 ============
    mtn = []
    # 煤电：逐日标记 → 起止（排他）；行序与煤电表一一对应
    mrows = parsed["coalMaintenance"]
    if len(mrows) != len(parsed["coal"]):
        raise ValueError(f"煤电检修安排 {len(mrows)} 行与煤电表 {len(parsed['coal'])} 行不一致")
    for r, m in zip(parsed["coal"], mrows):
        if m["name"] != r["电站名称"] or m["capWan"] != r["装机容量"]:
            raise ValueError(f"煤电检修安排行 ({m['name']}/{m['capWan']}) 与煤电表"
                             f" ({r['电站名称']}/{r['装机容量']}) 不对应")
        ok, _ = _included(r, year)
        if not ok or not m["flags"]:
            continue
        flags = m["flags"]                      # 1 基年内天
        if flags != list(range(flags[0], flags[0] + len(flags))):
            raise ValueError(f"{m['name']} #{r['机组编号']} 检修标记不连续，无法折算区间")
        jan1 = pd.Timestamp(year, 1, 1)
        mtn.append({"Year": year, "Type": "煤电", "UnitKey": coal_keys[id(r)],
                    "UnitName": _name_with_no(r), "AreaID": 1,
                    "StartTime": jan1 + pd.Timedelta(days=flags[0] - 1),
                    "EndTime": jan1 + pd.Timedelta(days=flags[-1]),   # 排他
                    "Enabled": 1, "Note": "源表煤电检修安排：逐日标记为1的日期"})
    # 非煤电：非运行周区间展开
    for type_name, rows, keys, name_fn in [
            ("气电", parsed["gas"], gas_keys, _name_with_no),
            ("核电", parsed["nuclear"], nuc_keys, lambda r: r["电站名称"]),
            ("生物质", parsed["biomass"], bio_keys, _name_with_no),
            ("抽水蓄能", parsed["pumpedStorage"], ps_keys, _name_with_no)]:
        for r in rows:
            if id(r) not in keys or not r.get("非运行周"):
                continue
            start, end = _week_dates(r["非运行周"], year)
            mtn.append({"Year": year, "Type": type_name, "UnitKey": keys[id(r)],
                        "UnitName": name_fn(r), "AreaID": 1,
                        "StartTime": start, "EndTime": end, "Enabled": 1,
                        "Note": f"源表非运行周{r['非运行周']}，含首尾周；周一至周日，年初年末截断"})

    # ============ 友好电站时序（同区风光因子 × 友好站容量）============
    frt_rows = []
    for i, fr in enumerate(fr_rows, 1):
        key = fr["UnitKey"]
        frt_rows.append(pd.DataFrame({
            "Time": dates, "UnitKey": key,
            "WindAvailable_MW": windFac * fr["WIND_CAP"],
            "PVAvailable_MW": pvFac * fr["PV_CAP"]}))
    frt_df = pd.concat(frt_rows, ignore_index=True) if frt_rows else pd.DataFrame(columns=FRT_COLS)

    # ============ 系统参数 / 省级约束 / 分区负荷参数 ============
    pen = cfg["penalties"]
    sys_df = pd.DataFrame([
        ("costScaleToYuan", 1, "倍率", "机组成本统一采用人民币元", "算例统一单位", "全部"),
        ("fixedCostReferenceHours", 8760, "h", "固定运维年度基准，按仿真小时折算", f"{year}全年", "全部"),
        ("shedCost", pen["shedCost"], "元/MWh", "失负荷惩罚", "算例假设", "负荷"),
        ("windCurtCost", pen["windCurtail"], "元/MWh", "弃风惩罚", "算例假设", "风电"),
        ("pvCurtCost", pen["pvCurtail"], "元/MWh", "弃光惩罚", "算例假设", "光伏"),
        ("hydroCurtCost", pen["hydroCurtail"], "元/MWh", "弃水惩罚", "算例假设", "水电"),
        ("co2Price", pen["co2Price"], "元/t", f"碳价格情景值，非{year}预测价格", "算例假设", "煤气电"),
        ("so2Price", pen["so2Price"], "元/t", f"SO2排放成本，非{area}税率", "算例假设", "煤电"),
        ("noxPrice", pen["noxPrice"], "元/t", f"NOx排放成本，非{area}税率", "算例假设", "煤电"),
        ("dustPrice", pen["dustPrice"], "元/t", f"烟尘排放成本，非{area}税率", "算例假设", "煤电"),
    ], columns=SYS_COLS)
    prov_cfg = cfg["province"]
    prov_df = pd.DataFrame([{
        "ProvinceID": prov, "AdequacyFactor": prov_cfg["adequacyFactor"],
        "ColdReserveRatio": coldRatio, "RenewableUtilizationMin": prov_cfg["renewableUtilizationMin"],
        "CO2Limit_t": prov_cfg["co2Limit_t"], "ReserveDuration_h": prov_cfg["reserveDuration_h"],
        "Status": (f"旋转{_pct(hotRatio)}、停机{_pct(coldRatio)}取自源表；未给利用率下限，"
                   f"基础值{_num(prov_cfg['renewableUtilizationMin'])}；"
                   f"碳预算及备用持续{_num(prov_cfg['reserveDuration_h'])}h为假设"),
        "HotReserveRatio": hotRatio}], columns=PROV_COLS)
    loadp_df = pd.DataFrame([{
        "AreaID": 1, "AreaName": area, "ShedMaxRatio": dr_u_cfg["shedMaxRatio"],
        "ShiftWindow_h": dr_u_cfg["shiftWindow_h"], "ShiftCost": dr_u_cfg["shiftCost"],
        "CutCost": dr_u_cfg["cutCost"], "DRFixedCost": dr_u_cfg["drFixedCost"],
        "AnnualCutEnergy_MWh": cutRatio * baseLoadMW * dr_u_cfg["annualCutHours"],
        "Status": ((f"源能力各{_pct(shiftRatio)}周峰" if shiftRatio == cutRatio
                    else f"源能力转移{_pct(shiftRatio)}、削减{_pct(cutRatio)}周峰")
                   + f"；失负荷允许记录缺口；{_num(dr_u_cfg['shiftWindow_h'])}h窗口、"
                   f"日{_num(dr_u_cfg['dailyCutHours'])}h/年{_num(dr_u_cfg['annualCutHours'])}h"
                   f"预算及成本为假设")}], columns=LOADP_COLS)

    # ============ 写出三份表 ============
    empty = lambda cols: pd.DataFrame(columns=cols)

    unit_dfs = {
        "分区表": pd.DataFrame([{"AreaID": 1, "AreaName": area, "ProvinceID": prov}],
                               columns=["AreaID", "AreaName", "ProvinceID"]),
        "煤电": coal_df,
        "气电": pd.DataFrame(gas_rows, columns=GAS_COLS),
        "核电": pd.DataFrame(nuc_rows, columns=NUC_COLS),
        "生物质": pd.DataFrame(bio_rows, columns=BIO_COLS),
        "抽水蓄能": pd.DataFrame(ps_rows, columns=PS_COLS),
        "电化学储能": pd.DataFrame(es_rows, columns=ES_COLS),
        "新能源友好电站": pd.DataFrame(fr_rows, columns=FR_COLS),
        "库容式水电": pd.DataFrame(hyd_rows, columns=HYD_COLS),
        "径流式水电": empty(ROR_COLS),      # 收资未含，空表（loader 读表头即可）
        "光热": empty(CSP_COLS),
        "风电": wind_df, "光伏": pv_df,
        "检修计划": pd.DataFrame(mtn, columns=MTN_COLS),
        # 供暖期设置：老师指定海南 11月15日~次年3月15日（起止含），Enabled=1（2026-10-14 MATLAB）
        "供暖期设置": pd.DataFrame(
            [{"ZoneID": 1, "Enabled": 1, "StartMonth": 11, "StartDay": 15,
              "EndMonth": 3, "EndDay": 15, "说明": "按用户指定：每年11月15日至次年3月15日为供暖期，起止日均包含"}],
            columns=["ZoneID", "Enabled", "StartMonth", "StartDay", "EndMonth", "EndDay", "说明"]),
    }
    with pd.ExcelWriter(out_dir / "分区机组信息表.xlsx") as w:
        for sheet, df in unit_dfs.items():
            df.to_excel(w, sheet_name=sheet, index=False)

    sys_dfs = {
        "系统参数": sys_df, "省级约束": prov_df, "分区负荷参数": loadp_df,
        "省内断面": empty(LINE_COLS), "省间通道": empty(TIE_COLS),
        "光热时序": empty(CSP_T_COLS),
        "跨区送电": pd.DataFrame(columns=["Time", area]),   # 海南无送电协议 → 空
        "需求响应时序": dr_df, "需求响应日限额": drd_df,
        "水电月参数": pd.DataFrame(hyd_month, columns=HYDM_COLS),
        "友好电站时序": frt_df,
        "煤电碳排节点": pd.DataFrame(emis, columns=EMIS_COLS),
    }
    with pd.ExcelWriter(out_dir / "算例系统参数.xlsx") as w:
        for sheet, df in sys_dfs.items():
            df.to_excel(w, sheet_name=sheet, index=False)

    base_df = pd.DataFrame([("负荷", area, baseLoadMW), ("风电", area, windCapTotal),
                            ("光伏", area, pvCapTotal)],
                           columns=["类型", "分区", "全年最大值(基准)"])
    prof_dfs = {
        "负荷": pd.DataFrame({"时间": dates, area: loadFac}),
        "风电": pd.DataFrame({"时间": dates, area: windFac}),
        "光伏": pd.DataFrame({"时间": dates, area: pvFac}),
        "归一化基准": base_df,
    }
    with pd.ExcelWriter(out_dir / "运行数据.xlsx") as w:
        for sheet, df in prof_dfs.items():
            df.to_excel(w, sheet_name=sheet, index=False)

    return {
        "unitCounts": {k: len(df) for k, df in unit_dfs.items() if k not in ("分区表",)},
        "maintenance": len(mtn),
        "excluded": excluded,
        "capacityMW": {"wind": windCapTotal, "pv": pvCapTotal, "loadBase": baseLoadMW},
    }


if __name__ == "__main__":
    import sys
    from parse_template import parse_template
    parsed = parse_template(r"..\数据模版-海南(1).xlsx")
    summary = convert(parsed, None, sys.argv[1] if len(sys.argv) > 1 else "tmp_out")
    print("units:", summary["unitCounts"])
    print("maintenance:", summary["maintenance"], "excluded:", len(summary["excluded"]))
