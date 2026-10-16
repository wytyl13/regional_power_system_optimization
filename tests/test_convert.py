#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File    : test_convert.py
转换器回归：convert(数据模版-海南(1).xlsx, 默认config) vs 人工三份表，
逐 sheet、逐列、逐值比对。比对范围 = 转换器生成的全部 sheet（即 reader/model
实际读取范围；说明类 sheet 不生成、不比对）。
容差：数值 rel 1e-9 / abs 1e-6（覆盖浮点求和序差异）；日期、字符串精确相等。
"""
from __future__ import annotations

import math
import numbers
import sys
from pathlib import Path

import pandas as pd

SERVICE = Path(__file__).resolve().parent.parent / "service"
sys.path.insert(0, str(SERVICE))
from convert import convert  # noqa: E402
from parse_template import parse_template  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TPL = ROOT / "海南2030算例20261014" / "海南2030算例" / "数据模版-海南.xlsx"
GT_DIR = ROOT / "海南2030算例20261014" / "海南2030算例"
OUT = SERVICE / "tmp_out"

SHEETS = {
    "分区机组信息表.xlsx": ["分区表", "煤电", "气电", "核电", "生物质", "抽水蓄能",
                            "电化学储能", "新能源友好电站", "库容式水电", "径流式水电",
                            "光热", "风电", "光伏", "检修计划", "供暖期设置"],
    "算例系统参数.xlsx": ["系统参数", "省级约束", "分区负荷参数", "省内断面", "省间通道",
                           "光热时序", "跨区送电", "需求响应时序", "需求响应日限额",
                           "水电月参数", "友好电站时序", "煤电碳排节点"],
    "运行数据.xlsx": ["负荷", "风电", "光伏", "归一化基准"],
}
MAX_REPORT = 400


def _eq(a, b) -> bool:
    if a is None and b is None:
        return True
    if (isinstance(a, numbers.Real) and isinstance(b, numbers.Real)
            and not isinstance(a, bool) and not isinstance(b, bool)):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
    try:
        if pd.isna(a) and pd.isna(b):
            return True
    except (TypeError, ValueError):
        pass
    return a == b


def compare(gt_path: Path, my_path: Path, sheets: list[str], diffs: list[str]):
    for sheet in sheets:
        gt = pd.read_excel(gt_path, sheet_name=sheet)
        mine = pd.read_excel(my_path, sheet_name=sheet)
        gtCols, myCols = list(gt.columns), list(mine.columns)
        if gtCols != myCols:
            miss = [c for c in gtCols if c not in myCols]
            extra = [c for c in myCols if c not in gtCols]
            order = "顺序不一致" if sorted(gtCols) == sorted(myCols) else ""
            diffs.append(f"[{gt_path.name}/{sheet}] 表头差异 miss={miss} extra={extra} {order}")
        if len(gt) != len(mine):
            diffs.append(f"[{gt_path.name}/{sheet}] 行数 gt={len(gt)} mine={len(mine)}")
            continue
        cols = [c for c in gtCols if c in myCols]
        n = 0
        for r in range(len(gt)):
            for c in cols:
                a, b = gt.iat[r, gtCols.index(c)], mine.iat[r, myCols.index(c)]
                if not _eq(a, b):
                    n += 1
                    if len(diffs) < MAX_REPORT:
                        diffs.append(f"[{gt_path.name}/{sheet}] 行{r} 列[{c}]: "
                                     f"gt={a!r} mine={b!r}")
        if n:
            diffs.append(f"[{gt_path.name}/{sheet}] 共 {n} 处值差异")


def main():
    parsed = parse_template(str(TPL))
    summary = convert(parsed, None, OUT)
    diffs: list[str] = []
    for fname, sheets in SHEETS.items():
        compare(GT_DIR / fname, OUT / fname, sheets, diffs)
    report = HERE / "tmp_regression_report.txt"
    report.write_text("\n".join(diffs) if diffs else "ALL PASS", encoding="utf-8")
    print(f"units={summary['unitCounts']}")
    print(f"excluded={len(summary['excluded'])} maintenance={summary['maintenance']}")
    print(f"diffs={len(diffs)} (report: {report.name})")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
