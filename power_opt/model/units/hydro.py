#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/15 17:00
@Author  : weiyutao
@File    : hydro.py
径流式+库容式水电单元：一个类 Hydro 自包含四步生命周期
（load → add_vars → add_constraints → add_cost）。
机组行来自分区机组信息表[径流式水电/库容式水电]（AreaID 分区列，非 SUBSTR）；
月参数来自算例系统参数[水电月参数]（海南 v2：按水文情景筛行、月值展开为小时，
每台库容水电 × 每个覆盖月份 = 一个调节周期；替代 v1 的水电时序/调节周期两表）。
守卫纪律（坑 4 镜像）：3.4 节 if N_hyd_ror>0 / if N_hyd_res>0 逐行照抄不增不删。
E_hyd_curt 变量无条件建、无条件进目标（第 1 轮坑 2 的根源：空机组表+周期有行
= 无界），靠数据副本保证不发生，本模块不自行兜底。
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
import gurobipy as gp

import config
from power_opt.reader import _read, _zstr, Inputs


@dataclass
class HydInputs:
    """MATLAB 1.17 节工作区变量的镜像；变量名与 MATLAB 一致。"""
    N_hyd_ror: int; N_hyd_res: int; N_hyd_period: int    # 径流台数 / 库容台数 / 调节周期数
    UnitKey_ror: list; UnitKey_res: list                 # 分区唯一编号
    area_hyd_ror: np.ndarray       # 机组 → 分区（0 基）
    area_hyd_res: np.ndarray
    P_hyd_ror_min: np.ndarray; P_hyd_ror_expected: np.ndarray   # N_ror×T，MW，最小/期望出力
    P_hyd_res_min: np.ndarray; P_hyd_res_expected: np.ndarray   # N_res×T，MW
    hydPeriodUnit: np.ndarray      # 周期行 → 库容机组（0 基，MATLAB 439）
    hydPeriodHours: list           # 每周期覆盖的小时序号（0 基，MATLAB 443）
    E_hyd_available: np.ndarray    # 周期可用电量，MWh（MATLAB 444）
    CAP_ror: np.ndarray; CAP_res: np.ndarray          # 装机容量，MW，成本 4.4 用
    FOM_ror: np.ndarray; FOM_res: np.ndarray          # 原表功率运维（未乘折算）


# ------------------- 2.4 决策变量（MATLAB 611-614 行） -------------------

INF = gp.GRB.INFINITY

@dataclass
class HydVars:
    """MATLAB 2.4 节决策变量的镜像，共 4 个（全连续）。"""
    P_hyd_ror: gp.MVar; P_hyd_res: gp.MVar   # 出力，MW
    R_hyd_hot: gp.MVar                        # 库容式热备用，MW
    E_hyd_curt: gp.MVar                       # 周期弃水电量，MWh（N_period×1）


