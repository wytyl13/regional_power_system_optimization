# 区域电力系统优化 · 设计文档

> 本项目唯一设计与规划文件（设计/规划/进度/决策），章节按 skill `design-doc` 固定七章。
> 2026-10-19 回补建档：§1–§5 依据现有代码与 README 回填，历史决策见 git 提交记录，§7 自建档日起只增。

## 1. 定位

> 项目做什么、输入、输出。

电力系统规划课题（课题1）自动求解：MATLAB 优化模型（海南2030算例）复现为 Python（Gurobi）并服务化。
输入：分区机组信息表 / 算例系统参数 / 运行数据 三份 xlsx（服务路径：收资模版经转换器生成三份表）。
输出：各电源决策变量、逐时有功功率与成本（时序 csv / 结果.json / 图表 / 分析件）。

## 2. 需求

> 已确认的需求，分组列出。

- 算法复现：MATLAB → Python，逐位对拍（MPS 逐字节 / 求解四项核对 / 时序逐列比对）
- 服务化：模版上传 → datasetId → 配置 → 提交计算 → 轮询 → 取结果（REST，docs/服务接口设计方案.md）
- 数据契约 v2：五大类 categories + 曲线懒加载 + config 表单 + fixedParams（旧契约 dataTreeModels/fieldsMapping 已废弃）
- 命令行全量出口：main.py 一键求解 + 全量件落盘（结果.json / 设备运行 PNG / 图 CSV / 分析八件套）
- 数据纪律：缺 sheet/列/值即报错不造数；每次求解先核对四项再谈结论

## 3. 数据设计

> 核心数据/对象的格式与约束。

- 输入三份表 sheet 结构：以 reader.py / parse_template.py 实际读取范围为准确界
- 收资模版：数据模版-*.xlsx，23 个 sheet（解析只提取不换算，换算在 convert.py）
- 结果.json（v2 瘦身口径）：meta + four_items + costs + metrics 标量
- 服务契约件：categories/config/fixedParams；solution.json（report 19 字段与 views 9 tab 字段集冻结）
- 结果目录：results/T{T}_gap{MIGAP}_{时间戳}/，分析件在其下 分析报告/{场景}/ 子目录
- 回归容差：数值 rel 1e-9 / abs 1e-6（test_convert 口径）；日期、字符串精确相等

## 4. 结构设计

> 目录布局、模块划分、职责、依赖方向。

```
power_opt/            算法包，唯一显式入口 run.py（RunConfig → run/run_with_outputs）
  reader → model/units/*（每电源一类）→ model/context（跨单元耦合）
  → solve（组装层，全项目唯一调 Gurobi）→ solution/plot/analysis_export（结果层）
service/              FastAPI 服务
  parse_template → convert → datasets/；app/assemble/position_chart 对接 power_opt
  shared/errors 统一异常（BizError 谱系 → HTTP，路由不写 try/except）
tests/                回归与接口测试（test_convert / test_algorithm / test_api_upload / test_api_job）
tools/、docs/temp/    本地草稿（git 忽略，不规范化）
```

依赖单向：入口 → 输入 → 单元 → 系统 → 组装 → 结果；耦合只发生在 model/context。
双入口分工（2026-10-19 归一）：main.py 命令行全量出口（内部调 run.run_with_outputs，只补增量件）；run.py 服务对外唯一显式接口。

## 5. 接口设计

> 对外接口。

- 算法包（唯一显式入口 power_opt/run.py）：`RunConfig` 数据类 + `run()` / `run_with_outputs()` / `last_result_dir()`
- REST（service/app.py）：
  - POST /api/datasets　上传模版 → datasetId + categories + config + fixedParams（不传 file 只回默认配置）
  - GET /api/datasets/{id}/curves/{sheet}　曲线懒加载（filterBy 筛）
  - POST /api/jobs　提交计算 → jobId
  - GET /api/jobs　任务列表（新→旧）
  - GET /api/jobs/{jobId}　状态（queued/running/solved/failed，solved 含四项核对）
  - GET /api/jobs/{jobId}/results　solution.json

