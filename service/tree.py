#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File    : tree.py
模版 xlsx → 前端 dataTreeModels 菜单树（结构对齐"新建 文本文档(1).txt"样例：
dataTreeId/dataTreeName/dataTreeOperate/dataTreeTables/dataTreeModels 嵌套，
叶子表 {tableName, tableData:{columns, rows}}，列名=模版 sheet 表头，值全字符串）。
树只含收资数据、只读（dataTreeEdit/dataTreeDelete=False）。
"""
from __future__ import annotations

import datetime
from pathlib import Path

import openpyxl


def _s(v) -> str:
    """单元格 → 字符串：None→空串；整值浮点去 .0；日期 → YYYY-MM-DD。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d")
    return str(v)


def _table(wb, sheet_name: str, table_name: str) -> dict | None:
    """模版 sheet → 叶子表。sheet 不存在或全空（无表头）→ None（不出现在树里）。"""
    if sheet_name not in wb.sheetnames:
        return None
    it = wb[sheet_name].iter_rows(values_only=True)
    header = list(next(it, None) or [])
    if all(v is None for v in header):
        return None
    cols = [str(c) for c in header if c is not None]
    colIdx = [i for i, c in enumerate(header) if c is not None]
    rows, i = [], 0
    for r in it:
        if r is None or all(v is None for v in r):
            continue
        i += 1
        row = {"id": str(i)}
        for c, j in zip(cols, colIdx):
            row[c] = _s(r[j]) if j < len(r) else ""
        rows.append(row)
    return {"tableName": table_name, "tableData": {"columns": ["id"] + cols, "rows": rows}}


def _node(tree_id: str, name: str, img: str,
          tables: list[dict | None] = (), children: list[dict] = ()) -> dict:
    return {
        "dataTreeBool": True,
        "dataTreeId": tree_id,
        "dataTreeName": name,
        "dataTreeOperate": {
            "dataTreeImg": img, "dataTreeSelImg": img,
            "dataTreeLevel": False, "dataTreeEdit": False, "dataTreeDelete": False,
        },
        "dataTreeTables": [t for t in tables if t],
        "dataTreeModels": list(children),
    }


def build_data_tree(path) -> list[dict]:
    """模版文件 → dataTreeModels 顶层节点列表（负荷及备用/电源/送电协议/一体化电站）。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        t = lambda s, n: _table(wb, s, n)
        return [
            _node("DLGXFX01", "负荷及备用", "1负荷预测.png", children=[
                _node("DLGXFX0101", "负荷", "2负荷.png",
                      tables=[t("负荷", "源数据表"), t("负荷-8760", "曲线-8760")]),
                _node("DLGXFX0102", "备用", "3备用.png", tables=[t("备用", "源数据表")]),
                _node("DLGXFX0103", "需求响应", "4需求响应.png", tables=[t("需求响应", "源数据表")]),
            ]),
            _node("DLGXFX02", "电源", "5电源.png", children=[
                _node("DLGXFX0201", "核电", "6核电.png", tables=[t("核电", "源数据表")]),
                _node("DLGXFX0202", "抽水蓄能", "7抽水蓄能.png", tables=[t("抽水蓄能", "源数据表")]),
                _node("DLGXFX0203", "常规水电", "8水电.png",
                      tables=[t("常规水电", "源数据表"), t("常规水电-出力曲线", "水电水文")]),
                _node("DLGXFX0204", "气电", "9电气 电力.png", tables=[t("气电", "源数据表")]),
                _node("DLGXFX0205", "生物质", "10生物质厂2.png", tables=[t("生物质", "源数据表")]),
                _node("DLGXFX0206", "煤电", "11燃煤电厂.png",
                      tables=[t("煤电", "源数据表"), t("煤电检修安排", "检修安排")]),
                _node("DLGXFX0207", "风电", "12风电.png",
                      tables=[t("风电", "源数据表"), t("风电-出力曲线", "风电-8760")]),
                _node("DLGXFX0208", "光伏", "13光伏.png",
                      tables=[t("光伏", "源数据表"), t("光伏-出力曲线", "光伏-8760")]),
                _node("DLGXFX0210", "新型储能", "15新能源.png", tables=[t("新型储能", "源数据表")]),
            ]),
            _node("DLGXFX04", "送电协议", "20送电.png", tables=[
                t("送电协议", "源数据表"),
                t("送电协议-送电曲线-8760", "曲线-8760"),
                t("送电协议-送电曲线-288（基于288生成8760）", "曲线-288"),
            ]),
            _node("DLGXFX05", "一体化电站", "16一体化.png", children=[
                _node("DLGXFX0501", "风光储", "16一体化.png", tables=[t("风光储", "源数据表")]),
                _node("DLGXFX0502", "风光抽", "16一体化.png", tables=[t("风光抽", "源数据表")]),
            ]),
        ]
    finally:
        wb.close()


if __name__ == "__main__":
    # 自检：节点/表数量 + 抽查一行值全为字符串（海南模版）
    tree = build_data_tree(r"..\数据模版-海南(1).xlsx")
    leaves = []

    def _walk(nodes):
        for n in nodes:
            leaves.extend(n["dataTreeTables"])
            _walk(n["dataTreeModels"])
    _walk(tree)
    assert [n["dataTreeId"] for n in tree] == ["DLGXFX01", "DLGXFX02", "DLGXFX04", "DLGXFX05"]
    coal = next(n for n in tree[1]["dataTreeModels"] if n["dataTreeName"] == "煤电")
    row0 = coal["dataTreeTables"][0]["tableData"]["rows"][0]
    assert all(isinstance(v, str) for v in row0.values()), row0
    load8760 = tree[0]["dataTreeModels"][0]["dataTreeTables"][1]
    assert len(load8760["tableData"]["rows"]) == 365 and "23时" in load8760["tableData"]["columns"]
    print(f"nodes ok; tables={len(leaves)}; coal cols={len(row0)}; "
          f"load8760 rows={len(load8760['tableData']['rows'])}")
