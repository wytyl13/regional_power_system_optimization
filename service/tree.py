#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File    :tree.py
模版 xlsx → 前端 dataTreeModels 菜单树（结构对齐"新建 文本文档(1).txt"样例：
dataTreeId/dataTreeName/dataTreeOperate/dataTreeTables/dataTreeModels 嵌套，
叶子表 {tableName, tableData:{columns, rows}}，列名=模版 sheet 表头，值全字符串）。
树只含收资数据、只读（dataTreeEdit/dataTreeDelete=False）。
曲线表（8760/288/水文）的 id 为外键=所属实体在源数据表的行号（负荷按 区域、
电站类按 区域+电站名称、送电按 区域+通道名称），同一实体的曲线行共用一个 id；
曲线表按样例剔除 区域/年度/电站名称/通道名称 等标识列（键值仍从这些列取）。
"""
from __future__ import annotations

import datetime

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


def _table(wb, sheet_name: str, table_name: str,
           drop_cols: tuple[str, ...] = (), key_cols: tuple[str, ...] = (),
           id_map: dict | None = None) -> dict | None:
    """模版 sheet → 叶子表。sheet 不存在或全空（无表头）→ None（不出现在树里）。
    id_map 缺省=源数据表：id 按行序 1..N；
    id_map 给定=曲线表：id 回填关联源表行的 id（key_cols=两表共有键列，查不到→空串，
    不造数）；drop_cols=输出剔除的列（曲线表按样例只留 id+日期/时刻/水文年+数值列）。"""
    if sheet_name not in wb.sheetnames:
        return None
    it = wb[sheet_name].iter_rows(values_only=True)
    header = list(next(it, None) or [])
    if all(v is None for v in header):
        return None
    colIdx = {str(c): j for j, c in enumerate(header) if c is not None}
    cols = [c for c in colIdx if c not in drop_cols]   # dict 保序=表头序
    keyIdx = [colIdx[c] for c in key_cols]             # 取键先于剔列（键列本身被 drop）
    rows, i = [], 0
    for r in it:
        if r is None or all(v is None for v in r):
            continue
        i += 1
        cell = lambda j: _s(r[j]) if j < len(r) else ""
        row = {"id": str(i) if id_map is None
               else id_map.get(tuple(cell(j) for j in keyIdx), "")}
        for c in cols:
            row[c] = cell(colIdx[c])
        rows.append(row)
    return {"tableName": table_name, "tableData": {"columns": ["id"] + cols, "rows": rows}}


def _id_map(table: dict | None, key_cols: tuple[str, ...]) -> dict:
    """源数据表 → {键列值元组: 行id}，供对应曲线表回填外键；键含空值的行不入映射。"""
    if not table:
        return {}
    return {tuple(r[c] for c in key_cols): r["id"]
            for r in table["tableData"]["rows"] if all(r[c] for c in key_cols)}


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
        t = lambda s, n, **kw: _table(wb, s, n, **kw)
        # 源数据表先行：曲线表 id 回填其行号（外键），键=两表共有的标识列
        load_src = t("负荷", "源数据表")
        hydro_src = t("常规水电", "源数据表")
        wind_src = t("风电", "源数据表")
        pv_src = t("光伏", "源数据表")
        transfer_src = t("送电协议", "源数据表")
        load_ids = _id_map(load_src, ("区域",))
        hydro_ids = _id_map(hydro_src, ("区域", "电站名称"))
        wind_ids = _id_map(wind_src, ("区域", "电站名称"))
        pv_ids = _id_map(pv_src, ("区域", "电站名称"))
        transfer_ids = _id_map(transfer_src, ("区域", "通道名称"))
        # 样例列名「年用电量」、模版为「用电量」：仅树输出改名（解析层仍读模版列名）
        if load_src and "用电量" in load_src["tableData"]["columns"]:
            td = load_src["tableData"]
            td["columns"] = ["年用电量" if c == "用电量" else c for c in td["columns"]]
            for r in td["rows"]:
                r["年用电量"] = r.pop("用电量")
        # 曲线表参数：剔除标识列；id=源表行号（外键）
        load_curve = dict(drop_cols=("区域", "年度"), key_cols=("区域",), id_map=load_ids)
        station_curve = dict(drop_cols=("区域", "电站名称"), key_cols=("区域", "电站名称"))
        channel_curve = dict(drop_cols=("区域", "通道名称"), key_cols=("区域", "通道名称"))
        return [
            _node("DLGXFX01", "负荷及备用", "1负荷预测.png", children=[
                _node("DLGXFX0101", "负荷", "2负荷.png", tables=[
                    load_src,
                    t("负荷-8760", "曲线-8760", **load_curve),
                    t("负荷-288（基于288可生成8760）", "曲线-288", **load_curve),
                ]),
                _node("DLGXFX0102", "备用", "3备用.png", tables=[t("备用", "源数据表")]),
                _node("DLGXFX0103", "需求响应", "4需求响应.png", tables=[t("需求响应", "源数据表")]),
            ]),
            _node("DLGXFX02", "电源", "5电源.png", children=[
                _node("DLGXFX0201", "核电", "6核电.png", tables=[t("核电", "源数据表")]),
                _node("DLGXFX0202", "抽水蓄能", "7抽水蓄能.png", tables=[t("抽水蓄能", "源数据表")]),
                _node("DLGXFX0203", "常规水电", "8水电.png", tables=[
                    hydro_src,
                    t("常规水电-出力曲线", "水电水文", **station_curve, id_map=hydro_ids),
                ]),
                _node("DLGXFX0204", "气电", "9电气 电力.png", tables=[t("气电", "源数据表")]),
                _node("DLGXFX0205", "生物质", "10生物质厂2.png", tables=[t("生物质", "源数据表")]),
                _node("DLGXFX0206", "煤电", "11燃煤电厂.png",
                      tables=[t("煤电", "源数据表"), t("煤电检修安排", "检修安排")]),
                _node("DLGXFX0207", "风电", "12风电.png", tables=[
                    wind_src,
                    t("风电-出力曲线", "风电-8760", **station_curve, id_map=wind_ids),
                ]),
                _node("DLGXFX0208", "光伏", "13光伏.png", tables=[
                    pv_src,
                    t("光伏-出力曲线", "光伏-8760", **station_curve, id_map=pv_ids),
                ]),
                _node("DLGXFX0210", "新型储能", "15新能源.png", tables=[t("新型储能", "源数据表")]),
            ]),
            _node("DLGXFX04", "送电协议", "20送电.png", tables=[
                transfer_src,
                t("送电协议-送电曲线-8760", "曲线-8760", **channel_curve, id_map=transfer_ids),
                t("送电协议-送电曲线-288（基于288生成8760）", "曲线-288",
                  **channel_curve, id_map=transfer_ids),
            ]),
            _node("DLGXFX05", "一体化电站", "16一体化.png", children=[
                _node("DLGXFX0501", "风光储", "16一体化.png", tables=[t("风光储", "源数据表")]),
                _node("DLGXFX0502", "风光抽", "16一体化.png", tables=[t("风光抽", "源数据表")]),
            ]),
        ]
    finally:
        wb.close()


if __name__ == "__main__":
    # 自检：节点/表数量 + 曲线表外键 id + 值全字符串（海南模版）
    tree = build_data_tree(r"..\数据模版-海南.xlsx")
    nodes = []

    def _walk(ns):
        for n in ns:
            nodes.append(n)
            _walk(n["dataTreeModels"])
    _walk(tree)
    leaves = [t for n in nodes for t in n["dataTreeTables"]]
    assert [n["dataTreeId"] for n in tree] == ["DLGXFX01", "DLGXFX02", "DLGXFX04", "DLGXFX05"]
    load = tree[0]["dataTreeModels"][0]
    src, cur8760, cur288 = load["dataTreeTables"]
    assert src["tableData"]["columns"] == ["id", "大区", "是否有效", "区域", "年度", "最大负荷",
                                           "年用电量", "负荷曲线", "供暖期", "保安开机"]
    assert cur8760["tableData"]["columns"] == ["id", "日期"] + [f"{h}时" for h in range(24)]
    assert cur288["tableData"]["columns"] == ["id", "时刻"] + [f"{m}月" for m in range(1, 13)]
    assert all(isinstance(v, str) for r in cur8760["tableData"]["rows"] for v in r.values())
    # 曲线表 id 外键：只能是同节点源数据表行号或空串（空=曲线引用了源表没有的实体，
    # 不造数）。同 id 行数正常应为单实体曲线行数（8760→365、负荷288→25、水文→6、
    # 送电288→24），异常即模版数据问题，打印供核对不判失败
    for node_name, tbl_name in [("负荷", "曲线-8760"), ("负荷", "曲线-288"),
                                ("风电", "风电-8760"), ("光伏", "光伏-8760"),
                                ("常规水电", "水电水文"), ("送电协议", "曲线-288")]:
        node = next(n for n in nodes if n["dataTreeName"] == node_name)
        cur = next(t for t in node["dataTreeTables"] if t["tableName"] == tbl_name)
        src_ids = {r["id"] for t in node["dataTreeTables"] if t["tableName"] == "源数据表"
                   for r in t["tableData"]["rows"]}
        ids = [r["id"] for r in cur["tableData"]["rows"]]
        assert set(ids) <= src_ids | {""}, f"{tbl_name}: 出现源表没有的 id {set(ids) - src_ids}"
        print(f"{node_name}/{tbl_name}: rows={len(ids)}, "
              f"id行数统计={ {i: ids.count(i) for i in sorted(set(ids))} }")
    print(f"nodes ok; tables={len(leaves)}; "
          f"load8760 rows={len(cur8760['tableData']['rows'])}")