## 6. 实施规划与进度

> 宏观步骤表（步骤/状态/验证结果）+ 当前进行到哪。

| 步骤 | 状态 | 验证 |
|---|---|---|
| MATLAB→Python 复现（Step1-9 电源类化、solve 收口） | 已完成 | T24 四项逐位一致；MPS 对拍逐字节一致 |
| 服务化（转换器/骨架/输出，方案A 收口 run.py） | 已完成 | T24 回归逐位过 |
| datasets 接口 v2 契约 | 已完成 | 双模版验证过 |
| solve 重构 v2（Solution/Plot/AnalysisExport/position_chart） | 已完成 | T24 逐字节 + T168 md5 回归过 |
| 代码规范化①：文件头批量同步 + 死代码清理 + 建档 | 已完成 | 2026-10-19，11 文件 diff 过目、py_compile 全过 |
| 代码规范化②：函数 docstring 批量补齐（Args/Returns，约 136 个） | 已完成 | 2026-10-19 三批收口：批1 power_opt 74 处、批2 service 49 处、批3 tests/docs 12 处；py_compile 全过，test_convert 回归 diffs=0 |
| 代码规范化③：遗留三件（测试改名 / 双入口归一 / 文档定稿） | 已完成 | test_algorithm.py 编译过；main.py T24 对拍四项一致（仅 runtime_s 抖动）+ 图 CSV md5 一致 |
| 海南 v2 深度对拍（1e-4 重跑、48h 枯水年回归、.mps diff） | 未开始 | 待排期；.mps diff 子项基准已被 20261020 版取代，见下方分析 |
| 海南 20261020 上游新版分析与同步（v3） | 进行中 | 阶段一（源码整体）四判据全过（2026-10-20，见阶段表下补充）；待用户核对后进阶段二（service） |

当前：v3 阶段一收口——源码改完且对拍新版 MATLAB 四判据全过（规模/24h 四项/168h 目标区间/mat 驱动 Excel 逐 cell），停下等用户核对；阶段二 service 未动。此前代码规范化①②③已收口待提交。

### 2026-10-20 上游新版影响分析（海南2030算例_20261020，对照 20261014 快照）

> 依据：新版自带 修改记录.md（老师定稿）+ 我方逐项 diff 核对（主程序、两本输入表 sheet、xlsx 字节比较）。

**上游变化（三块实质 + 其他）**

1. 模型表达重构（10-15 两轮求解加速，声明可行域与最优值不变）：煤电分段线性由 λ权重+段选择改为增量式 δ+z（`lambda_th`/`segment_th` 变量名废除）；启停 v/w 连续化；储能充放单整数；抽蓄单台分区不建方向整数；新增启停出力有效不等式；绕过 YALMIP 直接调 Gurobi 稀疏矩阵 + 松弛-固定分窗起点（新增 6 个 `rf*` 参数）。主程序 1589→2099 行。效果：168h 182.5s→10.6s；720h 由无可行解变为 1338s 收敛（间隙 0.98%）。
2. 陆风/海风分开建模（10-14，我方快照不含此项）：分区机组信息表、运行数据表各新增"陆上风电/海上风电"页（装机 190 / 4380 MW，合计 4570 = 旧合并页；FOM 均沿用 100000，曲线为 8760h 容量因子），主程序分别建出力与弃风变量，平衡取两者之和；新增 08_陆风海风消纳图与 WindSummary。老师验证陆海之和与原合并输入一致（最大差约 1.8e-12 MW）。原"风电"合并页保留仅为旧版兼容，不再读取。
   输入表逐 cell 对比（2026-10-20 复核）：另发现**核电页 MIN_POWER_GEN 5 行全部减半**（325/325/600/62.5/600，即 10-14"核电最小技术出力恢复为额定 50%"，我方快照早于该改动）；其余 21 页及负荷/风电/光伏曲线逐 cell 一致；算例系统参数、数据模版-海南字节一致。**由此 v2 逐位基准对新输入失效属预期**，对拍一律以新输入下的 MATLAB 实跑为准。数据模版未变，故 service 转换器输入不变，步骤 7 的工作是把模版"风电"页拆分为陆海两页输出（老师 DataStatus 注明来源即模版风电页）。
