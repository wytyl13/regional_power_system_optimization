"""Dump ground-truth values from the hand-built 3 xlsx needed to write the converter."""
import pandas as pd

U = r"..\海南2030算例\分区机组信息表.xlsx"
S = r"..\海南2030算例\算例系统参数.xlsx"
O = r"..\海南2030算例\运行数据.xlsx"
out = []

def dump(file, sheet, cols=None, note=""):
    df = pd.read_excel(file, sheet_name=sheet)
    if cols:
        cols = [c for c in cols if c in df.columns]
    else:
        cols = list(df.columns)
    out.append(f"=== [{sheet}] rows={len(df)} {note}")
    out.append("cols: " + ",".join(map(str, cols)))
    for _, r in df.iterrows():
        out.append(" | ".join(f"{c}={r[c]}" for c in cols))
    out.append("")

# unit tables: everything except pure provenance
uCols = ["UnitKey", "NAME", "CAP", "MIN_POWER_GEN", "MIN_DOWN_TIME", "MIN_UP_TIME",
         "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN", "COST_A", "COST_B", "COST_C", "COST_START_UP",
         "COST_SHUT_DOWN", "INIT_STATUS", "INIT_POWER", "INIT_HOURS", "START_RAMP", "STOP_RAMP",
         "FOM_COST_PERIOD", "DERATE_RATE", "SOURCE_MIN_RATIO", "SOURCE_DERATE",
         "CO2_FACTOR", "GEN_COST", "SO2_FACTOR", "NOX_FACTOR", "DUST_FACTOR",
         "INIT_MAINTENANCE", "ENERGY_LIMIT_MWH", "GEN_AFFI_FACTOR", "CHAEGE_AFFI_FACTOR",
         "MIN_POWER_CHARGE", "MAX_POWER_CHARGE"]
for sh in ["煤电", "气电", "核电", "生物质"]:
    dump(U, sh, uCols)

dump(U, "抽水蓄能", ["UnitKey", "NAME", "CAP", "ENERGY_MWH", "SOC_MIN", "SOC_MAX", "SOC_INITIAL",
                    "PUMP_MIN_MW", "FOM_COST_PERIOD", "COST_START_UP", "COST_A", "COST_B", "COST_C",
                    "RAMP_LIMIT_UP", "RAMP_LIMIT_DOWN", "MIN_POWER_GEN", "MIN_POWER_CHARGE", "MAX_POWER_CHARGE"])
dump(U, "电化学储能", None)
dump(U, "新能源友好电站", None)
dump(U, "库容式水电", None)
dump(U, "风电", None)
dump(U, "光伏", None)
dump(U, "分区表", None)
dump(U, "检修计划", ["Year", "Type", "UnitKey", "StartTime", "EndTime", "Enabled", "Note"])

dump(S, "系统参数", None)
dump(S, "省级约束", None)
dump(S, "分区负荷参数", None)
dump(S, "水电月参数", None)
dump(S, "煤电碳排节点", None)
dump(S, "省内断面", None)
dump(S, "省间通道", None)
dump(S, "跨区送电", None)
dump(S, "光热时序", None)

dump(O, "归一化基准", None)
dump(O, "曲线说明", None)

# 需求响应时序: first row of each week to see weekly pattern
df = pd.read_excel(S, sheet_name="需求响应时序")
df["Time"] = pd.to_datetime(df["Time"])
out.append("=== [需求响应时序] 每周首行（看周内是否恒定）rows=" + str(len(df)))
for wk, g in df.groupby(pd.Grouper(key="Time", freq="W-MON", label="left")):
    out.append(f"week {wk.date()}: n={len(g)} first={g.iloc[0]['ShiftOutMax_MW']!r} "
               f"uniq_out={g['ShiftOutMax_MW'].nunique()} uniq_cut={g['CutMax_MW'].nunique()} "
               f"cut_first={g.iloc[0]['CutMax_MW']!r}")
out.append("")

df = pd.read_excel(S, sheet_name="需求响应日限额")
df["Date"] = pd.to_datetime(df["Date"])
out.append("=== [需求响应日限额] rows=" + str(len(df)) + " uniq=" + str(df["CutEnergyMax_MWh"].nunique()))
out.append(str(sorted(df["CutEnergyMax_MWh"].unique())))
out.append("")

df = pd.read_excel(S, sheet_name="友好电站时序")
out.append(f"=== [友好电站时序] rows={len(df)} uniqWind={df['WindAvailable_MW'].nunique()} head:")
out.append(df.head(3).to_string())
out.append("")

with open("ground_truth.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(str(x) for x in out))
print("written", len(out), "lines")
