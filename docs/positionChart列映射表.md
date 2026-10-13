# positionChart 27 列映射表

对齐对象：老版导出件"青海(2030)…工作位置图(平水年)促进消纳-电量.xlsx"27 个数据列。
口径：全系统逐小时，逐机组求和，单位 MW；长度 T 跟随求解窗口。
列名、列序同老版 Excel。映射代码：power_opt/solve.py:405-456；服务层原样透传，不做二次映射。

两条恒等式（自检用，solve.py:449-455 assert）：

- 出力类 14 列之和 ≈ 修正后负荷：核电 + 煤电/气电/生物质最小与可调 + 水电强迫 +
  水电可调 + 风电发电 + 光伏发电 + 抽水蓄能 + 新型储能 + 电量不足
  = 修正后负荷曲线（容差 1e-3）
- 发电曲线 = 修正后负荷曲线 + 电网侧充电量（抽蓄抽水 + 电储能充电）（老版青海导出件实测口径）

## 表一：27 列映射

| # | 列名 | 取数 | 类别 |
|---|---|---|---|
| 1 | 核电 | Σ P_nuc | 直接聚合 |
| 2 | 煤电最小技术出力 | Σ (P_th_min × u_th) | 推导 |
| 3 | 生物质最小技术出力 | Σ (P_bio_min × (1−m_bio)) | 推导 |
| 4 | 气电最小技术出力 | Σ (P_gas_min × u_gas) | 推导 |
| 5 | 水电强迫 | Σ P_hyd_ror | 直接聚合 |
| 6 | 气电可调 | Σ P_gas − 气电最小技术出力列 | 推导 |
| 7 | 煤电可调 | Σ P_th − 煤电最小技术出力列 | 推导 |
| 8 | 生物质可调 | Σ P_bio − 生物质最小技术出力列 | 推导 |
| 9 | 启停调峰气电 | 0 | 补 0：模型无气电分类 |
| 10 | 水电可调 | Σ P_hyd_res | 直接聚合 |
| 11 | 风电发电 | Σ P_wind + Σ P_fr_wind_out | 直接聚合 |
| 12 | 光伏发电 | Σ P_pv + Σ P_fr_pv_out | 直接聚合 |
| 13 | 电量不足 | Σ P_shed | 直接聚合 |
| 14 | 抽水蓄能 | Σ (P_ps_gen − P_ps_pump) | 推导：净值 |
| 15 | 新型储能 | Σ (P_es_dis − P_es_ch) + Σ P_fr_dis | 推导：净值 |
| 16 | 发电曲线 | 修正后负荷曲线列 + Σ P_ps_pump + Σ P_es_ch | 推导：老版恒等式 |
| 17 | 修正后负荷曲线 | Σ P_D = Σ (P_load − P_shift_out + P_shift_in − P_cut) | 直接聚合 |
| 18 | 需求响应 | 本地负荷列 − 修正后负荷曲线列（正=净削减） | 推导 |
| 19 | 送电曲线 | 0 | 补 0：无真实送电数据 |
| 20 | 本地负荷 | Σ P_load | 直接聚合 |
| 21 | 水火备用 | Σ R_th_hot + Σ R_hyd_hot | 推导：仅煤电+库容水 |
| 22 | 旋转备用 | Σ Rhot（各省 R_th+R_gas+R_hyd+R_ps+R_es_hot 合计） | 推导：热备用约束同口径 |
| 23 | 核电压出力 | 0 | 补 0：模型无对应量 |
| 24 | 弃水 | 0 | 补 0：月度电量在 series.E_hyd_curt |
| 25 | 调峰不足 | 0 | 补 0：模型无对应量 |
| 26 | 弃风 | Σ P_wind_curt | 直接聚合 |
| 27 | 弃光 | Σ P_pv_curt | 直接聚合 |

## 表二：字段描述

性质：**决策变量** = `m.addMVar` 建的求解对象（`.X` 取解）；**参数** = 收资/转换层常数
（`np.ndarray`，`inp.`/`_available`/`_min`/`m_` 前缀惯例）；**派生** = 映射代码现算的中间量。
维度 N×T 统一 **行=机组或分区，列=小时**。

