#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对比两个 Gurobi log 的四件硬证据 + 过程 Info。
用法: python gurobi_log_compare.py matlab.log python.log
"""
import re
import sys

KEYS = [
    ("fingerprint", r"Model fingerprint:\s*(\S+)"),
    ("变量/行/nz",  r"Optimize a model with (\d+) rows, (\d+) columns and (\d+) nonzeros"),
    ("整数/0-1 块", r"Variable types:\s*(\d+) continuous, (\d+) integer \((\d+) binary\)"),
    ("根松弛",      r"Root relaxation:\s*objective\s+([\d.e+-]+)"),
    ("best obj",    r"Best objective\s+([\d.e+-]+)"),
    ("best bound",  r"best bound\s+([\d.e+-]+)"),
    ("gap",         r"gap\s+([\d.e+-]+)%"),
]

def pull(path):
    out = {}
    with open(path, encoding="utf-8", errors="ignore") as f:
        txt = f.read()
    for name, pat in KEYS:
        m = re.search(pat, txt)
        out[name] = m.groups() if m else None
    return out

a, b = pull(sys.argv[1]), pull(sys.argv[2])
hdr = ("指标", "MATLAB 侧", "Python 侧", "一致")
print("%-14s%-28s%-28s%s" % hdr)
print("-" * 78)
for name, _ in KEYS:
    va, vb = a[name], b[name]
    if va is None or vb is None:
        print("%-14s%-28s%-28s缺行" % (name, str(va), str(vb)))
        continue
    same = "✓" if va == vb else "✗"
    print("%-14s%-28s%-28s%s" % (name, str(va), str(vb), same))