class Hydro:
    """水电单元：inputs/vars 为实例属性，四步生命周期依次组装进模型。"""

    inputs: HydInputs      # 水电输入（load 后生效）
    vars: HydVars          # 水电决策变量（add_vars 后生效）

    def load(self, inp: Inputs) -> None:
        """镜像 MATLAB 1.17 节（海南 v2，402-451）加载水电输入。
        缺列缺值即报错，不补 0；结果存 self.inputs。

        Args:
            inp (Inputs): 系统公共输入。

        Returns:
            None
        """
        req_hyd = ["UnitKey", "AreaID", "CAP", "FOM_COST_PERIOD"]
        rorData = _read(config.UNIT_FILE, "径流式水电", req_hyd)
        resData = _read(config.UNIT_FILE, "库容式水电", req_hyd)
        N_ror, N_res = len(rorData), len(resData)
        areaPos = {a: i for i, a in enumerate(inp.areas.AreaID)}
        zone_ror = [_zstr(v) for v in rorData["AreaID"]]
        zone_res = [_zstr(v) for v in resData["AreaID"]]
        if miss := [z for z in zone_ror if z not in areaPos]:
            raise KeyError(f"径流式水电分区编号 {set(miss)} 不在分区表中")
        if miss := [z for z in zone_res if z not in areaPos]:
            raise KeyError(f"库容式水电分区编号 {set(miss)} 不在分区表中")
        area_ror = np.array([areaPos[z] for z in zone_ror], dtype=int)
        area_res = np.array([areaPos[z] for z in zone_res], dtype=int)
        UnitKey_ror = rorData["UnitKey"].astype(str).tolist()
        UnitKey_res = resData["UnitKey"].astype(str).tolist()

        # ---- 水电月参数（MATLAB 417-435）：按水文情景筛行，月值扩展为本次小时序列 ----
        hydMonthData = _read(config.SYSTEM_FILE, "水电月参数",
                             ["UnitKey", "Hydrology", "Month", "ForcedMW", "ExpectedMW", "AverageMW"])
        hydMonthData = hydMonthData[hydMonthData["Hydrology"].astype(str)
                                    == config.HYDROLOGY_SCENARIO]                # MATLAB 419
        if hydMonthData.empty:
            # MATLAB 419 无守卫：情景名写错会空表静默出错，按数值安全纪律显式报错
            raise KeyError(f"水电月参数无情景 {config.HYDROLOGY_SCENARIO!r} 的行"
                           "（检查 config.HYDROLOGY_SCENARIO 与表内 Hydrology 列）")
        T = inp.time.T
        P_ror_min = np.zeros((N_ror, T)); P_ror_exp = np.zeros((N_ror, T))
        P_res_min = np.zeros((N_res, T)); P_res_exp = np.zeros((N_res, T))
        for keys, mins, exps in ((UnitKey_ror, P_ror_min, P_ror_exp),
                                 (UnitKey_res, P_res_min, P_res_exp)):
            for i, key in enumerate(keys):
                rows = hydMonthData[hydMonthData["UnitKey"].astype(str) == key]
                if rows.empty:
                    raise KeyError(f"水电月参数缺机组 {key} 的行（不补值）")
                mmap = {}                                              # 月 → 行号（重复月即报错）
                for idx, mo in zip(rows.index, rows["Month"].astype(int)):
                    if mo in mmap:
                        raise KeyError(f"水电月参数机组 {key} 的 {mo} 月有重复行")
                    mmap[mo] = idx
                try:
                    sel = rows.loc[[mmap[mo] for mo in inp.time.Time.month]]    # MATLAB 426 ismember
                except KeyError as e:
                    raise KeyError(f"水电月参数缺机组 {key} 的 {e} 月行") from None
                mins[i, :] = sel["ForcedMW"].to_numpy(float)             # MATLAB 427/433
                exps[i, :] = sel["ExpectedMW"].to_numpy(float)           # MATLAB 428/434

        # ---- 调节周期（MATLAB 436-450）：每台库容水电 × 每个覆盖月份一个周期 ----
        hourMonth = inp.time.Time.to_period("M")                             # 每小时所属月
        Month = list(dict.fromkeys(hourMonth))                          # unique stable
        N_period = N_res * len(Month)                                   # MATLAB 438
        hydPeriodUnit = np.zeros(N_period, dtype=int)
        hydPeriodHours = []
        E_hyd_available = np.zeros(N_period)
        for i, key in enumerate(UnitKey_res):                           # MATLAB 442-450
            rows = hydMonthData[hydMonthData["UnitKey"].astype(str) == key]
            avg = dict(zip(rows["Month"].astype(int), rows["AverageMW"].to_numpy(float)))
            for j, mo in enumerate(Month):
                k = i * len(Month) + j
                hydPeriodUnit[k] = i
                hrs = np.where(hourMonth == mo)[0].astype(int)
                hydPeriodHours.append(hrs)
                if mo.month not in avg:
                    raise KeyError(f"水电月参数缺机组 {key} 的 {mo.month} 月 AverageMW 行")
                E_hyd_available[k] = avg[mo.month] * len(hrs) * inp.time.Delta_t

        for name, arr in [("P_hyd_ror_min", P_ror_min), ("P_hyd_ror_expected", P_ror_exp),
                          ("P_hyd_res_min", P_res_min), ("P_hyd_res_expected", P_res_exp),
                          ("E_hyd_available", E_hyd_available)]:
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"{name} 含 NaN/Inf，检查水电输入表")

        self.inputs = HydInputs(
            N_hyd_ror=N_ror, N_hyd_res=N_res, N_hyd_period=N_period,
            UnitKey_ror=UnitKey_ror, UnitKey_res=UnitKey_res,
            area_hyd_ror=area_ror, area_hyd_res=area_res,
            P_hyd_ror_min=P_ror_min, P_hyd_ror_expected=P_ror_exp,
            P_hyd_res_min=P_res_min, P_hyd_res_expected=P_res_exp,
            hydPeriodUnit=hydPeriodUnit, hydPeriodHours=hydPeriodHours,
            E_hyd_available=E_hyd_available,
            CAP_ror=rorData["CAP"].to_numpy(float), CAP_res=resData["CAP"].to_numpy(float),
            FOM_ror=rorData["FOM_COST_PERIOD"].to_numpy(float),
            FOM_res=resData["FOM_COST_PERIOD"].to_numpy(float),
        )
        # HydInputs 字段均已带 T 维度（N×T 矩阵），_T 仅作 add_vars 建形缓存
        self._T = inp.time.T

    def add_vars(self, m: gp.Model) -> HydVars:
        """镜像 MATLAB 2.4 节（611-614）创建水电决策变量，存 self.vars。
        连续变量 lb=-inf 照 MATLAB 611-614；E_hyd_curt 无条件建（坑 2 语义）。

        Args:
            m (gp.Model): Gurobi 模型。

        Returns:
            HydVars: 决策变量容器（同时存 self.vars，两处同引用）。
        """
        hyd = self.inputs
        T = self._T
        P_hyd_ror = m.addMVar((hyd.N_hyd_ror, T), lb=-INF, name="P_hyd_ror")     # 611
        P_hyd_res = m.addMVar((hyd.N_hyd_res, T), lb=-INF, name="P_hyd_res")     # 612
        R_hyd_hot = m.addMVar((hyd.N_hyd_res, T), lb=-INF, name="R_hyd_hot")     # 613
        E_hyd_curt = m.addMVar((hyd.N_hyd_period, 1), lb=-INF, name="E_hyd_curt")  # 614
        self.vars = HydVars(P_hyd_ror=P_hyd_ror, P_hyd_res=P_hyd_res,
                            R_hyd_hot=R_hyd_hot, E_hyd_curt=E_hyd_curt)
        return self.vars

    def add_constraints(self, m: gp.Model, inp: Inputs, sv=None) -> None:
        """一比一对照 MATLAB 3.4 节（801-818）添加水电约束。
        守卫逐行照抄（坑 4）：if N_hyd_ror>0（803）、if N_hyd_res>0（806）；
        P_hyd 聚合表达式（818）留给 context 层。行数账本见 docstring 注释。

        Args:
            m (gp.Model): Gurobi 模型。
            inp (Inputs): 系统公共输入（Delta_t）。
            sv (SystemVars): 系统层决策变量（本单元忽略）。

        Returns:
            None
        """
        hyd = self.inputs
        var = self.vars
        if hyd.N_hyd_ror > 0:                                            # MATLAB 803
            m.addConstr(hyd.P_hyd_ror_min <= var.P_hyd_ror, name="ror_lb")        # 804
            m.addConstr(var.P_hyd_ror <= hyd.P_hyd_ror_expected, name="ror_ub")
        if hyd.N_hyd_res > 0:                                            # MATLAB 806
            m.addConstr(hyd.P_hyd_res_min <= var.P_hyd_res, name="res_lb")        # 807
            m.addConstr(0 <= var.R_hyd_hot, name="res_R_lb")                      # 808
            m.addConstr(var.P_hyd_res + var.R_hyd_hot
                        <= hyd.P_hyd_res_expected, name="res_R_ub")
            for k in range(hyd.N_hyd_period):                            # MATLAB 809
                i = hyd.hydPeriodUnit[k]
                periods = hyd.hydPeriodHours[k]
                # MATLAB 813：E_hyd_used = sum(P+R over 周期时段)*Delta_t（表达式不建行）
                E_used = (var.P_hyd_res[i, periods].sum()
                          + var.R_hyd_hot[i, periods].sum()) * inp.time.Delta_t
                m.addConstr(E_used <= hyd.E_hyd_available[k], name=f"period_budget_{k}")
                m.addConstr(var.E_hyd_curt[k, 0] == hyd.E_hyd_available[k] - E_used,
                            name=f"period_curt_{k}")
                m.addConstr(var.E_hyd_curt[k, 0] >= 0, name=f"curt_lb_{k}")

    def add_cost(self, dt: float, econ) -> dict:
        """镜像 MATLAB 4.4 节（1040-1044）汇总水电成本表达式。
        水电仅 FOM 常数 + 弃水惩罚；原表 FOM 未乘折算，此处乘 fixedCostScale。
        加法顺序冻结。键名是对外成本合同，冻结。

        Args:
            dt (float): 单位步长（小时）。
            econ (EconomicParams): 系统经济参数。

        Returns:
            dict: 水电成本表达式与分项（键名与 solve 层成本 dict 一致）。
        """
        hyd = self.inputs
        C_hyd = float(econ.fixedCostScale
                      * (np.sum(hyd.FOM_ror * hyd.CAP_ror)
                         + np.sum(hyd.FOM_res * hyd.CAP_res)))          # 1042-1043 仅 FOM 常数
        C_hyd_curt = econ.c_hyd_curt * self.vars.E_hyd_curt.sum()           # 1044 弃水惩罚
        return {"C_hyd": C_hyd, "C_hyd_curt": C_hyd_curt}


