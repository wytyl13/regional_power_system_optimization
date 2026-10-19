# solution 重构设计 v2：第四章口径 + 四层类化

日期：2026-10-16（v2，按用户裁决重排）｜ 依据：`docs/全部模型汇总-0929最新版.docx` 第4章「模型输入与输出变量说明」
状态：**设计稿，待批准后动代码**（黄金规则：改源码前先同意）

v1 要点保留：Solution 是唯一 `.X` 取值出口；变量定义在上游 model 层（units.add_vars +
context.add_vars），solution 只引用句柄取值；第四章全量输出保留（含派生输出）。
v2 变化：compute_metrics 并入 AnalysisExport；positionChart 下放 service；
series/labels 舍弃；结果.json 重定位为算法件。

---

## 一、三个问题的回答（v1 结论，仍有效）

1. **solution 是否牵扯求解变量处理？** 是，且只此一处：`solution.py:32-37` 的
   `_values/_integers` 调 Gurobi `MVar.X` 取值。除此之外不创建、不修改变量/约束。
2. **直接取还是上游定义？** 变量定义本来就在上游 model 层；solution 拿 Vars 句柄
   **引用取值**。边界正确，保持"句柄传进来、取值即走"，不垫新层。
3. **现状问题**：solution.py 四类东西混装——取值（本职）、compute_metrics（分析口径）、
   write_result_json（前端口径）、时序CSV（对拍件）。v2 按"谁消费谁负责"拆到四层。

## 二、四层职责（目标架构）

```
power_opt/solution.py        Solution —— 只取值：第四章全量输出 + 成本 + units/inputs
                              镜像 + 四项核对 + slacks。纯 numpy/dict，无 metrics、
                              无任何前端结构
power_opt/plot.py            Plot(inp, solution) —— §七：00设备堆叠图 + 绘图曲线CSV
power_opt/analysis_export.py AnalysisExport(inp, solution) —— §八~十二：
                              metrics() / 五表 / views(减positionChart) / 01~07图
                              / 海南分析结果.mat / 分析摘要.txt
service/position_chart.py    positionChart 27列 —— 前端独有口径，从 solution.values 组装
```

数据流：

```
solve → Solution(m, inp, insts, Sv, cost_exprs)      # 唯一碰求解器的地方，取完即弃
  ├─ CLI: Plot.operation_png / AnalysisExport.export  # 本地件：CSV/图/.mat/摘要
  └─ service(只经 run.py): run() → (solution, inp)
       ├─ ae = AnalysisExport(inp, solution)          # 经 assemble 转发
       ├─ views = ae.build_views()                    # 透传，不重组
       ├─ positionChart = service侧组装               # 前端独有
       ├─ report = service侧从 four_items+metrics 拼  # 字段冻结不变
       └─ solution.json（前端合同，结构不变）
```

## 三、类定义

```python
class Solution:
    """第四章『模型输入与输出变量说明』各模型输出信息表的取值出口。
    变量定义在上游 model 层；本类只拿 Vars 句柄取 .X，不建变量/约束。
    纯数据载体：属性名沿用原 SolutionData，下游按属性访问不变。"""
    def __init__(self, m, inp, insts, sv, cost_exprs):
        self.four_items / self.values / self.costs / self.units / self.inputs_fields / self.slacks
        # 第四章派生输出以 property 给：核电可运行状态(1-m_nuc)、
        # 友好站对外出力(wind_out+pv_out+discharge)、断面分区送受/净受入(A_line@P_line)、
        # 储能等效循环(放电量/额定能量)
    def to_timeseries_csv(self)     # 对拍硬依赖（compare.py 读），列集=第四章输出展开


class Plot:
    """§七设备运行图：堆叠图 PNG + 绘图曲线 CSV。只读 solution，逻辑逐行不动。"""
    def __init__(self, inp, solution)
    def operation_png(self, save_png=True)
    def chart_csv(self, path=None)


class AnalysisExport:
    """§八~十二分析导出。compute_metrics 并入此处（属分析口径，非取值）。"""
    def __init__(self, inp, solution)
    def metrics(self) -> dict           # 原 compute_metrics（MATLAB 1177-1190 自检）
    def build_analysis(self) -> dict    # 五表（成本/火电/储能/设备/消纳）
    def build_views(self) -> dict       # 9 tab 减 positionChart（前端合同冻结）
    def export(self, metrics=None)      # 01~07 PNG + 海南分析结果.mat + 分析摘要.txt
    # write_mat / write_summary_txt / plot_analysis_figures 收为私有方法，落盘目录不变


# service/position_chart.py
def build_position_chart(inp, solution) -> dict   # 27 列老 Excel 口径，前端独有
```

