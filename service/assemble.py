#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/13 08:36
@Author  : weiyutao
@File    : assemble.py

service → power_opt 的转发（power_opt 路径注入收口在此，避免双份实现）。
v2（2026-10-16）：导出 AnalysisExport 类（metrics/五表/views/export）替代
原 build_analysis/build_views 函数；服务侧自建实例，不再读 结果.json。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power_opt"))
from analysis_export import AnalysisExport as po_AnalysisExport  # noqa: E402
from run import RunConfig  # noqa: E402
from run import run_with_outputs as po_run  # noqa: E402

__all__ = ["po_AnalysisExport", "RunConfig", "po_run"]