3. Excel 输出层（10-19 三轮）：新增 `exportExcelResults` 开关 + 模型输出结果.xlsx 模板 + `summarize_hainan_excel`；每次求解输出 25 列"逐小时结果"汇总页（煤气电分开、抽蓄/电化学储能分开）及明细页。
4. 其他：新增 储能初始SOC分析 子目录（其 .m 副本未同步加速修改，修改记录明示）、历史文件 备份、含 720h 的多轮分析结果。

**对本项目的影响**

- 权威基准切换：同步对象由 20261014 版改为 20261020 版（权威顺序：MATLAB 源码 > 推断）。
- v2 对拍结论仍有效的是数学口径（老师声明重构不改可行域与最优值）；逐位口径（MPS、变量名）已失效，须按新版重做——§6 上表 .mps diff 子项由此标注取代。
- 受影响模块：reader（陆海风两 sheet）、model/units（煤电增量式、风电拆分、储能单整数、有效不等式）、solve（两阶段/松弛-固定/矩阵直调）、analysis_export（25 列 Excel 口径）。
- service 层：收资模版到三表的转换与 datasets 契约是否跟进新增 sheet，待定。

**待拍板（拍板后记 §7）**

1. v3 同步范围：仅算法层 / 含 Excel 输出口径 / 含 service 转换与契约。→ **已拍板（2026-10-20）：全量同步，分步走，每步对拍 MATLAB**，步骤表见下。
2. 20261020 文件夹是否 git 入库（当前未跟踪）。→ **已拍板（2026-10-20）：入库但输出件不入库**——源码/输入表/模板/文档提交（commit 2463fce，24 文件），分析结果/、SOC 场景结果与分析图、mat/png/log 经 .gitignore 排除（既有全局模式 + 追加 2 行）。

**v3 同步阶段表（两阶段，每阶段完成即整体对拍、停下等用户核对）**

> 总原则：老师声明模型重构不改可行域与最优值，故判据是"目标值/间隙对齐 + 变量/约束规模与 MATLAB 侧对齐"，不要求解向量逐位相同（MIP 多解）。
> 基准件：本机 MATLAB R2026a + Gurobi 可实跑老师新 .m；分析结果/20300101_0000_168h_20261019_143202_664（终版代码 168h：总成本 25662.5472 万元、间隙 0.9381%、含逐小时 Excel 与 mat）；gurobi_hainan_last.log（变量/约束规模）。

| 阶段 | 范围（一次改完） | 完成后的对拍判据 |
|---|---|---|
| 一：源码（power_opt 整体） | reader 陆海风两页；model 风电拆陆海、煤电增量式 δ+z、启停 v/w 连续、储能单整数、抽蓄方向简化、启停有效不等式；solve 两阶段+松弛-固定+窗口验收；输出层 25 列 Excel、08 消纳图、WindSummary；v2 回归基准按新输入重建 | ① 变量/约束/整数规模 vs 新版 MATLAB 日志（168h 整数 11256 口径）② T24 四项 vs 本机实跑新 .m ③ T168 目标/间隙 vs 20261019 基准（25662.5472 万元 / 0.9381%）④ 载入老师 mat 解驱动 Excel 生成器，与 模型输出结果_逐小时.xlsx 逐 cell 数值比对 |
| 二：service | 转换器把模版"风电"页拆陆海两页输出；datasets 契约/曲线端点暴露新页 | test_convert 旧模版回归 diffs=0；新数据模版全链路（上传→转换→T24 求解）四项一致；既有 API 回归过 |

