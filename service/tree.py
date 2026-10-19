#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/29 13:41
@Author  : weiyutao
@File    : tree.py

模版 xlsx → 前端展示契约两件套：
  build_categories：固定五大类菜单（负荷及备用/电源/送电协议/一体化电站/检修安排），
    细分名=源 sheet 名；小表（源数据表/检修安排）全量内联 {name, columns, rows}；
    曲线不内联，只给元数据 curve={sheet, filterBy, wholeSheet?}，前端点行后
    调 GET /api/datasets/{id}/curves/{sheet}?<filterBy=值> 懒加载。
  read_curve：曲线懒加载端点的取数实现（匹配规则集中本文件，app.py 只调）。
规则（两版模版通吃，服务端收口）：
  精确匹配：负荷/常规水电/送电协议曲线；风电=源表站名前缀归一（旧模版曲线为省略写法）；
  光伏=整表一条因子（电站名称列是逐日占位名，不作实体键）。
"""
from __future__ import annotations

import datetime

import openpyxl

# ---- 曲线匹配规则注册表：细分名 → (目标 sheet, 筛选键列表, 匹配方式, 剔除列) ----
# 匹配方式：exact=键值精确；prefix=源站名前缀归一（曲线站名∈源站名前缀）；
#           whole=整表一条曲线，filterBy 仅校验存在。剔除列=输出的表格中不出现的
#           伴随标识列（filterBy 键本身必剔；区域/年度等键值已在请求/源表可见）
CURVE_RULES = {
    "负荷": ("负荷-8760", ["区域"], "exact", ["年度"]),
    "常规水电": ("常规水电-出力曲线", ["电站名称"], "exact", ["区域"]),
    "风电": ("风电-出力曲线", ["电站名称"], "prefix", ["区域"]),
    "光伏": ("光伏-出力曲线", [], "whole", ["区域", "电站名称"]),
    "送电协议": ("送电协议-送电曲线-8760", ["区域", "通道名称", "送端", "受端"],
                 "exact", []),
}

# ---- 固定菜单：五大类 → 细分（=源 sheet，按渲染序）；curve 细分见 CURVE_RULES ----
CATEGORY_MAP = [
    ("负荷及备用", ["负荷", "备用", "需求响应"]),
    ("电源", ["核电", "抽水蓄能", "常规水电", "气电", "生物质", "煤电", "风电",
              "光伏", "新型储能"]),
    ("送电协议", []),          # 无细分，本级直接挂表
    ("一体化电站", ["风光储", "风光抽"]),
    ("检修安排", ["煤电检修安排"]),
]


def _s(v) -> str:
    """单元格 → 字符串：None→空串；整值浮点去 .0；日期 → YYYY-MM-DD。

    Args:
        v: 单元格值。

    Returns:
        str: 展示文本。
    """
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime("%Y-%m-%d")
    return str(v)


def _table(wb, sheet_name: str) -> dict | None:
    """源 sheet → {name, columns, rows}（全字符串、模版表头原样）。
    sheet 不存在或无表头 → None（不出现在菜单）。不洗尾部说明行：
    尾行若有文字说明也如实展示（收资文件长什么样前端就看到什么样）。

    Args:
        wb (Workbook): 模版工作簿。
        sheet_name (str): 源 sheet 名。

    Returns:
        dict | None: {name, columns, rows}；sheet 缺失/无表头 → None。
    """
    if sheet_name not in wb.sheetnames:
        return None
    it = wb[sheet_name].iter_rows(values_only=True)
    header = list(next(it, None) or [])
    if all(v is None for v in header):
        return None
    colIdx = {str(c): j for j, c in enumerate(header) if c is not None}
    rows = []
    for r in it:
        if r is None or all(v is None for v in r):
            continue
        rows.append({c: _s(r[j]) if j < len(r) else "" for c, j in colIdx.items()})
    return {"name": sheet_name, "columns": list(colIdx), "rows": rows}


def _curve_meta(sheet: str) -> dict | None:
    """细分名 → 曲线懒加载元数据 {sheet, filterBy, match, wholeSheet?}。

    Args:
        sheet (str): 细分名（CURVE_RULES 的键）。

    Returns:
        dict | None: 曲线元数据；非曲线细分 → None。
    """
    if sheet not in CURVE_RULES:
        return None
    target, filter_by, match, drop = CURVE_RULES[sheet]
    meta = {"sheet": target, "filterBy": filter_by, "match": match}
    if match == "whole":
        meta["wholeSheet"] = True
    return meta


def build_categories(path) -> list[dict]:
    """模版文件 → 固定五大类数组（顺序即渲染序，前端照序展示零写死）。
    有 items → 二级菜单；无 items（送电协议）→ 本级直接挂 tables。

    Args:
        path: 模版文件路径。

    Returns:
        list[dict]: 五大类菜单数组。
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        categories = []
        for cat_name, item_sheets in CATEGORY_MAP:
            if not item_sheets:
                t = _table(wb, cat_name)
                cat = {"name": cat_name,
                       "tables": [t] if t else [],
                       "curve": _curve_meta(cat_name)}
                if not cat["tables"] and not cat["curve"]:
                    continue      # sheet 缺失且无曲线 → 整类不出现
                categories.append(cat)
                continue
            items = []
            for s in item_sheets:
                t = _table(wb, s)
                if t is None:
                    continue      # sheet 缺失 → 细分不出现，不造空表
                items.append({"name": s, "tables": [t],
                              **({"curve": c} if (c := _curve_meta(s)) else {})})
            if items:
                categories.append({"name": cat_name, "items": items})
        return categories
    finally:
        wb.close()


