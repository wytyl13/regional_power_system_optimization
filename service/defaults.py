#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File    : defaults.py
默认配置（= 海南2030算例已验证基线）。前端表单的 configDefaults 即本文件；
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