源码内部施工顺序（仅施工排程，不作为确认节点）：reader → model → solve → 输出层 → 回归基准重建。
范围外暂不动：储能初始SOC分析 子目录（其 .m 副本老师未同步加速修改）、历史文件/ 备份。

**阶段一对拍结果（2026-10-20，全部通过）**

- ① 规模：T168 全模型 51471 列 / 100549 行 / 二值 11256（= MATLAB 168h 日志口径）；各单元类自检全过（煤电 38328 列 / 10080 二值 / 70572 行；气电 9075 行 / 20649 非零——P_gas_max=SU=SD 使紧致不等式退化为 P≤Pmax·u，与 YALMIP 导出一致）。
- ② T24 四项：7383 变量 / 1608 二值 / 14401 约束 / 36229 非零，逐项与 MATLAB 实跑一致；OPTIMAL，obj 33867818.84 元 / gap 0.7914%（MATLAB 33733203.86 / 0.3871%；双方区间互含：Python 下界 3.3600e7 ≤ MATLAB incumbent 3.3733e7 ≤ Python incumbent 3.3868e7）；平衡残差 1.8e-12 MW、储能闭环 0、无失负荷。
- ③ T168：obj 255707485.43 元 / gap 0.5305%（下界 25435.1 万元）；基准 25662.5472 万元 / 0.9381%（下界 25421.7 万元）。Python 区间 [25435.1, 25570.7] ⊂ MATLAB 区间 [25421.7, 25662.5]，且 Python incumbent 更优——模型等价、求解更紧。残差 3.6e-12 MW、无失负荷。
- ④ Excel：载入老师 24h 海南分析结果.mat 解驱动 excel_export（tools/mat_driven_excel_check.py），与老师同次运行 xlsx 逐 cell 比对：mat 支撑的 104 列（煤电/气电全明细、负荷六件、火电状态 8 项、储能 3 组含 SOC、逐小时结果 14 列）全部通过，最大差 3.64e-12；12 个 sheet 顺序/形状/表头一致（此前全量结构对拍已过，数值差异均为不同 incumbent 所致，在 gap 内）。
- 结果件：results/T24_gap0.01_20261020_111528/、results/T168_gap0.01_20261020_112026/（含 分析结果/、模型输出结果_逐小时.xlsx）。
- 补充（2026-10-20 下午）：T=336 实测松弛-固定分窗路径，暴露并修复两处 bug——①`_integer_hour_map` 对三维 z_th 切片得到嵌套 list 不可哈希（改 `block[..., t-1].reshape(-1)`）；②窗口循环不复位上一窗的连续化（第 2 窗当前窗仍松弛，起点带小数被终解拒收；改每窗先按备份复位 VType/lb/ub 再套本窗规则，对齐 MATLAB 每窗模型副本语义），另删去 MATLAB 没有的逐窗喂起点。终版 T336：二值 22512=2×11256 ✓，OPTIMAL，obj 537723656.05 元 / gap 0.6139%，残差 5.5e-12；日志确认第 2 窗超时触发验收→180s 重求通过，终解成功载入起点（5.4511e8→5.3772e8）。该路径 T>168 才触发，T24/T168 对拍结论不受影响。
- 补充（2026-10-20 晚，用户裁决"数据住 Solution"）：`Solution.sheets` 新增为 §12.1 输出数据的唯一驻所（cached_property，`dict[sheet名, DataFrame]`，12 页；index=日期、columns=中文列名、values 纯数值），构建逻辑自 excel_export.py 原样移入（含 pandas dict+MultiIndex 对齐陷阱一处：字符串键对不上元组标签会整页 NaN，改为建帧后原位挂列头）。excel_export.write_excel 瘦身为纯写盘层（签名 `(solution, path)`）；analysis_export 调用点同步改签名。回归：T24 重跑 obj 逐位一致（33867818.8400523），新旧 xlsx 12 sheet 逐格 diffs=0（结果件 results/T24_gap0.01_20261020_152448/）。前端取数路径定为 service 直接消费 `sheets`（`to_dict(orient="split")`，多页出 `[{name, columns, index, data}]` 列表），挑选策略（默认只回逐小时结果、按需加页）留待 service 重构时实施。
- 补充（2026-10-20 晚，用户裁决"只保留第一行表头"）：明细页原第二行口径描述行不再落表，`_page_frame` 改单行列头（口径字符串仅构建期留痕），excel_export 删两级表头分支。此后 xlsx 与 MATLAB 终态不再行对齐，判据④复跑以"去掉口径行后的数据区"为准（tools/mat_driven_excel_check.py 未改，再跑需调整对齐）。回归：T24 obj 逐位一致，基线跳过口径行后 12 sheet / 6665 格 diffs=0（results/T24_gap0.01_20261020_153251/）。

