#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/22 14:20
@Author  : weiyutao
@File    : main.py
入口：改 T 在 config.py；结果核对走 solve.solve 的四项打印。
"""
import time

import config
import power_opt.reader as reader
from solve import solve
from solution import write_result_json


if __name__ == "__main__":
    t0 = time.perf_counter()
    inp = reader.load_inputs()
    solution = solve(inp)
    if solution.four_items["objective"]["value"] is not None:
        from analysis_export import AnalysisExport
        ae = AnalysisExport(inp, solution)
        metrics = ae.metrics()
        write_result_json(inp, solution, metrics)
        import plot as PL
        PL.plt.rcParams["font.family"] = ["Microsoft YaHei"]
        PL.plt.rcParams["axes.unicode_minus"] = False
        plot = PL.Plot(inp, solution)
        plot.operation_png(save_png=True)   # 先出 00 堆叠图源
        plot.chart_csv()
        ae.export(metrics)    # 八~十二节：00+7 PNG + .mat + 摘要.txt
    print("---- 本次配置 ----")
    print("时段数 T=%d  起始 %s  步长 %g h  水文情景 %s"
          % (config.T, config.START_TIME, config.DELTA_T, config.HYDROLOGY_SCENARIO))
    print("总耗时: %.1fs" % (time.perf_counter() - t0))
