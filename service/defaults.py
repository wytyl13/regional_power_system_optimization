#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File    : defaults.py
默认配置（= 海南2030算例已验证基线）。config 表单块由本文件生成（build_form）；
用户 config 与其深合并后生效，取值优先级：用户显式传入 > Excel 收资值 > 本默认。
所有值的来源见 docs/服务接口设计方案.md §2.2 与 service/ground_truth.txt。
"""
from __future__ import annotations
import copy

# 储能量换算常数：ENERGY_MWh = CAP × 时长h × sqrt(转换效率) / 0.8
# （0.8 为算例既定系数，抽蓄/储能/友好站三处已逐一数值验证）
ENERGY_DIVISOR = 0.8

DEFAULT_CONFIG = {
    # 情景（startTime/hours 决定求解窗口，必须落在模版曲线覆盖范围内）
    "scenario": {
        "startTime": "2030-01-01 00:00",
        "hours": 168,
        "hydrology": "平水年",          # 平水年 | 枯水年
        "interprovincial": False,       # 省间互济（开=需真实送电协议数据）
        "interRegionTransfer": False,   # 跨区送电
    },
    # 求解器
    "solver": {
        "timeLimit_s": 1800,
        "mipGap": 1e-3,
    },
    # 省级（provinceId 为 UnitKey 前缀，换省即换）
    "province": {
        "provinceId": "HN",
        "adequacyFactor": 1.15,
        "renewableUtilizationMin": 0.0,
        "co2Limit_t": 58872296.712308295,
        "reserveDuration_h": 1.0,
    },
    # 惩罚与排放价格（元）
    "penalties": {
        "shedCost": 28000,
        "windCurtail": 500,
        "pvCurtail": 500,
        "hydroCurtail": 500,
        "co2Price": 70,
        "so2Price": 1000,
        "noxPrice": 1000,
        "dustPrice": 1000,
    },
    # 需求响应（比例默认取 Excel 收资值；此处为收资缺失时的兜底）
    "demandResponse": {
        "shiftRatio": 0.025,       # 转移型，周峰比例
        "cutRatio": 0.025,         # 削减型，周峰比例
        "shiftWindow_h": 4,
        "shiftCost": 600,
        "cutCost": 3000,
        "shedMaxRatio": 1.0,       # 失负荷开关：1=允许（记录缺口）
        "drFixedCost": 0,
        "dailyCutHours": 2,        # 日削减电量预算 = 2h × 周响应值
        "annualCutHours": 20,      # 年削减预算 = 比例 × 基准负荷 × 20h
    },
    "units": {
        "coal": {
            "minUpDown_h": 8,
            "rampRatio": 0.3,          # 爬坡 = 0.3×CAP /h
            "costA": 0.01,             # 元/MW²·h
            "costB": 280,              # 元/MWh
            "costCPerMW": 10,          # COST_C = 10×CAP
            "startUpPerMW": 200,       # 启动 = 200×CAP
            "shutDownPerMW": 10,
            "initialOn": True, "initialHours": 24,
            "fom": 84000,
        },
        "gas": {
            "minUpDown_h": 3,
            "rampRatio": 1.0,
            "startUpPerMW": 50,
            "shutDownPerMW": 10,
            "startStopRampRatio": 0.85,   # 启停爬坡 = 0.85×CAP
            "co2Factor": 0.40392,          # t/MWh
            "genCost": 400,
            "initialOn": False, "initialHours": 24,
            "fom": 74000,
        },
        "nuclear": {
            "rampRatio": 0.2,
            "genCost": 100,
            "fom": 300000,
        },
        "biomass": {
            "rampRatio": 1.0,
            "genCost": 400,
            "annualHours": 8000,      # 年等效发电小时（ENERGY_LIMIT 用）
            "fom": 212500,
        },
        "hydro": {
            "genCost": 50,
            "fom": 150000,
        },
        "wind": {"fom": 100000},
        "pv": {"fom": 30000},
        "pumpedStorage": {
            "fom": 90000,
            "socMin": 0.1, "socMax": 0.9, "socInitial": 0.5,
        },
        "storage": {   # 新型储能（电化学）
            "socMin": 0.1, "socMax": 0.9, "socInitial": 0.5,
            "selfDischarge": 0.0001,
            "powerCost": 200000, "energyCost": 1100000,
            "replaceFactor": 0.8, "maxCycles": 5000,
            "fomPower": 4000, "fomEnergy": 22000,
        },
        "friendlyStation": {
            "storageHours": 2,        # 友好站储能时长 h
            "efficiency": 0.9,        # 转换效率（模板未给，配置兜底）
            "socMin": 0.1, "socMax": 0.9, "socInitial": 0.5,
            "selfDischarge": 0.0001,
            "powerCost": 200000, "energyCost": 1100000,
            "replaceFactor": 0.8, "maxCycles": 5000,
            "fomPower": 4000, "fomEnergy": 22000,
            "windFom": 100000, "pvFom": 30000,
        },
    },
    # 煤电碳排节点
    "emission": {
        "so2Factor": 0.0001,           # t/MWh
        "noxFactor": 0.00015,
        "dustFactor": 1.5e-05,
        "coalCO2_kgPerGJ": 94.6,
        "coalEfficiencyLarge": 0.38,   # CAP ≥ 300MW
        "coalEfficiencySmall": 0.35,
        "largeCapThresholdMW": 300,
        "partialLoadFactor": 0.1,      # 低位发热修正项系数
    },
}


# configDefaults 字段 → 中文描述（含收资浮出的 hotReserveRatio/coldReserveRatio；
# build_form 本地取用：label=标签、value=默认值，服务响应不再单独下发 fieldsMapping）
FIELDS_MAPPING = {
    # ---- scenario 情景 ----
    "scenario.startTime": "求解起始时间",
    "scenario.hours": "求解时长（小时）",
    "scenario.hydrology": "水文情景（平水年/枯水年）",
    "scenario.interprovincial": "省间互济开关",
    "scenario.interRegionTransfer": "跨区送电开关",
    # ---- solver 求解器 ----
    "solver.timeLimit_s": "求解时限（秒）",
    "solver.mipGap": "MIP最优间隙（相对值）",
    # ---- province 省级 ----
    "province.provinceId": "省份编号",
    "province.adequacyFactor": "电力充裕度系数",
    "province.renewableUtilizationMin": "新能源利用率下限",
    "province.co2Limit_t": "碳排放上限（吨）",
    "province.reserveDuration_h": "备用时长（小时）",
    "province.hotReserveRatio": "热备用率",
    "province.coldReserveRatio": "冷备用率",
    # ---- penalties 惩罚与排放价格 ----
    "penalties.shedCost": "失负荷惩罚（元/MWh）",
    "penalties.windCurtail": "风电弃电惩罚（元/MWh）",
    "penalties.pvCurtail": "光伏弃电惩罚（元/MWh）",
    "penalties.hydroCurtail": "水电弃电惩罚（元/MWh）",
    "penalties.co2Price": "碳排放价格（元/吨）",
    "penalties.so2Price": "SO₂排放价格（元/吨）",
    "penalties.noxPrice": "NOx排放价格（元/吨）",
    "penalties.dustPrice": "烟尘排放价格（元/吨）",
    # ---- demandResponse 需求响应 ----
    "demandResponse.shiftRatio": "转移型响应比例（占周峰）",
    "demandResponse.cutRatio": "削减型响应比例（占周峰）",
    "demandResponse.shiftWindow_h": "负荷转移窗口（小时）",
    "demandResponse.shiftCost": "转移成本（元/MWh）",
    "demandResponse.cutCost": "削减成本（元/MWh）",
    "demandResponse.shedMaxRatio": "失负荷上限比例",
    "demandResponse.drFixedCost": "响应固定成本（元）",
    "demandResponse.dailyCutHours": "日削减电量预算（小时）",
    "demandResponse.annualCutHours": "年削减预算（小时）",
    # ---- units.coal 煤电 ----
    "units.coal.minUpDown_h": "最小开停机时间（小时）",
    "units.coal.rampRatio": "爬坡速率（比例×容量/小时）",
    "units.coal.costA": "二次成本系数（元/MW²·h）",
    "units.coal.costB": "线性成本（元/MWh）",
    "units.coal.costCPerMW": "常数成本系数（元/MW）",
    "units.coal.startUpPerMW": "启动成本（元/MW）",
    "units.coal.shutDownPerMW": "停机成本（元/MW）",
    "units.coal.initialOn": "初始开机状态",
    "units.coal.initialHours": "初始已开机时长（小时）",
    "units.coal.fom": "固定运维成本（元/MW·年）",
    # ---- units.gas 气电 ----
    "units.gas.minUpDown_h": "最小开停机时间（小时）",
    "units.gas.rampRatio": "爬坡速率（比例×容量/小时）",
    "units.gas.startUpPerMW": "启动成本（元/MW）",
    "units.gas.shutDownPerMW": "停机成本（元/MW）",
    "units.gas.startStopRampRatio": "启停爬坡（比例×容量）",
    "units.gas.co2Factor": "碳排因子（吨/MWh）",
    "units.gas.genCost": "发电成本（元/MWh）",
    "units.gas.initialOn": "初始开机状态",
    "units.gas.initialHours": "初始已停机时长（小时）",
    "units.gas.fom": "固定运维成本（元/MW·年）",
    # ---- units.nuclear 核电 ----
    "units.nuclear.rampRatio": "爬坡速率（比例×容量/小时）",
    "units.nuclear.genCost": "发电成本（元/MWh）",
    "units.nuclear.fom": "固定运维成本（元/MW·年）",
    # ---- units.biomass 生物质 ----
    "units.biomass.rampRatio": "爬坡速率（比例×容量/小时）",
    "units.biomass.genCost": "发电成本（元/MWh）",
    "units.biomass.annualHours": "年等效发电小时",
    "units.biomass.fom": "固定运维成本（元/MW·年）",
    # ---- units.hydro / wind / pv ----
    "units.hydro.genCost": "发电成本（元/MWh）",
    "units.hydro.fom": "固定运维成本（元/MW·年）",
    "units.wind.fom": "风电固定运维成本（元/MW·年）",
    "units.pv.fom": "光伏固定运维成本（元/MW·年）",
    # ---- units.pumpedStorage 抽水蓄能 ----
    "units.pumpedStorage.fom": "固定运维成本（元/MW·年）",
    "units.pumpedStorage.socMin": "SOC（荷电状态）下限",
    "units.pumpedStorage.socMax": "SOC上限",
    "units.pumpedStorage.socInitial": "初始SOC",
    # ---- units.storage 新型储能 ----
    "units.storage.socMin": "SOC（荷电状态）下限",
    "units.storage.socMax": "SOC上限",
    "units.storage.socInitial": "初始SOC",
    "units.storage.selfDischarge": "自放电率",
    "units.storage.powerCost": "功率成本（元/MW）",
    "units.storage.energyCost": "容量成本（元/MWh）",
    "units.storage.replaceFactor": "更换系数",
    "units.storage.maxCycles": "循环寿命（次）",
    "units.storage.fomPower": "功率固定运维成本（元/MW·年）",
    "units.storage.fomEnergy": "容量固定运维成本（元/MWh·年）",
    # ---- units.friendlyStation 友好电站 ----
    "units.friendlyStation.storageHours": "储能时长（小时）",
    "units.friendlyStation.efficiency": "转换效率",
    "units.friendlyStation.socMin": "SOC（荷电状态）下限",
    "units.friendlyStation.socMax": "SOC上限",
    "units.friendlyStation.socInitial": "初始SOC",
    "units.friendlyStation.selfDischarge": "自放电率",
    "units.friendlyStation.powerCost": "功率成本（元/MW）",
    "units.friendlyStation.energyCost": "容量成本（元/MWh）",
    "units.friendlyStation.replaceFactor": "更换系数",
    "units.friendlyStation.maxCycles": "循环寿命（次）",
    "units.friendlyStation.fomPower": "功率固定运维成本（元/MW·年）",
    "units.friendlyStation.fomEnergy": "容量固定运维成本（元/MWh·年）",
    "units.friendlyStation.windFom": "风电部分固定运维（元/MW·年）",
    "units.friendlyStation.pvFom": "光伏部分固定运维（元/MW·年）",
    # ---- emission 排放 ----
    "emission.so2Factor": "SO₂排放因子（吨/MWh）",
    "emission.noxFactor": "NOx排放因子（吨/MWh）",
    "emission.dustFactor": "烟尘排放因子（吨/MWh）",
    "emission.coalCO2_kgPerGJ": "单位热值碳排放（kg/GJ）",
    "emission.coalEfficiencyLarge": "大机组发电效率（≥300MW）",
    "emission.coalEfficiencySmall": "小机组发电效率（<300MW）",
    "emission.largeCapThresholdMW": "大小机组容量阈值（MW）",
    "emission.partialLoadFactor": "低位发热修正系数",
}


def merge_config(user: dict | None) -> dict:
    """用户 config 深合并到默认值上（只覆盖显式给出的键）。"""
    cfg = copy.deepcopy(DEFAULT_CONFIG)

    def _merge(base, over):
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                _merge(base[k], v)
            else:
                base[k] = copy.deepcopy(v)

    if user:
        _merge(cfg, user)
    return cfg


# ---- 服务端固定口径（convert.py 写死、config 改不动的部分；/api/datasets 只读展示）----
FIXED_PARAMS = [
    {"key": "wanToMW", "label": "容量单位换算",
     "value": "源表装机为 万kW 口径：容量 ×10 → MW；曲线为归一化因子，负荷=因子×最大负荷"},
    {"key": "curveBlend", "label": "8760 曲线合成",
     "value": "风电=纳入电站出力曲线按装机容量加权合成一条容量因子；光伏=全区一条因子"},
    {"key": "energyFormula", "label": "储能容量换算",
     "value": "储能容量 = 功率 × 时长 × √(源表转换效率) ÷ 0.8（抽蓄/新型储能/友好站三处同式）"},
    {"key": "coalEmissionNodes", "label": "煤电碳排线性化",
     "value": "Pmin→Pmax 等分 6 节点；节点速率 = 单位热值碳排 × 3.6 ÷ 发电效率 × P"
              " × (1 + 低位修正系数 × (1 − P÷CAP))，系数可在 emission 配置组修改"},
    {"key": "weekAnchor", "label": "周锚点口径",
     "value": "备用需求/需求响应潜力按当周最大负荷的比例展开；含 1月1日 的那周周一为第 1 周"},
    {"key": "maintenanceParse", "label": "煤电检修口径",
     "value": "源表逐日标记为 1 的连续日期折算为起止区间（右端排他），不连续即报错"},
    {"key": "windCurveMatch", "label": "风电曲线匹配",
     "value": "出力曲线站名按源表电站名前缀归一（曲线表为省略写法，两版模版通吃）"},
    {"key": "pvCurveRule", "label": "光伏曲线口径",
     "value": "光伏出力曲线整表对应全区一条因子，电站名称列不作实体键"},
]


def build_form(defaults: dict, hydrology: list) -> dict:
    """配置默认值 + 字段标签 → /api/datasets 的 config 表单块
    {组:{键:{label, value, options?}}}。wire 格式不变：提交计算时前端只回
    {组:{键:值}}（POST /api/jobs 原契约），label/options 剥掉不回传。"""
    form = {}
    for g, kv in defaults.items():
        form[g] = {k: {"label": FIELDS_MAPPING.get(f"{g}.{k}", f"{g}.{k}"), "value": v}
                   for k, v in kv.items()}
    form["scenario"]["hydrology"]["options"] = sorted(hydrology)
    return form