def read_curve(path, sheet: str, filters: dict[str, str]) -> dict:
    """曲线懒加载：按 CURVE_RULES[sheet] 的匹配规则筛曲线表行。
    filters = {filterBy 键: 行上单元格值}（前端从点击行取）。
    返回 {columns, rows}，剔除筛选键列（值已在请求里）。
    表存在但 0 数据行 → 返回空表（未收资如实展示）；
    有数据但筛选 0 命中 / prefix 有歧义 → ValueError（app.py 转 422，不造数）。

    Args:
        path: 模版文件路径。
        sheet (str): 目标曲线 sheet 名。
        filters (dict[str, str]): {filterBy 键: 点击行的单元格值}。

    Returns:
        dict: {columns, rows}。
    """
    rule_key = next((k for k, r in CURVE_RULES.items() if r[0] == sheet), None)
    if rule_key is None:
        raise ValueError(f"无此曲线表: {sheet}")
    _, filter_by, match, drop = CURVE_RULES[rule_key]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise ValueError(f"模版缺曲线 sheet: {sheet}")
        it = wb[sheet].iter_rows(values_only=True)
        header = list(next(it, None) or [])
        if all(v is None for v in header):
            return {"columns": [], "rows": []}   # 全空 sheet：未收资，空表
        colIdx = {str(c): j for j, c in enumerate(header) if c is not None}
        # 筛选键若不在曲线表头（如此 sheet 无通名列），按未命中处理：传入值非空即 0 命中
        cell = lambda r, c: _s(r[colIdx[c]]) if c in colIdx and colIdx[c] < len(r) else ""
        keep = [c for c in colIdx if c not in filter_by and c not in drop]
        rows, total, prefix_cands = [], 0, set()
        for r in it:
            if r is None or all(v is None for v in r):
                continue
            total += 1
            if match == "whole":
                hit = True
            elif match == "exact":
                hit = all(cell(r, k) == filters.get(k, "") for k in filter_by)
            else:   # prefix：曲线站名须是给定源站名的省略写法（海风←海风2024度夏）
                given = filters.get("电站名称", "")
                if given and cell(r, "电站名称") not in (given,) \
                        and not given.startswith(cell(r, "电站名称")):
                    prefix_cands.add(cell(r, "电站名称"))
                    continue
                hit = True
            if hit:
                rows.append({c: cell(r, c) for c in keep})
        if not rows and not total:
            return {"columns": keep, "rows": []}  # 只表头无数据：未收资，空表
        if match == "prefix" and not rows:
            raise ValueError(
                f"电站 [{filters.get('电站名称', '')}] 在曲线表 [{sheet}] 无对应曲线"
                f"（曲线表现有实体: {sorted(prefix_cands) or '空'}）")
        if not rows:
            raise ValueError(f"按 {filters} 筛曲线表 [{sheet}] 命中 0 行")
        return {"columns": keep, "rows": rows}
    finally:
        wb.close()


if __name__ == "__main__":
    # 自检（docs 新模版 + 旧 20261012 模版都过）：菜单结构、curve 元数据、曲线懒加载
    import json
    import sys

    for path in (sys.argv[1:] or [r"..\docs\数据模版-海南.xlsx"]):
        print(f"===== {path}")
        cats = build_categories(path)
        assert [c["name"] for c in cats] == ["负荷及备用", "电源", "送电协议",
                                             "一体化电站", "检修安排"]
        for c in cats:
            subs = [i["name"] for i in c.get("items", [])]
            print(" ", c["name"], subs or "<tables at this level>")
            if "curve" in c:
                print("    curve:", json.dumps(c["curve"], ensure_ascii=False))
            for i in c.get("items", []):
                if "curve" in i:
                    print(f"    {i['name']}.curve:", json.dumps(i["curve"], ensure_ascii=False))
        # 曲线懒加载三连：负荷（exact）/ 风电（prefix，旧模版）/ 光伏（whole）
        load_rows = read_curve(path, "负荷-8760", {"区域": "海南"})
        assert len(load_rows["rows"]) == 365 and load_rows["columns"][0] == "日期", len(load_rows["rows"])
        print("  负荷-8760(区域=海南) rows =", len(load_rows["rows"]))
        pv = read_curve(path, "光伏-出力曲线", {})
        print("  光伏整表 rows =", len(pv["rows"]))
        try:
            read_curve(path, "风电-出力曲线", {"电站名称": "不存在的站"})
            raise AssertionError("应报 422")
        except ValueError as e:
            print("  风电 0 命中报错 OK:", e)
    print("categories self-check PASS")
