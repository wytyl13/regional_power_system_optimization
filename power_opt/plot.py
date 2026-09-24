#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/09/22 19:40
@Author  : weiyutao
@File    : plot.py
设备运行图：一比一对照 MATLAB §七（synergy_jiangsu_25.m 1186-1292 行）。
MATLAB 行号注释在每段上方；颜色逐行抄 plotColors，布局抄 tiledlayout/legend 参数。
煤电单测：21 列设备数据里只有煤电、省内净交换非零，其余列全 0（visibleDevices 自动隐藏）。
"""
from __future__ import annotations

import numpy as np
import matplotlib
matplotlib.use("Agg")                       # 无屏服务器/命令行环境也可出图
import matplotlib.pyplot as plt

import config
import reader
import solve as S
from model import context as CT
from model.units import thermal as TH

# MATLAB 1199-1210 plotDeviceNames（21 列，顺序即堆叠顺序）
DEVICE_NAMES = ['煤电', '气电', '生物质', '径流式水电', '库容式水电',
                '核电', '风电', '光伏', '光热', '友好电站风电', '友好电站光伏', '友好电站放电',
                '抽蓄发电', '电化学储能放电', '抽蓄抽水', '电化学储能充电', '友好电站充电',
                '省内净交换', '省间互济', '既定跨区输电', '失负荷补偿']
# MATLAB 1212-1217 plotColors（RGB 0-1，逐行抄；充电列与放电列同色，靠正负半轴区分）
DEVICE_COLORS = np.array([
    [0.36, 0.40, 0.46], [0.75, 0.49, 0.32], [0.50, 0.62, 0.32],
    [0.36, 0.72, 0.86], [0.14, 0.48, 0.72], [0.65, 0.51, 0.72],
    [0.30, 0.66, 0.51], [0.95, 0.73, 0.25], [0.89, 0.45, 0.23],
    [0.57, 0.78, 0.64], [0.98, 0.85, 0.49], [0.76, 0.38, 0.55],
    [0.27, 0.42, 0.70], [0.38, 0.69, 0.70], [0.27, 0.42, 0.70],
    [0.38, 0.69, 0.70], [0.76, 0.38, 0.55], [0.66, 0.70, 0.76],
    [0.49, 0.47, 0.65], [0.71, 0.63, 0.48], [0.85, 0.26, 0.30]])


def build_device_data(inp, coal, gas, nuc, bio, ps, es, hyd,
                      Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv):
    """MATLAB 1229-1240 plotDeviceData：21 列设备×分区×小时，发电正/充电抽水负。
    光热/友好电站×4/省间互济/既定跨区：未建模或开关关闭 → 0 列（债，待对应模块接入）。"""
    Z = lambda *shape: np.zeros(shape)                      # 未建模电源列 = 0
    def A(n, area):                                         # 分区归属矩阵 (Narea,n)，同 solve 残差自检
        M = Z(inp.Narea, n); M[area, np.arange(n)] = 1
        return M
    A_line = Z(inp.Narea, inp.N_line)
    A_line[inp.line_from, np.arange(inp.N_line)] = -1
    A_line[inp.line_to, np.arange(inp.N_line)] = 1
    P = lambda mv: np.asarray(mv.X)
    return np.stack([                                     # cat(3,...) 同序
        A(coal.N_th, coal.area_th) @ P(Tv.P_th),             # 煤电
        A(gas.N_gas, gas.area_gas) @ P(Gv.P_gas),            # 气电
        A(bio.N_bio, bio.area_bio) @ P(Bv.P_bio),            # 生物质
        A(hyd.N_hyd_ror, hyd.area_hyd_ror) @ P(Hv.P_hyd_ror),  # 径流式水电
        A(hyd.N_hyd_res, hyd.area_hyd_res) @ P(Hv.P_hyd_res),  # 库容式水电
        A(nuc.N_nuc, nuc.area_nuc) @ P(Nv.P_nuc),            # 核电
        P(Sv.P_wind), P(Sv.P_pv), Z(inp.Narea, inp.T),       # 风电/光伏/光热(未建模)
        Z(inp.Narea, inp.T), Z(inp.Narea, inp.T), Z(inp.Narea, inp.T),  # 友好电站风/光/放电(未建模)
        A(ps.N_ps, ps.area_ps) @ P(Pv.P_ps_gen),             # 抽蓄发电
        A(es.N_es, es.area_es) @ P(Ev.P_es_discharge),       # 电储能放电
        -A(ps.N_ps, ps.area_ps) @ P(Pv.P_ps_pump),           # 抽蓄抽水（负半轴）
        -A(es.N_es, es.area_es) @ P(Ev.P_es_charge),         # 电储能充电（负半轴）
        Z(inp.Narea, inp.T),                                 # 友好电站充电(未建模)
        A_line @ P(Sv.P_line), Z(inp.Narea, inp.T),          # 省内净交换/省间互济(开关关)
        Z(inp.Narea, inp.T), P(Sv.P_shed)], axis=2)          # 既定跨区(开关关)/失负荷


def plot_operation(inp, coal, gas, nuc, bio, ps, es, hyd,
                   Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv, save_png=False):
    """MATLAB 1233-1292：全系统（+多省时省级，+各分区）设备运行堆叠图。
    煤电单测 Nprovince=1 → 只有"全系统 + 5 个分区"共 6 张图。"""
    # MATLAB 1190-1193：默认展示前 168 小时
    plotRows = np.arange(0, min(168, inp.T))
    plotTime = inp.Time[plotRows]
    device = build_device_data(inp, coal, gas, nuc, bio, ps, es, hyd,
                               Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv)
    P_D = inp.P_load - np.asarray(Sv.P_shift_out.X) + np.asarray(Sv.P_shift_in.X) \
        - np.asarray(Sv.P_cut.X)                            # MATLAB 1240 plotNetLoad
    # 自检：21 列逐点合计 ≈ 响应后负荷（与 solve 残差同一恒等式），漏列立刻报警
    assert np.abs(device.sum(axis=2) - P_D).max() < 1e-3, "21 列合计≠响应后负荷：有设备列漏画"

    # MATLAB 1233-1239：作用域 = 全系统 (+省) + 各分区
    scopes = [("全系统", np.ones((1, inp.Narea)))]
    if inp.Nprovince > 1:
        A_prov = np.zeros((inp.Nprovince, inp.Narea))
        A_prov[inp.areaProvince, np.arange(inp.Narea)] = 1
        scopes += [(f"{p}省级汇总", A_prov[i:i + 1]) for i, p in enumerate(inp.ProvinceID)]
    scopes += [(f"{name}分区", np.eye(inp.Narea)[i:i + 1])
               for i, name in enumerate(inp.AreaName)]

    for g, (scopeName, M) in enumerate(scopes):
        bars = np.einsum("ar,rtc->tc", M, device[ :, plotRows, :])   # scope×device 汇总
        bars[np.abs(bars) < 1e-6] = 0                          # 1226 浮点噪声置 0
        visible = np.any(np.abs(bars) > 0, axis=0)             # 1227 visibleDevices
        origLoad = (M @ inp.P_load[:, plotRows]).ravel()        # 1228
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


def main(T: int = 24, save_png: bool = False):
    """跑求解 → 出图。save_png=True 时存 results/设备运行_全系统.png 等 6 张。"""
    plt.rcParams["font.family"] = ["Microsoft YaHei"]            # MATLAB 1257 FontName
    plt.rcParams["axes.unicode_minus"] = False
    config.T = T
    inp = reader.load_inputs()
    m, coal, gas, nuc, bio, ps, es, hyd, Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv = S.solve(inp)
    if m.SolCount == 0:
        print("无可行解，不出图"); return
    plot_operation(inp, coal, gas, nuc, bio, ps, es, hyd,
                   Tv, Gv, Nv, Bv, Pv, Ev, Hv, Sv, save_png=save_png)


if __name__ == "__main__":
    import sys
    from model import context as CT
    T = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    save = (len(sys.argv) > 2 and sys.argv[2] == "save")
    main(T, save_png=save)
