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
| 海南 v2 深度对拍（1e-4 重跑、48h 枯水年回归、.mps diff） | 未开始 | 待排期 |

当前：代码规范化①②③全部收口（文件头 / 135 处 docstring / 遗留三件），等用户核对后提交。

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
