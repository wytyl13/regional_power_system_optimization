#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/28 11:30
@Author  : weiyutao
@File    : plot.py
设备运行图：一比一对照 MATLAB §七（synergy_hainan_30.m 1192-1299 行）。
MATLAB 行号注释在每段上方；颜色逐行抄 plotColors，布局抄 tiledlayout/legend 参数。
海南 v2：友好电站 4 列接真值（风/光列=直送+站内充，放电正、充电负）、
'失负荷补偿'→'未供电负荷'、分区图加 if Narea>1 守卫（单分区不出分区图）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")                       # 无屏服务器/命令行环境也可出图
import matplotlib.pyplot as plt

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from power_opt import reader

# MATLAB 1203-1206 plotDeviceNames（21 列，顺序即堆叠顺序）
DEVICE_NAMES = ['煤电', '气电', '生物质', '径流式水电', '库容式水电',
                '核电', '风电', '光伏', '光热', '友好电站风电', '友好电站光伏', '友好电站放电',
                '抽蓄发电', '电化学储能放电', '抽蓄抽水', '电化学储能充电', '友好电站充电',
                '省内净交换', '省间互济', '既定跨区输电', '未供电负荷']
# MATLAB 1207-1214 plotColors（RGB 0-1，逐行抄；充电列与放电列同色，靠正负半轴区分）
DEVICE_COLORS = np.array([
    [0.36, 0.40, 0.46], [0.75, 0.49, 0.32], [0.50, 0.62, 0.32],
    [0.36, 0.72, 0.86], [0.14, 0.48, 0.72], [0.65, 0.51, 0.72],
    [0.30, 0.66, 0.51], [0.95, 0.73, 0.25], [0.89, 0.45, 0.23],
    [0.57, 0.78, 0.64], [0.98, 0.85, 0.49], [0.76, 0.38, 0.55],
    [0.27, 0.42, 0.70], [0.38, 0.69, 0.70], [0.27, 0.42, 0.70],
    [0.38, 0.69, 0.70], [0.76, 0.38, 0.55], [0.66, 0.70, 0.76],
    [0.49, 0.47, 0.65], [0.71, 0.63, 0.48], [0.85, 0.26, 0.30]])


def build_device_data(inp, solution):
    """MATLAB 1188-1196 plotDeviceData：21 列设备×分区×小时，发电正/充电抽水负。
    光热 2026-10-16 接入（A_csp@P_csp_unit；N_csp=0 → 0 列）；
    省间互济/既定跨区：开关关闭 → 0 列（债，待开关打开）。"""
    Z = lambda *shape: np.zeros(shape)                      # 未建模电源列 = 0
    def A(n, area):                                         # 分区归属矩阵 (Narea,n)，同 solve 残差自检
        M = Z(inp.areas.Narea, n); M[area, np.arange(n)] = 1
        return M
    A_line = Z(inp.areas.Narea, inp.lines.N_line)
    A_line[inp.lines.line_from, np.arange(inp.lines.N_line)] = -1
    A_line[inp.lines.line_to, np.arange(inp.lines.N_line)] = 1
    V = solution.values
    area = lambda name: solution.units[name]["area"]
    count = lambda name: len(solution.units[name]["keys"])
    return np.stack([                                     # cat(3,...) 同序
        A(count("thermal"), area("thermal")) @ V["P_th"],    # 煤电
        A(count("gas"), area("gas")) @ V["P_gas"],            # 气电
        A(count("biomass"), area("biomass")) @ V["P_bio"],   # 生物质
        A(count("hydro_ror"), area("hydro_ror")) @ V["P_hyd_ror"],  # 径流式水电
        A(count("hydro_reservoir"), area("hydro_reservoir")) @ V["P_hyd_res"],  # 库容式水电
        A(count("nuclear"), area("nuclear")) @ V["P_nuc"],    # 核电
        V["P_wind"], V["P_pv"],
        A(count("csp"), area("csp")) @ V["P_csp_unit"] if count("csp")
        else Z(inp.areas.Narea, inp.time.T),                    # 风电/光伏/光热（MATLAB 1193）
        A(count("friendly"), area("friendly")) @ (V["P_fr_wind_out"]
                                                  + V["P_fr_wind_ch"]),
        A(count("friendly"), area("friendly")) @ (V["P_fr_pv_out"]
                                                  + V["P_fr_pv_ch"]),
        A(count("friendly"), area("friendly")) @ V["P_fr_discharge"],
        A(count("pumped_storage"), area("pumped_storage")) @ V["P_ps_gen"],
        A(count("electrochemical"), area("electrochemical")) @ V["P_es_discharge"],
        -A(count("pumped_storage"), area("pumped_storage")) @ V["P_ps_pump"],
        -A(count("electrochemical"), area("electrochemical")) @ V["P_es_charge"],
        -A(count("friendly"), area("friendly")) @ V["P_fr_charge"],
        A_line @ V["P_line"], Z(inp.areas.Narea, inp.time.T),           # 省内净交换/省间互济(开关关)
        Z(inp.areas.Narea, inp.time.T), V["P_shed"]], axis=2)            # 既定跨区(开关关)/未供电负荷


