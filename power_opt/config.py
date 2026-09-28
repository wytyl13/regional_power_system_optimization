#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/21 16:34
@Author  : weiyutao
@File    : config.py
唯一配置入口
"""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
MATLAB_DIR = PROJECT_DIR.parent / "海南2030算例"

UNIT_FILE = MATLAB_DIR / "分区机组信息表.xlsx"
PROFILE_FILE = MATLAB_DIR / "运行数据.xlsx"
SYSTEM_FILE = MATLAB_DIR / "算例系统参数.xlsx"
RESULT_DIR = PROJECT_DIR / "results"

T = 168
START_TIME = "2030-01-01 00:00"
DELTA_T = 1.0
HYDROLOGY_SCENARIO = "平水年"  # 水文情景，镜像 MATLAB 41 行；可改"枯水年"

MIGAP = 1e-3
TIME_LIMIT = 1800
# 以下三项镜像 MATLAB 1165-1168（优先找可行解、区分不可行/无界）
MIP_FOCUS = 1
HEURISTICS = 0.2
DUAL_REDUCTIONS = 0

# 省间互济开关，测试数据不包含
USE_INTERPROVINCIAL = False
# 既定跨区送电，国家宏观调控，测试数据不包含
USE_FIXED_TRANSFER   = False

# MATLAB_REF = {
#     1:  dict(rows=5974,   cols=4012,  nz=16554,  obj=13983472.76616,
#              fingerprint="0x2f1bfb53", obj_const=921276.71232876),
#     24: dict(rows=135933, cols=89236, nz=411744, obj=329325836.2167,
#              fingerprint="0x4a7d4cbe", obj_const=22110641.09589042),
# }
