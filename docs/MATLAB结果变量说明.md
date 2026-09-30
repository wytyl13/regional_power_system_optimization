# MATLAB 结果变量说明

来源：synergy_hainan_30.m 求解后工作区（whos），T=168。
维度约定：逐时段变量 列=小时；行=机组数或分区数（海南算例单分区，分区量只有 1 行）。

| 变量名 | 维度 | 中文描述 |
|---|---|---|
| AreaID | 1x1 string | 分区编号 |
| AreaName | 1x1 string | 分区名称 |
| BalanceResidual | 1x168 | 功率平衡残差（发电+储能净出力+外购+失负荷−修正后负荷），MW |
| E_es_result | 1x169 | 电储能荷电量，T+1 列（第 0 列=初值，第 k 列=第 k 小时末），MWh |
| E_fr_result | 1x169 | 友好电站荷电量，T+1 列，MWh |
| E_ps_result | 1x169 | 抽水蓄能蓄电量，T+1 列，MWh |
| EnergyNotServed | 标量 | 未供电量（失负荷×时长合计），MWh |
| LossOfLoadHours | 标量 | 失负荷小时数，h |
| MaxLoadShortfall | 标量 | 最大负荷缺口（失负荷逐时最大值），MW |
| P_bio_result | 7x168 | 生物质出力（7 台机组），MW |
| P_cut_result | 1x168 | 需求响应削减（分区），MW |
| P_es_charge_result | 1x168 | 电储能充电（1 台机组），MW |
| P_es_discharge_result | 1x168 | 电储能放电（1 台机组），MW |
| P_fr_result | 1x168 | 友好电站出力（全部友好电站聚合到分区），MW |
| P_gas_result | 3x168 | 气电出力（3 台机组），MW |
| P_hyd_result | 1x168 | 水电出力（径流式+库容式聚合到分区），MW |
| P_load | 1x168 | 收资本地负荷（分区，修正前），MW |
| P_nuc_result | 5x168 | 核电出力（5 台机组），MW |
| P_ps_gen_result | 1x168 | 抽水蓄能发电（1 台机组），MW |
| P_ps_pump_result | 1x168 | 抽水蓄能抽水（1 台机组），MW |
| P_pv_curt_result | 1x168 | 弃光（分区），MW |
| P_pv_result | 1x168 | 光伏实际出力（分区），MW |
| P_shed_result | 1x168 | 失负荷（分区；非需求响应削减），MW |
| P_shift_in_result | 1x168 | 需求响应转入（分区），MW |
| P_shift_out_result | 1x168 | 需求响应转出（分区），MW |
| P_th_result | 12x168 | 煤电出力（12 台机组），MW |
| P_wind_curt_result | 1x168 | 弃风（分区），MW |
| P_wind_result | 1x168 | 风电实际出力（分区），MW |
| R_cold_result | 1x168 | 全省冷备用（煤电+气电停机备用），MW |
| R_hot_result | 1x168 | 全省热备用（煤+气+水+抽蓄+电储能旋转备用），MW |
| RenewableUtilization | 标量 | 新能源利用率 |
| Time | 168x1 datetime | 时间轴 |
| TotalCost | 标量 | 目标函数值（总成本），元 |
| report | 1x1 struct | 指标报告（19 字段，对齐上游验证 json；含 balanceErrorMW、hot/coldReserveMarginMW 等） |

补充：修正后负荷 P_D = P_load − P_shift_out + P_shift_in − P_cut（中间量，不在 whos 中）。