def _scopes(inp):
    """MATLAB 1217-1224：作用域列表 [(名称, 1×Narea 汇总矩阵)]。
    全系统 (+多省时省级汇总) + (Narea>1 时各分区)；海南单分区 → 只有全系统。"""
    scopes = [("全系统", np.ones((1, inp.areas.Narea)))]
    if inp.areas.Nprovince > 1:
        A_prov = np.zeros((inp.areas.Nprovince, inp.areas.Narea))
        A_prov[inp.areas.areaProvince, np.arange(inp.areas.Narea)] = 1
        scopes += [(f"{p}省级汇总", A_prov[i:i + 1]) for i, p in enumerate(inp.areas.ProvinceID)]
    if inp.areas.Narea > 1:                                          # 1223：单分区不出分区图
        scopes += [(f"{name}分区", np.eye(inp.areas.Narea)[i:i + 1])
                   for i, name in enumerate(inp.areas.AreaName)]
    return scopes


def write_chart_csv(inp, solution, path=None):
    """绘图曲线 CSV（前端复现 设备运行_*.png 用）：Time + 21 设备列 + 2 条负荷线。
    列名即 PNG 图例文字；充电/抽水/净送出列已是负值，前端正半轴堆正、负半轴堆负即可。
    全 T 行（PNG 只画前 168 小时，前端自行截取）；utf-8-sig 让中文列名 Excel 直开；
    全精度不四舍五入（同其他结果件纪律）。全 0 列（如海南的光热/省间互济）照常输出，
    前端按 PNG 的 visibleDevices 逻辑隐藏即可。"""
    device = build_device_data(inp, solution)
    V = solution.values
    P_D = inp.profiles.P_load - V["P_shift_out"] + V["P_shift_in"] \
        - V["P_cut"]                                        # MATLAB 1240 plotNetLoad
    # 与 plot_operation 同款自检：21 列逐点合计 ≈ 响应后负荷，漏列立刻报警
    assert np.abs(device.sum(axis=2) - P_D).max() < 1e-3, "21 列合计≠响应后负荷：有设备列漏画"
    scopes = _scopes(inp)
    sfx = lambda name: f"{name}_{scopeName}" if len(scopes) > 1 else name
    cols = {"Time": [ts.strftime("%Y-%m-%d %H:%M") for ts in inp.time.Time]}
    for scopeName, M in scopes:                # 每个作用域一组列（海南仅"全系统"一组，列名不带后缀）
        for k, name in enumerate(DEVICE_NAMES):
            cols[sfx(name)] = (M @ device[:, :, k]).ravel()
        cols[sfx("响应后负荷")] = (M @ P_D).ravel()
        cols[sfx("原始负荷")] = (M @ inp.profiles.P_load).ravel()
    if path is None:
        path = config.RESULT_DIR / f"设备运行数据_T{inp.time.T}.csv"
    pd.DataFrame(cols).to_csv(path, index=False, encoding="utf-8-sig")
    print("已导出:", path)


