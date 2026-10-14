#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/13 20:15
@Author  : weiyutao
@File    : run.py
算法包对外唯一入口（显式接口）。

服务与其他调用方只 import 本文件，构造 RunConfig 后调 run()；
不再 sys.path 直插、不再直接改写 config 模块属性（原"方案A"由此收口）。
内部实现不变：仍以 config 模块属性为全局状态（单线程串行下安全），
只是改写动作从调用方移进算法包自己手里。

改写范围与原 service/app.py::_run_job 中方案A完全等价，T24 回归基准不变。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import config
import power_opt.reader as reader
from solution import SolutionData, compute_metrics, write_result_json
from solve import solve


@dataclass
class RunConfig:
    """一次求解的全部可变参数。未给的字段不动 config 现值（命令行跑=全走默认）。"""
    # 输入三件套路径
    unit_file: Path | None = None          # 分区机组信息表.xlsx
    system_file: Path | None = None        # 算例系统参数.xlsx
    profile_file: Path | None = None       # 运行数据.xlsx
    result_dir: Path | None = None         # 输出根目录（默认 power_opt/results）
    # 场景
    T: int | None = None
    start_time: str | None = None          # "2030-01-01 00:00"
    delta_t: float | None = None           # 小时
    hydrology: str | None = None            # 平水年/枯水年
    # 求解器
    migap: float | None = None
    time_limit: float | None = None         # 秒
    # 开关
    use_interprovincial: bool | None = None
    use_fixed_transfer: bool | None = None


def _apply(cfg: RunConfig) -> None:
    """RunConfig → config 模块属性（只改显式给出的字段）。"""
    if cfg.unit_file is not None:
        config.UNIT_FILE = Path(cfg.unit_file)
    if cfg.system_file is not None:
        config.SYSTEM_FILE = Path(cfg.system_file)
    if cfg.profile_file is not None:
        config.PROFILE_FILE = Path(cfg.profile_file)
    if cfg.result_dir is not None:
        config.RESULT_DIR = Path(cfg.result_dir)
    if cfg.T is not None:
        config.T = cfg.T
    if cfg.start_time is not None:
        config.START_TIME = cfg.start_time
    if cfg.delta_t is not None:
        config.DELTA_T = cfg.delta_t
    if cfg.hydrology is not None:
        config.HYDROLOGY_SCENARIO = cfg.hydrology
    if cfg.migap is not None:
        config.MIGAP = cfg.migap
    if cfg.time_limit is not None:
        config.TIME_LIMIT = cfg.time_limit
    if cfg.use_interprovincial is not None:
        config.USE_INTERPROVINCIAL = cfg.use_interprovincial
    if cfg.use_fixed_transfer is not None:
        config.USE_FIXED_TRANSFER = cfg.use_fixed_transfer


def run(cfg: RunConfig) -> SolutionData:
    """应用配置 → 读输入 → 求解。返回 SolutionData（纯数据，不含落盘 json）。

    结果落盘位置：solve() 会把 config.RESULT_DIR 再指到其下
    T{T}_gap{MIGAP}_{时间戳} 子目录并写 csv/log；该子目录可用 last_result_dir() 取回。
    """
    _apply(cfg)
    config.RESULT_DIR = Path(cfg.result_dir) if cfg.result_dir is not None else config.RESULT_DIR
    inp = reader.load_inputs()
    return solve(inp)


def last_result_dir() -> Path:
    """最近一次 run 实际落盘目录（solve 会把 RESULT_DIR 指到时间戳子目录）。
    串行执行下紧跟 run() 调用是安全的。"""
    return Path(config.RESULT_DIR)


def run_with_outputs(cfg: RunConfig) -> tuple[SolutionData, object, dict]:
    """run() + metrics + 结果.json 落盘，一步到位。

    返回 (solution, inp, metrics)——服务层需要 inp/metrics 组装 views/report，
    落盘结果用 last_result_dir() 取，不必摸 config 全局态。
    """
    _apply(cfg)
    inp = reader.load_inputs()
    solution = solve(inp)
    metrics = compute_metrics(inp, solution)
    write_result_json(inp, solution, config, metrics)
    return solution, inp, metrics


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    sol = run(RunConfig())
    print("总耗时: %.1fs（run.py 默认配置直跑）" % (time.perf_counter() - t0))