if __name__ == "__main__":
    import power_opt.reader as reader
    inp = reader.load_inputs()
    hy = Hydro()
    hy.load(inp)
    hyd = hy.inputs
    T = inp.time.T
    fom_const = float(inp.econ.fixedCostScale
                      * (np.sum(hyd.FOM_ror * hyd.CAP_ror)
                         + np.sum(hyd.FOM_res * hyd.CAP_res)))      # MATLAB 1036-1037
    print('输入自检: N_ror=%d, N_res=%d, N_period=%d' %
          (hyd.N_hyd_ror, hyd.N_hyd_res, hyd.N_hyd_period))
    if hyd.N_hyd_ror > 0:
        print('  ror: min[0]=%s expected[0]=%s' %
              (hyd.P_hyd_ror_min[0, :2], hyd.P_hyd_ror_expected[0, :2]))
    if hyd.N_hyd_res > 0:
        print('  res: min[0]=%s expected[0]=%s' %
              (hyd.P_hyd_res_min[0, :2], hyd.P_hyd_res_expected[0, :2]))
    print('  周期: 机组=%d 小时数=%d E_avail=%.2f MWh' %
          (hyd.hydPeriodUnit[0], len(hyd.hydPeriodHours[0]),
           hyd.E_hyd_available[0]))
    print('  C_hyd FOM 常数=%.2f 元, c_hyd_curt=%s 元/MWh' % (fom_const, inp.econ.c_hyd_curt))

    m = gp.Model()
    hy.add_vars(m)
    m.update()
    H_all = sum(len(h) for h in hyd.hydPeriodHours)
    exp_cols = (hyd.N_hyd_ror + 2 * hyd.N_hyd_res) * T + hyd.N_hyd_period
    print('变量列数  期望=%d  实际=%d' % (exp_cols, m.NumVars))
    assert m.NumVars == exp_cols, '变量清单与推导不符'
    print('二值变量  期望=0  实际=%d' % m.NumBinVars)
    assert m.NumBinVars == 0, '水电不应有二值变量'

    hy.add_constraints(m, inp)
    m.update()
    exp_rows = (2 * hyd.N_hyd_ror + 3 * hyd.N_hyd_res) * T + 3 * hyd.N_hyd_period
    exp_nz = (2 * hyd.N_hyd_ror + 4 * hyd.N_hyd_res) * T + 4 * H_all + 2 * hyd.N_hyd_period
    print('水电约束行数  期望=%d  实际=%d' % (exp_rows, m.NumConstrs))
    assert m.NumConstrs == exp_rows, '行数与手推账本不符'
    print('水电非零元  期望=%d  实际=%d' % (exp_nz, m.getAttr('NumNZs')))
    assert m.getAttr('NumNZs') == exp_nz, '非零元与手推账本不符'
    print('水电账本核对通过（MATLAB log 减法参照: +123 行 +242 元，T=24）')
