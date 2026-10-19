#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/29 13:41
@Author  : weiyutao
@File    : parse_template.py

解析收资模版（数据模版-*.xlsx，23 个 sheet）→ 纯结构化数据。
只提取不换算：单位换算、机组筛选、派生计算全部在 convert.py。
缺 sheet / 缺列 / 缺曲线行 → ValueError，不造数。
"""
from __future__ import annotations

import openpyxl

HOURS = [f"{h}时" for h in range(24)]

# 新模版把单位/口径说明写进了表头（parse 认旧名，双名兼容两版模版通吃）
COL_ALIAS = {
    "最大负荷（万千瓦）": "最大负荷",
    "用电量（亿千瓦时）": "用电量",
    "旋转备用（每周备用需求的规模为当周最大负荷的上述比例）": "旋转备用",
    "转移型负荷响应比例（每周响应潜力的规模为当周最大负荷的上述比例）": "转移型负荷响应比例",
}


def _load(path):
    """只读方式打开模版工作簿（data_only 取公式计算值）。

    Args:
        path: 模版文件路径。

    Returns:
        Workbook: openpyxl 工作簿（调用方负责 close）。
    """
    return openpyxl.load_workbook(path, read_only=True, data_only=True)


def _rows(wb, sheet, required_cols, key_col=None):
    """按表头名取列，返回 [ {列名: 值}, ... ]。跳过全空行；
    给 key_col 时只留该列非空的行（滤掉表尾的单位/说明行）。

    Args:
        wb (Workbook): 模版工作簿。
        sheet (str): sheet 名。
        required_cols (list[str]): 必需列名（缺列 → ValueError）。
        key_col (str | None): 主键列名；给定时该列为空的行被跳过。

    Returns:
        list[dict]: 行列表（键 = 列名）。
    """
    if sheet not in wb.sheetnames:
        raise ValueError(f"模版缺 sheet: [{sheet}]")
    ws = wb[sheet]
    it = ws.iter_rows(values_only=True)
    try:
        header = list(next(it))
    except StopIteration:
        raise ValueError(f"sheet [{sheet}] 无表头") from None
    if all(v is None for v in header):   # 全空 sheet（如送电曲线-8760 未填）
        return []
    header = [COL_ALIAS.get(str(c), c) if c is not None else c for c in header]
    idx = {}
    for col in required_cols:
        if col not in header:
            raise ValueError(f"sheet [{sheet}] 缺列: {col}")
        idx[col] = header.index(col)
    out = []
    for r in it:
        if r is None or all(v is None for v in r):
            continue
        if key_col is not None and r[idx[key_col]] is None:
            continue
        if all(r[i] is None for i in idx.values()):
            continue
        out.append({c: r[i] for c, i in idx.items()})
    return out


def _curves(wb, sheet, station_col):
    """8760 曲线 sheet → {(电站名称, 日期str): [24 float]}。

    Args:
        wb (Workbook): 模版工作簿。
        sheet (str): 曲线 sheet 名。
        station_col (str): 电站名称列名。

    Returns:
        dict: {(电站名称, 日期str): [24 个小时值]}。
    """
    rows = _rows(wb, sheet, ["日期"] + HOURS + [station_col])
    out = {}
    for r in rows:
        out[(r[station_col], str(r["日期"]))] = [float(r[h]) for h in HOURS]
    return out


def parse_template(path) -> dict:
    """解析模版，返回结构化 dict。year 取自负荷表年度。

    Args:
        path: 模版文件路径。

    Returns:
        dict: 结构化结果（负荷/备用/机组/曲线/检修等键，原始口径不换算）。
    """
    wb = _load(path)
    try:
        return _parse(wb)
    finally:
        wb.close()


def _parse(wb) -> dict:
    """逐 sheet 提取结构化数据（只提取不换算；缺 sheet/列/行 → ValueError）。

    Args:
        wb (Workbook): 模版工作簿。

    Returns:
        dict: 结构化结果（area/year/负荷/备用/需求响应/机组/检修/曲线等键）。
    """
    # ---- 负荷/年度 ----
    load = _rows(wb, "负荷", ["区域", "年度", "最大负荷", "用电量"], key_col="年度")
    if len(load) != 1:
        raise ValueError(f"负荷表应恰有 1 行，实际 {len(load)} 行")
    area = str(load[0]["区域"])
    year = int(load[0]["年度"])
    d = {
        "area": area, "year": year,
        "maxLoadWan": float(load[0]["最大负荷"]),      # 万kW
        "energyYi": float(load[0]["用电量"]),           # 亿kWh
    }

    # ---- 备用 / 需求响应（周峰比例）----
    res = _rows(wb, "备用", ["时间", "旋转备用", "停机备用"], key_col="时间")
    if len(res) != 1:
        raise ValueError("备用表应恰有 1 行")
    d["hotReserveRatio"] = float(res[0]["旋转备用"])
    d["coldReserveRatio"] = float(res[0]["停机备用"])
    dr = _rows(wb, "需求响应", ["时间", "转移型负荷响应比例", "削减型负荷响应比例"],
               key_col="时间")
    if len(dr) != 1:
        raise ValueError("需求响应表应恰有 1 行")
    d["drShiftRatio"] = float(dr[0]["转移型负荷响应比例"])
    d["drCutRatio"] = float(dr[0]["削减型负荷响应比例"])

    # ---- 负荷 8760（归一化因子，日期 MM-DD → 24 值）----
    lrows = _rows(wb, "负荷-8760", ["日期"] + HOURS)
    d["loadCurve"] = {str(r["日期"]): [float(r[h]) for h in HOURS] for r in lrows}

    # ---- 各类机组（原始行，万kW）----
    d["coal"] = _rows(wb, "煤电", ["电站名称", "装机容量", "机组编号", "最小技术出力率",
                                   "供暖期最小技术出力率", "非供暖期最小技术出力率",
                                   "受阻率", "非供暖期受阻率",
                                   "检修天数", "状态", "投产时间", "退役时间"])
    d["gas"] = _rows(wb, "气电", ["电站名称", "装机容量", "机组编号", "最小技术出力率",
                                  "供暖期最小技术出力率", "非供暖期最小技术出力率",
                                  "受阻率", "非供暖期受阻率",
                                  "工作模式", "状态", "投产时间", "非运行周"])
    d["nuclear"] = _rows(wb, "核电", ["电站名称", "装机容量", "机组编号", "状态", "投产时间", "非运行周"])
    d["biomass"] = _rows(wb, "生物质", ["电站名称", "装机容量", "机组编号", "最小技术出力率",
                                        "供暖期最小技术出力率", "非供暖期最小技术出力率",
                                        "受阻率", "非供暖期受阻率",
                                        "状态", "投产时间", "非运行周"])
    d["pumpedStorage"] = _rows(wb, "抽水蓄能", ["电站名称", "装机容量", "机组编号", "转换效率",
                                                "抽水时长", "状态", "投产时间", "非运行周"])
    d["hydro"] = _rows(wb, "常规水电", ["电站名称", "装机容量", "水库调节系数", "状态", "投产时间"])

    # ---- 煤电检修安排：逐日标记（366 日列，1 基列号）----
    if "煤电检修安排" not in wb.sheetnames:
        raise ValueError("模版缺 sheet: [煤电检修安排]")
    ws = wb["煤电检修安排"]
    it = ws.iter_rows(values_only=True)
    header = list(next(it))
    dayCols = {str(c): header.index(c) for c in header if isinstance(c, int)}  # '1'..'366'
    mtn = []
    for r in it:
        if r is None or r[0] is None:
            continue
        flags = [int(c) for c in range(1, 367)
                 if r[dayCols[str(c)]] is not None and str(r[dayCols[str(c)]]).strip() not in ("", "0")]
        mtn.append({"id": r[0], "name": str(r[1]), "capWan": r[2], "flags": flags})
    d["coalMaintenance"] = mtn

    # ---- 风电/光伏：电站 + 曲线 ----
    d["windStations"] = _rows(wb, "风电", ["电站名称", "装机容量", "状态", "投产时间"])
    d["pvStations"] = _rows(wb, "光伏", ["电站名称", "装机容量", "状态", "投产时间"])
    d["windCurves"] = _curves(wb, "风电-出力曲线", "电站名称")
    # 光伏曲线按日期读（源表电站名称逐行递增，不作为机组拆分依据）
    d["pvCurves"] = {date: vals for (_, date), vals in _curves(wb, "光伏-出力曲线", "电站名称").items()}

    # ---- 新型储能 / 友好电站 ----
    d["storage"] = _rows(wb, "新型储能", ["电站名称", "装机容量", "机组编号", "转换效率",
                                          "充电时长", "状态", "投产时间"])
    fs = _rows(wb, "风光储", ["名称", "新型储能电站", "光伏电站", "风电电站"])
    d["friendlyStation"] = fs if fs and fs[0]["新型储能电站"] not in (None, "无") else []
    fp = _rows(wb, "风光抽", ["名称", "抽蓄储能电站", "光伏电站", "风电电站"])
    d["friendlyPump"] = fp if fp and fp[0]["抽蓄储能电站"] not in (None, "无") else []

    # ---- 水电月出力曲线 ----
    hrows = _rows(wb, "常规水电-出力曲线",
                  ["电站名称", "水文年", "出力类型"] + [f"{m}月" for m in range(1, 13)])
    d["hydroCurves"] = {(r["电站名称"], r["水文年"], r["出力类型"]):
                        [float(r[f"{m}月"]) for m in range(1, 13)] for r in hrows}

    # ---- 送电协议（海南收资为空：源表 0 行、8760 曲线只有表头 → 空如实展示；
    #      288 测试通道 v1 不用：既不展示也不参与计算，解析整段移除）----
    d["interconnection"] = _rows(wb, "送电协议",
                                 ["通道名称", "送端", "受端", "状态", "投产时间"])
    d["transferCurve8760"] = _rows(wb, "送电协议-送电曲线-8760", ["日期"] + HOURS) \
        if "送电协议-送电曲线-8760" in wb.sheetnames else []

    return d


if __name__ == "__main__":
    import json
    t = parse_template(r"..\数据模版-海南(1).xlsx")
    print("area:", t["area"], "year:", t["year"], "maxLoad:", t["maxLoadWan"])
    print("coal:", len(t["coal"]), "gas:", len(t["gas"]), "mtn:", len(t["coalMaintenance"]),
          "loadDays:", len(t["loadCurve"]), "windDays:", len(t["windCurves"]),
          "pvDays:", len(t["pvCurves"]), "hydroCurves:", len(t["hydroCurves"]),
          "transfer8760:", len(t["transferCurve8760"]))
