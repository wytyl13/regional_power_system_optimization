#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""算法侧自检（不依赖 FastAPI）：小规模求解 → 恒等式断言 + 输出件比对。

用法（power_opt 目录下）：
    python ../tests/test_algrithm.py          # T=24 全流程自检
断言口径：
    1) 成本分解恒等式（对齐 MATLAB 1312 CostReconciliationError ≈1e-7）：
       六项之和 == 十项之和 == Objective。
    2) 功率平衡残差、22 列 positionChart 堆叠 ≈ 0（solve/compute_metrics 内置）。
    3) 储能首末能量闭合（E_fr 约束保证 == 0；E_ps/E_es 应 ≈ 0）。
    4) 新能源利用率 ∈ [0,1]；C_gen/C_sto 等六项非负。
"""
import os
# 须在 import gurobipy（经 solve 链）之前设好；setdefault 不覆盖外部已设值
os.environ.setdefault("GRB_LICENSE_FILE", r"C:\gurobi1300\win64\bin\gurobi.lic")

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "power_opt"))
sys.path.insert(0, str(HERE.parent / "service"))

import numpy as np

import config
import reader_new
from solve import solve
from solution import compute_metrics, write_result_json
from assemble import build_analysis


def main():
    # 自检固定小规模T=24：不受 config.T 干扰（跑 T=168 大模型须挂大 license 且耗时长）
    config.T = 24
    if config.MIGAP > 0.01:
        config.MIGAP = 0.01
    print(f"自检规模: T={config.T} MIPGap={config.MIGAP}")
    inp = reader_new.load_inputs()
    solution = solve(inp)
    obj = solution.four_items["objective"]["value"]
    if obj is None:
        print("无可行解，自检中止")
        return 1
    metrics = compute_metrics(inp, solution)

    # 1) 成本分解恒等式（MATLAB 1312）
    analysis = build_analysis(inp, solution)
    six_sum = sum(it["costYuan"] for it in analysis["costSummary"]["items"])
    ten_sum = sum(it["costYuan"] for it in analysis["costDetail"]["items"])
    err = analysis["costDetail"]["reconciliationErrorYuan"]
    assert abs(six_sum - obj) < 1e-6 * max(1, abs(obj)), \
        f"六项之和 {six_sum} ≠ Objective {obj}"
    assert abs(ten_sum - obj) < 1e-6 * max(1, abs(obj)), \
        f"十项之和 {ten_sum} ≠ Objective {obj}"
    assert max(abs(e) for e in err) < 1e-4, f"核对差额过大: {err}"
    print(f"成本恒等式 PASS：六项/十项 vs obj 偏差 {err[0]:.3g}/{err[1]:.3g} 元")

    # 2) 平衡残差与堆叠（compute_metrics/plot 内部已断言，这里复核阈值）
    assert metrics["balanceErrorMW"] < 1e-6, metrics["balanceErrorMW"]
    print(f"平衡残差 PASS：{metrics['balanceErrorMW']:.3g} MW")

    # 3) 储能闭合
    closure = metrics["storageClosure"]
    assert closure < 1e-6, f"储能首末能量差 {closure} MWh 超阈"
    print(f"储能闭合 PASS：{closure:.3g} MWh")

    # 4) 利用率界与非负成本
    util = metrics["renewableUtilization"]
    assert np.all((util[~np.isnan(util)] >= 0) & (util[~np.isnan(util)] <= 1)), util
    for it in analysis["costSummary"]["items"]:
        assert it["costYuan"] >= 0, it
    print(f"利用率界与非负成本 PASS：利用率 {util[0] * 100:.2f}%")

    # 落盘全链路（比对件 + 图 CSV + 分析五表序列化）
    write_result_json(inp, solution, config, metrics)
    import plot as PL
    PL.write_chart_csv(inp, solution)
    import json
    path = config.RESULT_DIR / "结果.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) >= {"meta", "report", "positionChart", "time", "labels",
                        "series", "costs"}
    assert len(doc["positionChart"]) == 27, "positionChart 27 列契约"
    analysis_json = json.dumps(analysis, ensure_ascii=False)   # 可序列化
    assert "thermalSummary" in analysis_json and len(
        analysis["thermalSummary"]) == 12 + 3     # 海南煤 12 + 气 3
    print("落盘/序列化 PASS")
    print("---- 全部自检 PASS ----")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
