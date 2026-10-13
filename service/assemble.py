"""analysis 五表（MATLAB 八~十一节口径）→ solution.json 前端契约。
表计算本体在 power_opt/analysis_export.py（算法侧 .mat/.txt/PNG 导出共用）；
本文件只做 service → power_opt 的转发，避免双份实现。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power_opt"))
from analysis_export import build_analysis  # noqa: E402
from analysis_export import build_views as po_build_views  # noqa: E402
from run import RunConfig  # noqa: E402
from run import last_result_dir as po_last_result_dir  # noqa: E402
from run import run_with_outputs as po_run  # noqa: E402

__all__ = ["build_analysis", "po_build_views", "RunConfig", "po_run",
           "po_last_result_dir"]
