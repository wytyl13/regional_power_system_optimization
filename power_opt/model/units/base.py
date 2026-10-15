#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/15 14:30
@Author  : weiyutao
@File    : base.py
单元层公共工具：只放跨单元共享的纯函数，不放任何电源逻辑。
"""

from __future__ import annotations

import numpy as np
import gurobipy as gp


def _wsum(w: np.ndarray, X: gp.MVar, T: int):
    """按机组权重对决策变量矩阵加权和求和，即 MATLAB sum(w'*X)。
    w 按 np.repeat 铺 (N,T) 后逐元素乘（沙盒已实测的广播原语）再全求和。

    Args:
        w (np.ndarray): 机组权重向量 (N,)。
        X (gp.MVar): 决策变量矩阵 (N, T)。
        T (int): 调度时段数。

    Returns:
        LinExpr: Σ_{i,t} w_i·X(i,t) 线性表达式。
    """
    W = np.repeat(np.asarray(w)[:, None], T, axis=1)
    return (W * X).sum()