- 原 `SolutionData` dataclass 撤销，类本身即数据载体。
- 原 `extract_all` 的 19 个位置参数收成 5 个（insts 列表已含全部 inputs/Vars）。
- `.mat` 保存在 AnalysisExport 内（现状 write_mat 已在此文件，目录不变）。

## 四、Solution 是否内置 view 结构

**不内置。** view 是前端合同（冻结但会改版），Solution 是算法资产（对拍基准）；
合同变化不得反向波及算法层。组装放算法侧 `AnalysisExport.build_views()`
（需与 MATLAB 七~十一节图逐参数对拍，属算法口径），service 透传不重组，
只补两个前端独有块：positionChart、report。

## 五、前端返回数据逐项裁决（solution.json 的 result 块）

| 块 | 处置 | 归属 |
| --- | --- | --- |
| views（9 tab，positionChart 除外） | ✅ 保留 | 算法侧组装，service 透传 |
| views.positionChart | ✅ 保留 | **service 侧组装**（老 Excel 27 列，前端独有） |
| report 19 字段 | ✅ 保留（字段冻结） | service 从 four_items + metrics 拼 |
| time | ✅ 保留 | 算法侧（views 轴用） |
| analysis 五表 | ✅ 保留 | 算法侧透传 |
| costs | ✅ 保留 | Solution.costs |
| config | ✅ 保留 | service（已是） |
| series + labels | ❌ **舍弃** | views 定稿后前端不消费；对拍走时序CSV |
| 结果.json 前端块 | ❌ 移出算法侧 | 见下 |

## 六、落盘件归属

| 件 | 产出方 | 消费方 |
| --- | --- | --- |
| 时序结果_T.csv | Solution.to_timeseries_csv | compare.py 对拍（硬门槛） |
| 设备运行_*.png + 设备运行数据_T.csv | Plot | 前端复现/人工 |
| 01~07 分析 PNG | AnalysisExport.export | 人工/MATLAB 对拍 |
| 海南分析结果.mat + 分析摘要.txt | AnalysisExport.export | MATLAB 对拍 |
| 结果.json（瘦身：meta+four_items+costs） | 算法侧 run | compare.py（objectiveYuan 改读 four_items，一行） |
| solution.json | service | 前端（合同冻结，结构不变） |

## 七、第四章输出 ↔ 现有 values 键 对照审计（v1 结论，仍有效）

逐模型核对（docx 转文本 1375-3215 行）：煤电/气电/生物质/径流/库容/电化学/抽蓄/
风光/友好站/核电/断面/需求响应/失负荷——除下述项外全部 ✅ 对上。

**第四章没有、现在多取的 6 键**：F_th（零消费，建议删）；
CO2_th、R_th_cold、R_gas_cold、R_ps_hot、R_es_hot（metrics 自检依赖，留 + 注释分组）。

**缺项**：u_ps_area_gen/pump（第四章抽蓄输出表有、交接文档约定不取）→ 裁决点2；
派生输出（核电可运行状态、友好站对外出力、断面送受/净受入、储能等效循环）→ Solution property。

## 八、迁移步骤（每步过 T24：四项核对 + compare 两段判定）

1. **solution.py 类化**：extract_all → `Solution.__init__`；compute_metrics 迁出；
   write_timeseries_csv → `to_timeseries_csv`；派生输出补 property。
   改引用方 solve.py / run.py / main.py。
2. **plot.py 类化**：`Plot(inp, solution)`，逻辑逐行不动。
3. **analysis_export.py 类化**：compute_metrics 并入为 `metrics()`；五表/views/图/
   mat/摘要收进类；build_views 摘除 positionChart。
4. **service**：新建 position_chart.py（27 列逻辑自 write_result_json 平移）；
   report 组装落 service；result 块舍弃 series/labels。
5. **结果.json 瘦身** + compare.py 一行改读 four_items。
6. **T24 回归**：重构前先留基准副本；CSV 逐字节比 + 四项核对 + solution.json 结构比对。

## 九、裁决记录（2026-10-16 用户已拍板）

1. **F_th**：**不删**，保留取值。
2. **u_ps_area_gen/pump**：**遵第四章补取**（交接文档 TODO 注释随之作废；不进时序CSV，
   CSV 列集是对拍冻结契约）。
3. **series/labels**：**舍弃**（前端确认不消费；结果.json 与 solution.json 均不再产出）。
4. **结果.json 瘦身**：**接受**（meta+four_items+costs+metrics 标量；
   compare.py 改读 four_items.objective.value 与 metrics 块）。
