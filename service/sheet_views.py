#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/20 15:40
@Author  : weiyutao
@File    : sheet_views.py
solution.sheets → 前端 views list（2026-10-20 与用户定稿格式）：
  [{"series": {sheet_name, index, data:[{column_name, data}]},
    "lines":  {sheet_name, index, data:[{column_name, data}]}}]
映射表在下方两常量：取啥 sheet 列 → 映射成啥名；sheet 缺列（未装机）跳过。
时间轴随各块自带（index = sheet 日期索引），不再单列 result.time。
"""
from __future__ import annotations

# 逐小时结果 sheet 列名 → 前端序列名（用户可自行增删改）
HOURLY_SERIES_MAP = {
    "煤电": "煤电",
    "气电": "气电",
    "生物质": "生物质",
    "核电": "核电",
    "径流式水电": "径流式水电",
    "库容式水电": "库容式水电",
    "风电": "风电",
    "光伏": "光伏",
    "抽水蓄能净出力": "抽水蓄能",
    "独立电化学储能净出力": "电化学储能",
    "友好一体化电站": "友好电站",
}
# 参照线：逐小时结果 sheet 列名 → 前端线名
HOURLY_LINES_MAP = {
    "本地负荷": "原始负荷",
    "响应后负荷": "响应后负荷",
}


def build_sheet_views(solution) -> list:
    """逐小时结果 sheet → views list（series/lines 各带 sheet_name/index/data）。

    Args:
        solution (Solution): 求解结果（sheets 属性含逐小时结果页）。

    Returns:
        list: [ {series: {...}, lines: {...}} ]。
    """
    frame = solution.sheets["逐小时结果"]
    index = [str(t) for t in frame.index]

    def block(mapping):
        data = [{"column_name": name, "data": frame[col].tolist()}
                for col, name in mapping.items() if col in frame.columns]
        return {"sheet_name": "逐小时结果", "index": index, "data": data}

    return [{"series": block(HOURLY_SERIES_MAP),
             "lines": block(HOURLY_LINES_MAP)}]