def plot_operation(inp, solution, save_png=False):
    """MATLAB 1217-1299：全系统（+多省时省级，+Narea>1 时各分区）设备运行堆叠图。
    海南单分区 → 只有"全系统"1 张图。"""
    # MATLAB 1195-1196：默认展示前 168 小时
    plotRows = np.arange(0, min(168, inp.time.T))
    plotTime = inp.time.Time[plotRows]
    device = build_device_data(inp, solution)
    P_D = inp.profiles.P_load - solution.values["P_shift_out"] \
        + solution.values["P_shift_in"] - solution.values["P_cut"]
    # 自检：21 列逐点合计 ≈ 响应后负荷（与 solve 残差同一恒等式），漏列立刻报警
    assert np.abs(device.sum(axis=2) - P_D).max() < 1e-3, "21 列合计≠响应后负荷：有设备列漏画"

    # MATLAB 1217-1224：作用域列表（见 _scopes，write_chart_csv 与此处共用一份口径）
    for g, (scopeName, M) in enumerate(_scopes(inp)):
        bars = np.einsum("ar,rtc->tc", M, device[ :, plotRows, :])   # scope×device 汇总
        bars[np.abs(bars) < 1e-6] = 0                          # 1226 浮点噪声置 0
        visible = np.any(np.abs(bars) > 0, axis=0)             # 1227 visibleDevices
        origLoad = (M @ inp.profiles.P_load[:, plotRows]).ravel()        # 1228
        actLoad = (M @ P_D[:, plotRows]).ravel()                # 1229

        fig = plt.figure(figsize=(14.5, 7.0), facecolor="w")     # Position 1450x700
        ax = fig.add_axes([0.07, 0.22, 0.90, 0.68])             # 留出底部 legend 空间
        x = np.arange(1, len(plotRows) + 1)
        if visible.any():                                        # 1232-1238 堆叠柱
            cols = visible.nonzero()[0]                           # visibleDevices 顺序
            names = [DEVICE_NAMES[k] for k in cols]
            colors = [DEVICE_COLORS[k] for k in cols]
            # MATLAB stacked bar：正值列在 0 上方各自堆叠，负值列在 0 下方各自堆叠。
            pos_base = np.zeros(len(x)); neg_base = np.zeros(len(x))
            for j, k in enumerate(cols):
                v = bars[:, k]
                up = np.maximum(v, 0); dn = np.minimum(v, 0)
                ax.bar(x, up, bottom=pos_base, width=0.96, color=colors[j],
                       edgecolor="none", label=names[j])
                ax.bar(x, dn, bottom=neg_base, width=0.96, color=colors[j],
                       edgecolor="none")
                pos_base += up; neg_base += dn
        marker = "o" if len(x) == 1 else None                   # 1245 单小时也可见负荷
        ax.plot(x, actLoad, "-", color=(0.13, 0.16, 0.20), lw=1.8,
                marker=marker, label="响应后负荷")                 # 1247
        if np.any(np.abs(origLoad - actLoad) > 1e-6):           # 1249 响应改了负荷才画
            ax.plot(x, origLoad, "--", color=(0.46, 0.47, 0.49), lw=1.3,
                    marker=marker, label="原始负荷")              # 1250
        ax.axhline(0, color=(0.25, 0.28, 0.32), lw=0.8)          # 1251
        tickRows = np.unique(np.round(np.linspace(0, len(plotRows) - 1,
                                                min(9, len(plotRows)))).astype(int))  # 1252
        ax.set_xticks(tickRows + 1)
        ax.set_xticklabels([t.strftime("%m/%d %H:%M") for t in plotTime[tickRows]])
        ax.set_xlim(0.4, len(plotRows) + 0.6)                    # 1254
        ax.tick_params(direction="out")                          # 1258
        for s in ("top", "right"):                               # Box off
            ax.spines[s].set_visible(False)
        ax.grid(axis="y", alpha=0.12)                            # 1259-1261
        ax.set_axisbelow(False)                                  # Layer top
        ax.set_ylabel("功率 / MW")                                # 1263
        ax.set_xlabel("时间（月/日 时:分）")                      # 1264
        ax.set_title(f"{scopeName}设备运行", fontsize=16, fontweight="bold")  # 1265
        ax.text(0.5, 1.01, "%s 至 %s  |  逐小时出力；充电、抽水及净送出位于负半轴"
                % (plotTime[0].strftime("%Y-%m-%d %H:%M"),
                   plotTime[-1].strftime("%Y-%m-%d %H:%M")),
                transform=ax.transAxes, ha="center", fontsize=10,
                color=(0.40, 0.43, 0.48))                         # 1266-1268 subtitle
        ax.xaxis.label.set_fontsize(11); ax.yaxis.label.set_fontsize(11)
        leg = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=6,
                        frameon=False, fontsize=10)              # 1269-1271
        if save_png:                                             # MATLAB 1294 exportgraphics
            out = config.RESULT_DIR / f"设备运行_{scopeName}.png"
            fig.savefig(out, dpi=300, bbox_inches="tight")
            print("已导出:", out)
        plt.close(fig)


class Plot:
    """§七设备运行图入口（v2 类化）：只读 Solution，包装出图与曲线 CSV。
    模块级 build_device_data/_scopes 保留公用（analysis_export 的 views 设备 tab 同口径）。"""

    def __init__(self, inp, solution):
        self.inp = inp
        self.solution = solution

    def operation_png(self, save_png: bool = True):
        """全系统（+多省/多分区）设备运行堆叠图（MATLAB 1217-1299）。"""
        return plot_operation(self.inp, self.solution, save_png=save_png)

    def chart_csv(self, path=None):
        """绘图曲线 CSV（前端复现 设备运行_*.png 用）：Time + 21 设备列 + 2 条负荷线。"""
        return write_chart_csv(self.inp, self.solution, path)


def main(T: int = 24, save_png: bool = False):
    """跑求解 → 出图。save_png=True 时存 results/设备运行_全系统.png（海南单分区 1 张）。"""
    plt.rcParams["font.family"] = ["Microsoft YaHei"]            # MATLAB 1268 FontName
    plt.rcParams["axes.unicode_minus"] = False
    config.T = T
    from solve import solve
    inp = reader.load_inputs()
    solution = solve(inp)
    if solution.four_items["objective"]["value"] is None:
        print("无可行解，不出图"); return
    plot_operation(inp, solution, save_png=save_png)


if __name__ == "__main__":
    import sys
    T = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    save = (len(sys.argv) > 2 and sys.argv[2] == "save")
    main(T, save_png=save)