| 字段 | 电源/层 | 维度 | 性质 | 含义 | 定义处 | 进哪列 |
|---|---|---|---|---|---|---|
| P_th | 煤电 | N_th×T | 决策（连续） | 实际出力 MW | thermal.py:156 | 煤电可调 |
| u_th | 煤电 | N_th×T | 决策（二值） | 开机状态 1/0 | thermal.py:157 | 煤电最小技术出力 |
| R_th_hot | 煤电 | N_th×T | 决策（连续） | 热备用 MW | thermal.py:162 | 水火备用、旋转备用 |
| R_th_cold | 煤电 | N_th×T | 决策（连续） | 冷备用 MW | thermal.py:163 | 不进 |
| P_th_min | 煤电 | 长度 N_th | **参数** | 单机最小技术出力 | thermal.py load | 煤电最小技术出力 |
| P_th_rated/_max 等 | 煤电 | 长度 N_th | **参数** | 额定/最大出力、成本系数族 | thermal.py load | 不进 |
| P_gas / u_gas | 气电 | N_gas×T | 决策（连续/二值） | 出力 / 开机 | gas.py:130-131 | 气电最小技术出力、气电可调 |
| R_gas_hot / R_gas_cold | 气电 | N_gas×T | 决策 | 热/冷备用 | gas.py:136-137 | 旋转备用 |
| P_gas_min | 气电 | 长度 N_gas | **参数** | 单机最小技术出力 | gas.py load | 气电最小技术出力 |
| P_nuc | 核电 | N_nuc×T | 决策（连续） | 出力（无开机变量，检修定运行） | nuclear.py:102 | 核电 |
| m_nuc | 核电 | N_nuc×T | **参数** | 检修矩阵 1=检修 | nuclear.py load | 不进 |
| P_bio | 生物质 | N_bio×T | 决策（连续） | 出力（无开机变量） | biomass.py:111 | 生物质最小技术出力、生物质可调 |
| m_bio | 生物质 | N_bio×T | **参数** | 检修矩阵 1=检修；u_bio=1−m_bio | biomass.py:70-78 | 生物质最小技术出力 |
| P_bio_min | 生物质 | 长度 N_bio | **参数** | 单机最小技术出力 | biomass.py load | 生物质最小技术出力 |
| P_hyd_ror | 水电 | N_ror×T | 决策（连续） | 径流式出力 | hydro.py:143 | 水电强迫 |
| P_hyd_res | 水电 | N_res×T | 决策（连续） | 库容式出力 | hydro.py:144 | 水电可调 |
| R_hyd_hot | 水电 | N_res×T | 决策（连续） | 库容式热备用 | hydro.py:145 | 水火备用、旋转备用 |
| E_hyd_curt | 水电 | N_period×1 | 决策（连续） | 机组×月弃水电量 MWh | hydro.py:146 | 弃水列补 0，值在 series |
| P_ps_gen / P_ps_pump | 抽蓄 | N_ps×T | 决策（连续） | 发电 / 抽水 | pumped_storage.py:106-107 | 抽水蓄能 |
| R_ps_hot | 抽蓄 | N_ps×T | 决策（连续） | 热备用 | pumped_storage.py:110 | 旋转备用 |
| E_ps | 抽蓄 | N_ps×(T+1) | 决策（连续） | 蓄电量；第 0 列=初值参数钉 | pumped_storage.py:111 | series |
| u_ps_gen / u_ps_pump | 抽蓄 | N_ps×T | 决策（二值） | 发/抽状态（互斥） | pumped_storage.py:108-109 | 不进 |
| P_es_ch / P_es_dis | 新型储能 | N_es×T | 决策（连续） | 充电 / 放电 | electrochemical.py:97-98 | 新型储能 |
| R_es_hot | 新型储能 | N_es×T | 决策（连续） | 热备用 | electrochemical.py:102 | 旋转备用 |
| E_es | 新型储能 | N_es×(T+1) | 决策（连续） | 荷电量 | electrochemical.py:101 | series |
| u_es_ch / u_es_dis | 新型储能 | N_es×T | 决策（二值） | 充/放状态（互斥） | electrochemical.py:99-100 | 不进 |
| P_fr_wind_out / P_fr_pv_out | 友好电站 | N_fr×T | 决策（连续） | 风/光直送电网 | friendly.py:121,124 | 风电发电、光伏发电 |
| P_fr_ch / P_fr_dis | 友好电站 | N_fr×T | 决策（连续） | 站内储能充(不进图)/放 | friendly.py:127-128 | 新型储能（仅放电） |
| P_fr_wind_curt / P_fr_pv_curt | 友好电站 | N_fr×T | 决策（连续） | 站内弃风/弃光 | friendly.py:123,126 | 不进（见口径 1） |
| E_fr | 友好电站 | N_fr×(T+1) | 决策（连续） | 站内荷电量 | friendly.py:131 | series |
| P_fr_wind/pv_available | 友好电站 | N_fr×T | **参数** | 站内风/光可用曲线 | friendly.py load | 不进 |
| P_wind / P_pv | 系统层 | Narea×T | 决策（连续） | 风/光上网出力（无单机变量） | context.py:53-54 | 风电发电、光伏发电 |
| P_wind_curt / P_pv_curt | 系统层 | Narea×T | 决策（连续，非负） | 弃风/弃光；上网+弃=可用 | context.py:55-56 | 弃风、弃光 |
| P_shed | 系统层 | Narea×T | 决策（连续，非负） | 未供电负荷（付惩罚保平衡） | context.py:57 | 电量不足 |
| P_shift_out / P_shift_in / P_cut | 系统层 | Narea×T | 决策（连续，非负） | 需求响应转出/转入/削减 | context.py:59-61 | 修正后负荷曲线、需求响应 |
| P_line | 系统层 | N_line×T | 决策（连续） | 联络线（全系统求和抵消） | context.py:58 | 不进 |
| u_ps_area_gen/pump | 系统层 | Narea×T | 决策（二值） | 分区抽发互斥辅助 | context.py:65-66 | 不进 |
| P_load | 系统层 | Narea×T | **参数** | 收资原始负荷 | reader.py | 需求响应、本地负荷 |
| P_wind_available / P_pv_available | 系统层 | Narea×T | **参数** | 风/光可用出力曲线 | reader.py | 弃电平衡右端 |
| E0 / u_0 / P_0（各类） | 各电源 | 长度 N | **参数** | 初始 SOC/开机/功率，首时段约束用 | 各 load_xxx | 不进 |
| hotReserveRatio / coldReserveRatio / P_week_peak | 系统层 | 各省/全窗 | **参数** | 备用比例、周峰值 | reader.py | report 裕度 |
| P_D | 系统层 | Narea×T | **派生** | 修正后负荷=P_load−out+in−cut | solve.py:333 | 修正后负荷曲线 |
| th_min 等 4 个 _min | 全系统 | T | **派生** | 逐小时最小出力合计（三个"最小技术出力"列的拆段） | solve.py:413-415 | 各最小技术出力列 |
| ps_net / es_net | 全系统 | T | **派生** | 储能净出力 | solve.py:416-417 | 抽水蓄能、新型储能 |
| Rhot / Rcold | 省级 | Nprovince×T | **派生** | A_prov@各类备用合计 | solve.py:398-401 | 旋转备用 |
| A_th…A_fr：机组→分区 0/1 | 系统 | Narea×N类 | **派生** | 关联矩阵 | solve.py:315-323 | 旋转备用（中转） |
| A_prov：分区→省 0/1 | 系统 | Nprovince×Narea | **派生** | 关联矩阵 | solve.py:342-343 | 旋转备用（中转） |
| A_line：线→分区 ±1 | 系统 | Narea×N_line | **派生** | 送端−1/受端+1 | solve.py:324-326 | 不进 |
| ZT | 全系统 | T | **派生** | 补 0 零向量 | solve.py:412 | 启停调峰气电、送电曲线、核电压出力、弃水、调峰不足 |
| PD_tot | 全系统 | T | **派生** | P_D.sum(0)，堆叠自检基准 | solve.py:418 | 发电曲线、修正后负荷曲线 |

## 口径注意（4 条，对账时核对）

| 注意点 |
|---|
| 弃风、弃光两列不含友好电站弃电（P_fr_wind/pv_curt），但 renewableUtilization 指标含 → 图与指标对不上时先查此处 |
| 水火备用只算煤电+库容水；旋转备用含煤+气+水+抽蓄+电储能且经分区→省两级聚合；友好电站不参与热备用（照抄 MATLAB） |
| 需求响应是净值=转出−转入+削减，转入多时为负，正常 |
| 发电曲线 ≠ 各电源出力之和（恒 = 修正后负荷+电网侧充电；含 fr 站内充电与否见 solve.py:405-411 注释） |
