#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/20 19:10
@Author  : weiyutao
@File    : excel_export.py
§12.1 Excel 写盘（纯呈现层）：Solution.sheets → 模型输出结果_逐小时.xlsx。
2026-10-20 解耦重构（用户裁决）：数据构建移 Solution.sheets（dict[sheet名,
DataFrame]），本文件只做写盘，不含任何口径；sheet 顺序 = sheets 插入序。
2026-10-20 晚（用户裁决）：全部页只保留第一行表头，明细页原第二行口径
描述不再落表（MATLAB 终态为两行头，此后 Excel 逐格对拍以去行后数据区为准）。
前端 JSON 由 service 直接消费 Solution.sheets，与本文件互不感知。
"""
from __future__ import annotations

import numpy as np
from openpyxl import Workbook


def _append_row(ws, values) -> None:
    """一行写入（openpyxl 不收 numpy 标量，逐格转 Python 原生类型）。

    Args:
        ws: openpyxl 工作表。
        values (list): 单元格值序列。

    Returns:
        None
    """
    ws.append([v.item() if isinstance(v, np.generic) else v for v in values])


def write_excel(solution, path) -> None:
    """Solution.sheets → xlsx：逐 sheet 逐行 append，数值页单行表头。

    Args:
        solution (Solution): 求解结果（sheets 属性含全部 12 页）。
        path: 输出 xlsx 路径。

    Returns:
        None
    """
    wb = Workbook()
    for i, (name, df) in enumerate(solution.sheets.items()):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = name
        if df.index.name:                      # 数值页：单行头 + 日期列 + 数据
            _append_row(ws, [df.index.name] + list(df.columns))
            for idx, row in zip(df.index.tolist(), df.to_numpy().tolist()):
                _append_row(ws, [idx] + row)
        else:                                  # 说明页：自由行表
            for row in df.to_numpy().tolist():
                _append_row(ws, row)
    wb.save(path)
    print("已导出: %s" % path)
