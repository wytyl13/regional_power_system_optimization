#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/22 14:20
@Author  : weiyutao
@File    : main.py
入口：改 T 在 config.py；结果核对走 solve.solve 的四项打印。
"""
import sys

import reader
from solve import solve

if __name__ == "__main__":
    inp = reader.load_inputs()
    m = solve(inp)