## 7. 决策记录

> 每个拍板：日期/问题/选择/理由。自建档日起只增；此前决策见 git 提交。

| 日期 | 问题 | 选择 | 理由 |
|---|---|---|---|
| 2026-10-19 | 旧文件缺文件头元信息，是否批量回补 | 一次性补齐 11 个已跟踪文件，@Time 取 git 首次提交时刻 | skill `code-header`：用户明示批量同步时补齐并在本文留痕 |
| 2026-10-19 | @File 行后是否空行接正文 | 按 skill 走（加空行）；既有合规旧文件不回改 | 权威顺序：规则文档 > 源码；且旧文件默认不回补 |
| 2026-10-19 | power_opt/reader_bak.py 死代码处置 | 删除 | 全库无 import 引用，新版 reader.py 已上线 |
| 2026-10-19 | DESIGN.md 缺失 | 回补建档（七章固定结构，§1–§5 从代码与 README 回填） | CLAUDE.md / doc-roles / code-header 均引用本文 |
| 2026-10-19 | tests/test_algrithm.py 文件名拼写错误 | git mv 重命名 test_algorithm.py，同步 3 处引用（文件头/用法行/solution.py 注释） | 拼写订正，无行为变化 |
| 2026-10-19 | main.py 与 run.py 双入口 | main.py 收口为调 run.run_with_outputs，只保留全量出口增量件 | 读数/求解/结果.json 单一实现；T24 对拍 obj 逐位一致、图 CSV md5 一致 |
| 2026-10-19 | DESIGN.md §2–§5 冻结区 | 依代码与 README 回填定稿（需求 5 条、数据设计补 2 条、结构补双入口分工） | 用户批准"修改 3"；内容均为实然事实 |
| 2026-10-20 | 海南 20261020 上游新版的同步范围 | 全量同步（算法 + Excel 输出 + service），分八步走，每步对拍 MATLAB、停下等核对后再进下一步 | 用户原话"都要同步，但是你得一步步来，每一步都要确认下同步的结果和原始matlab一致"；权威顺序 MATLAB 源码 > 推断 |
| 2026-10-20 | 20261020 文件夹入库口径 | 入库但输出结果不入库：源码/输入/模板/文档提交，分析结果等输出件走 .gitignore | 用户原话"海南2030算例_20261020入库但是里面的输出结果不入库"；历届算例文件夹此前均未入库（被忽略模式连带），本次为新口径 |
| 2026-10-20 | 同步步进粒度 | 两阶段：源码一次改完→对拍新版 MATLAB 结果；再改 service→测试。此前的八步微步骤方案作废 | 用户原话"先一下改完源码部分，然后改service，源码部分改完了要测试一下和新版本的matlab结果要一致" |
| 2026-10-20 | §12.1 输出数据的存储与解耦 | 数据住 `Solution.sheets`（`dict[sheet名, DataFrame]`，index=日期、values 纯数值矩阵）；excel_export 只写盘；service 只挑选序列化（`to_dict orient="split"`，多页出 list）。dict-of-columns/Series dataclass/object 混型矩阵等方案均否 | 用户：Solution 主职是存储封装输出数据，Excel 只是次要消费者；习惯纯类型矩阵（DL 背景），字符串进数值矩阵不接受 |
| 2026-10-20 | 输出表头行数 | 全部页只保留第一行表头，明细页第二行口径描述不落表（Excel 与前端 JSON 一致）；接受 xlsx 与 MATLAB 终态不再行对齐 | 用户明示"只需要保留第一行作为表头，不要第二行的描述"；对拍口径改为去行后数据区 |
| 2026-10-20 | 逐小时结果汇总口径 | 第三次偏离 summarize 终态：常规水电拆径流式/库容式；风电=陆风+海风、光伏=本体（剔除友好站）；友好电站合并单列（总送出 P_fr_unit）；发电类分项之和=发电汇总。径流式水电装机 0 台按"未装机删除"规则跳过 | 用户明示"常规水电需要进行拆分""还有缺少友好电站相关的数据""友好电站可以合并为一列"；分项语义清晰便于前端展示 |
| 2026-10-20 | 上一条汇总口径拆分 | 当日回退，恢复 MATLAB summarize 终态口径（常规水电合并；风电/光伏含友好站送出与站内充电；友好站不单列）；上一条决策作废 | 用户明示"回退吧，现在不要友好电站了，也不拆分常规水电了"；回归 T24 四项逐位同基线、12 sheet 全一致（results/T24_gap0.01_20261020_163019） |
| 2026-10-21 | 前端交互 JSON 格式（config 表单/提交/曲线/views） | config 下发=三层数组树 `[{group_name,label,data:[{field_name,label,value[,options]}]}]`（units 嵌 sub_group_name；classes 等结构键不下发）；config 提交=裸嵌套 `{组:{键:值}}`（merge_config 原生格式，/api/jobs 零改动）；曲线维持 `{sheet_name,index,data:[{column_name,data}]}`；views 改 `[{sheet_name,index,series_data,lines_data}]`。判别原则：名字是地址（要改要回传）→对象、名字是标签（只读展示）→数组；凝练为 skill `api-json-format` | 用户多轮定稿，末轮原话"好的就这样，我觉得可行"；改动=build_form 重写+sheet_views 键名+两测试契约断言；test_api_upload 顺带补剥统一壳（065c9f5 加壳后未回归）；回归：upload PASS（7 组 98 字段）、job PASS（T24 四项：变量 7383/约束 14401/OPTIMAL/obj 43091610.92 元） |
| 2026-10-21 | /api/datasets 返回字段瘦身 | 8 字段→6：删 fixedParams（纯说明文字，前端从未渲染，口径沉淀于 skill/docs，常量同删）、删 excludedUnits（与 warnings 的 EXCLUDED_UNITS 汇总重复，明细改落服务日志一行）；留 datasetId/area/year/categories/config/warnings | 用户原话"非必要直接删掉啊"；回归 upload PASS |
| 2026-10-21 | categories 结构与 config 同轨 | `[{group_name, data:[{sub_group_name, tables:[{sheet_name,columns,rows}], curve?}]}]`：大类 name→group_name、细分 name→sub_group_name、子级 items→data（判别词统一：有 data=分组）；表名/曲线键统一 sheet_name。**单轨中文名不配 label**——categories 纯只读不回传，名字是标签不是地址，造英文键=造数据；无细分大类（送电协议）本级直接挂 tables/curve | 用户先问"sub_group_name 比 config 少英文字段，前端好操作吗"，确认"名字不回传=不是地址=无需机器键"后原话"好的就这样干"；改动=tree.py 三处键名+upload 测试断言+README 示例+skill api-json-format 增 categories 块 |
