% 海南2030年省级逐小时生产模拟
% 结构：全部输入 → 决策变量 → 分设备约束 → 目标函数 → 求解 → 结果。
% 数据口径和暂未覆盖内容：
% 1. 全省统一分区1（HN），保留自动识别分区逻辑。输入表格与本主程序放在同一文件夹。
% 2. 机组按源表投产时间纳入2030年，规划状态不代表实测运行；所有补充假设见表内参数说明。
% 3. 煤、气、生物质采用非供暖期最小出力率及受阻率。固定运维仍按额定容量计费。
% 4. 水电按用户确认纳入海南，保留原名；月强迫、预想和平均出力直接读取，默认平水年。
%    水库调节系数保留为资料，不重复乘入能量；无水位/水头非线性及跨月水量调配模型。
% 5. 风光储按新能源友好电站单独建模。采用独立列报容量，普通风光和储能表容量不扣减。
%    友好站曲线暂用全省容量因子，站内储能时长参照源新型储能2h，均在表内注明。
% 6. 原负荷8760曲线对应841.03亿kWh，与汇总807亿kWh不同；保留原曲线及14210MW峰值。
% 7. 仅使用2030年检修日历。煤电读取逐日标记；其他非运行周含首尾周，周一至周日。
%    年首和年尾不完整周各计一周，共53周。各类机组周期前检修状态由计划衔接。
% 8. 除省间互济、既定跨区送电开关外，设备及系统约束均直接建立。无资产的设备保留空表。
%    单分区没有省内断面；外部交换缺少正式资料，默认false，不采用源表测试通道。
% 9. 旋转备用10%、停机备用5%取自源表，分别按完整自然周负荷峰值建立约束。
%    碳预算、价格、储能SOC及未收资技术参数属于算例假设。
% 10. 源表无新能源利用率下限，基础值0；保留弃电惩罚并输出实际利用率。
%     下限可在表中修改，应用于当前仿真区间，不能直接套用江苏95%而忽略海南消纳条件。
%     年电量/碳预算在短周期内作可用预算，不自动分摊；连续滚动须维护剩余预算和机组状态。
% 11. 仿真步长1h，首末储能能量相等，最小启停尾端按PDF截断。全年一次求解规模较大。
%     煤电燃料和CO2共享六节点分段线性权重，最小启停使用等价累计表达。
% 12. 当前脚本所有输入、变量、约束、成本、求解和图像都在同一文件。无数据审查前置门槛。
%     输入为直接读取数值；更新容量后须同步更新储能能量、友好站MW时序和煤电碳节点。
% 13. 无外部受电且按原检修计划，部分月份有供电缺口。基础算例允许记录失负荷并高额惩罚，
%     不把缺口当成实际发电；结果输出未供电电量及缺电小时。严格保供时在表中设ShedMaxRatio=0。
clear;
clc;
close all;
tic;

%% 一、输入数据
% -------------------- 1.1 文件、时间及本轮计算设置 --------------------
projectDir = fileparts(mfilename('fullpath')); % 自动定位本脚本目录，整个文件夹可移动
inputDir = projectDir;                      % 直接读取与本脚本同级的三份Excel表格
unitFile = fullfile(inputDir, '分区机组信息表.xlsx');
profileFile = fullfile(inputDir, '运行数据.xlsx');
systemFile = fullfile(inputDir, '算例系统参数.xlsx');

startTime = datetime(2030, 1, 1, 0, 0, 0); % 仿真起始时刻，按整小时设置
hydrologyScenario = "平水年";             % 可改为源表另一个水文情景：枯水年
T = 168;                                % 仿真小时数，可改为24、168、720或8760，时段须在2030年内
Delta_t = 1;                             % 单个时段长度，h
Time = startTime + hours((0:T-1)');        % 每个时段的起点

useInterprovincial = false;               % 省间互济：需两端省份完整输入
useFixedTransfer = false;                 % 是否计入既定跨区送电
solverName = 'gurobi';
solverTimeLimit = 1800;                    % Gurobi求解时限，s；不含前面的读取与建模
solverMIPGap = 0.01;

% 直接按表头读取，不调用数据审查，不补造缺失列。
% 浅黄色为可编辑输入；已补数参数的来源、口径和假设见表内说明，空白不自动当作0。
% 所有已实现模型直接参加求解，输入说明见文件开头。

% -------------------- 1.2 系统经济参数 --------------------
% 算例系统参数.xlsx / 系统参数：Parameter、Value两列，货币统一为元。
% 机组表成本乘costScale转为元；惩罚成本直接采用元/MWh。
systemData = readtable(systemFile, 'Sheet', '系统参数', 'VariableNamingRule', 'preserve');
parameter = string(systemData.Parameter);
costScale = systemData.Value(parameter == "costScaleToYuan");
fixedCostReferenceHours = systemData.Value(parameter == "fixedCostReferenceHours");
fixedCostPeriodRatio = T*Delta_t/fixedCostReferenceHours; % 基准期固定费用折算到本次小时数
fixedCostScale = costScale*fixedCostPeriodRatio;         % 基准期原货币 → 本周期元
c_shed = systemData.Value(parameter == "shedCost");
c_wind_curt = systemData.Value(parameter == "windCurtCost");
c_pv_curt = systemData.Value(parameter == "pvCurtCost");

% 检修为外生计划：按UnitKey匹配，时间区间左闭右开；未安排机组保持可用。
maintenanceData = readtable(unitFile, 'Sheet', '检修计划', 'VariableNamingRule', 'preserve');
maintenanceData = maintenanceData(maintenanceData.Enabled == 1, :);
if isnumeric(maintenanceData.StartTime)
    maintenanceData.StartTime = datetime(maintenanceData.StartTime, 'ConvertFrom', 'excel');
    maintenanceData.EndTime = datetime(maintenanceData.EndTime, 'ConvertFrom', 'excel');
end

% -------------------- 1.3 煤电输入 --------------------
thData = readtable(unitFile, 'Sheet', '煤电', 'VariableNamingRule', 'preserve');
N_th = height(thData);                         % 机组台数
zone_th = string(thData.('SUBSTR(C.ID，12)')); % 原机组表的分区编号
P_th_rated = thData.CAP;             % 额定容量用于固定运维，MW
P_th_max = P_th_rated.*(1-thData.DERATE_RATE); % 非供暖期可用上限，MW
m_th = zeros(N_th, T);                        % 检修输入：1检修，0可用
c_th_FOM = fixedCostScale * thData.FOM_COST_PERIOD; % 本周期元/MW

P_th_min = thData.MIN_POWER_GEN;          % 单机最小技术出力，MW

u_th_0 = thData.INIT_STATUS;                 % 周期前运行状态，0/1
c_th_start = costScale * thData.COST_START_UP; % 单次启动成本，元
c_th_stop = costScale * thData.COST_SHUT_DOWN; % 单次停机成本，元

a_th = costScale * thData.COST_A;           % 燃料成本二次项系数
b_th = costScale * thData.COST_B;           % 燃料成本一次项系数
c_th = costScale * thData.COST_C;           % 燃料成本常数项，运行时计入
% PDF 2.1.1：五段、六节点；每行对应一台煤电机组。
P_th_node = P_th_min + (P_th_max-P_th_min) * (0:5)/5;
F_th_node = a_th .* P_th_node.^2 + b_th .* P_th_node + c_th;
T_th_up = thData.MIN_UP_TIME;           % 最小连续开机小时数
T_th_down = thData.MIN_DOWN_TIME;       % 最小连续停机小时数
T_th_initial = thData.INIT_HOURS;      % 周期前已保持初始状态的小时数

P_th_0 = thData.INIT_POWER;              % 周期前功率，MW
RU_th = thData.RAMP_LIMIT_UP;            % 正常向上爬坡，MW/时段
RD_th = thData.RAMP_LIMIT_DOWN;          % 正常向下爬坡，MW/时段

SU_th = thData.START_RAMP;              % 启动允许功率变化，MW/时段
SD_th = thData.STOP_RAMP;               % 停机允许功率变化，MW/时段

plan_th = maintenanceData(string(maintenanceData.Type) == "煤电", :);
for j = 1:height(plan_th)
    i = find(string(thData.UnitKey) == string(plan_th.UnitKey(j)));
    periods = Time >= plan_th.StartTime(j) & Time < plan_th.EndTime(j);
    m_th(i, periods) = 1;
    if Time(1)-hours(1)>=plan_th.StartTime(j) && Time(1)-hours(1)<plan_th.EndTime(j)
        u_th_0(i) = 0;
        P_th_0(i) = 0;
        T_th_initial(i) = hours(Time(1)-plan_th.StartTime(j));
    end
end

% 煤电六节点排放速率及污染物因子。
coalEmissionData = readtable(systemFile, 'Sheet', '煤电碳排节点', 'VariableNamingRule', 'preserve');
CO2_th_node = nan(N_th,6);                 % t/h，对应P_th_node的六个节点
for i = 1:N_th
    emissionRows = coalEmissionData(string(coalEmissionData.UnitKey)==string(thData.UnitKey(i)),:);
    [~, nodeOrder] = ismember((0:5)', emissionRows.NodeIndex);
    CO2_th_node(i,:) = emissionRows.CO2Rate_t_h(nodeOrder)';
end
SO2_th_factor = thData.SO2_FACTOR;          % t/MWh
NOX_th_factor = thData.NOX_FACTOR;
dust_th_factor = thData.DUST_FACTOR;

% -------------------- 1.4 气电输入 --------------------
gasData = readtable(unitFile, 'Sheet', '气电', 'VariableNamingRule', 'preserve');
N_gas = height(gasData);                         % 机组台数
zone_gas = string(gasData.('SUBSTR(C.ID，12)')); % 原机组表的分区编号
P_gas_rated = gasData.CAP;             % 额定容量用于固定运维，MW
P_gas_max = P_gas_rated.*(1-gasData.DERATE_RATE); % 非供暖期可用上限，MW
m_gas = zeros(N_gas, T);                        % 检修输入：1检修，0可用
c_gas_FOM = fixedCostScale * gasData.FOM_COST_PERIOD; % 本周期元/MW

P_gas_min = gasData.MIN_POWER_GEN;          % 单机最小技术出力，MW

u_gas_0 = gasData.INIT_STATUS;                 % 周期前运行状态，0/1
c_gas_start = costScale * gasData.COST_START_UP; % 单次启动成本，元
c_gas_stop = costScale * gasData.COST_SHUT_DOWN; % 单次停机成本，元

c_gas_gen = costScale * gasData.GEN_COST;       % PDF线性单位发电成本，元/MWh

P_gas_0 = gasData.INIT_POWER;              % 周期前功率，MW
RU_gas = gasData.RAMP_LIMIT_UP;            % 正常向上爬坡，MW/时段
RD_gas = gasData.RAMP_LIMIT_DOWN;          % 正常向下爬坡，MW/时段

SU_gas = gasData.START_RAMP;              % 启动允许功率变化，MW/时段
SD_gas = gasData.STOP_RAMP;               % 停机允许功率变化，MW/时段

T_gas_initial = gasData.INIT_HOURS; % 先读初态，检修衔接时再按已停机小时覆盖
plan_gas = maintenanceData(string(maintenanceData.Type) == "气电", :);
for j = 1:height(plan_gas)
    i = find(string(gasData.UnitKey) == string(plan_gas.UnitKey(j)));
    periods = Time >= plan_gas.StartTime(j) & Time < plan_gas.EndTime(j);
    m_gas(i, periods) = 1;
    if Time(1)-hours(1)>=plan_gas.StartTime(j) && Time(1)-hours(1)<plan_gas.EndTime(j)
        u_gas_0(i) = 0;
        P_gas_0(i) = 0;
        T_gas_initial(i) = hours(Time(1)-plan_gas.StartTime(j));
    end
end

% 气电排放因子及最小开停机时间。
CO2_gas_factor = gasData.CO2_FACTOR;
T_gas_up = gasData.MIN_UP_TIME;
T_gas_down = gasData.MIN_DOWN_TIME;


% -------------------- 1.5 抽水蓄能输入 --------------------
psData = readtable(unitFile, 'Sheet', '抽水蓄能', 'VariableNamingRule', 'preserve');
N_ps = height(psData);                         % 机组台数
zone_ps = string(psData.('SUBSTR(C.ID，12)')); % 原机组表的分区编号
P_ps_max = psData.CAP;                        % 单机额定容量，MW
m_ps = zeros(N_ps, T);                        % 检修输入：1检修，0可用
c_ps_FOM = fixedCostScale * psData.FOM_COST_PERIOD; % 本周期元/MW

% 抽水功率按从电网吸收的正值定义，旧MIN_POWER_CHARGE负值不直接用作下限。
P_ps_pump_min = psData.PUMP_MIN_MW;         % 抽水功率下限，MW
P_ps_pump_max = psData.MAX_POWER_CHARGE;    % 抽水功率上限，MW
eta_ps_gen = psData.GEN_AFFI_FACTOR;        % 发电效率，0至1
eta_ps_pump = psData.CHAEGE_AFFI_FACTOR;    % 抽水效率，沿用原表列名拼写
E_ps_max = psData.ENERGY_MWH;               % 单机对应等效水库能量容量，MWh
SOC_ps_min = psData.SOC_MIN;               % SOC下限
SOC_ps_max = psData.SOC_MAX;               % SOC上限
SOC_ps_initial = psData.SOC_INITIAL;       % 初始SOC
E_ps_initial = E_ps_max .* SOC_ps_initial; % 初始储能量，MWh

plan_ps = maintenanceData(string(maintenanceData.Type) == "抽水蓄能", :);
for j = 1:height(plan_ps)
    i = string(psData.UnitKey) == string(plan_ps.UnitKey(j));
    periods = Time >= plan_ps.StartTime(j) & Time < plan_ps.EndTime(j);
    m_ps(i, periods) = 1;
end

% -------------------- 1.6 核电输入 --------------------
nucData = readtable(unitFile, 'Sheet', '核电', 'VariableNamingRule', 'preserve');
N_nuc = height(nucData);                         % 机组台数
zone_nuc = string(nucData.('SUBSTR(C.ID，12)')); % 原机组表的分区编号
P_nuc_max = nucData.CAP;                        % 单机额定容量，MW
m_nuc = zeros(N_nuc, T);                        % 检修输入：1检修，0可用
c_nuc_FOM = fixedCostScale * nucData.FOM_COST_PERIOD; % 本周期元/MW

P_nuc_min = nucData.MIN_POWER_GEN;          % 单机最小技术出力，MW

c_nuc_gen = costScale * nucData.GEN_COST;       % PDF线性单位发电成本，元/MWh

P_nuc_0 = nucData.INIT_POWER;              % 周期前功率，MW
RU_nuc = nucData.RAMP_LIMIT_UP;            % 正常向上爬坡，MW/时段
RD_nuc = nucData.RAMP_LIMIT_DOWN;          % 正常向下爬坡，MW/时段

m_nuc_0 = nucData.INIT_MAINTENANCE;      % 周期前检修状态，0/1

plan_nuc = maintenanceData(string(maintenanceData.Type) == "核电", :);
for j = 1:height(plan_nuc)
    i = string(nucData.UnitKey) == string(plan_nuc.UnitKey(j));
    periods = Time >= plan_nuc.StartTime(j) & Time < plan_nuc.EndTime(j);
    m_nuc(i, periods) = 1;
    if Time(1)-hours(1)>=plan_nuc.StartTime(j) && Time(1)-hours(1)<plan_nuc.EndTime(j)
        m_nuc_0(i) = 1;
        P_nuc_0(i) = 0;
    end
end

% -------------------- 1.7 生物质输入 --------------------
bioData = readtable(unitFile, 'Sheet', '生物质', 'VariableNamingRule', 'preserve');
N_bio = height(bioData);                         % 机组台数
zone_bio = string(bioData.('SUBSTR(C.ID，12)')); % 原机组表的分区编号
P_bio_rated = bioData.CAP;             % 额定容量用于固定运维，MW
P_bio_max = P_bio_rated.*(1-bioData.DERATE_RATE); % 非供暖期可用上限，MW
m_bio = zeros(N_bio, T);                        % 检修输入：1检修，0可用
c_bio_FOM = fixedCostScale * bioData.FOM_COST_PERIOD; % 本周期元/MW

P_bio_min = bioData.MIN_POWER_GEN;          % 单机最小技术出力，MW

c_bio_gen = costScale * bioData.GEN_COST;       % PDF线性单位发电成本，元/MWh

P_bio_0 = bioData.INIT_POWER;              % 周期前功率，MW
RU_bio = bioData.RAMP_LIMIT_UP;            % 正常向上爬坡，MW/时段
RD_bio = bioData.RAMP_LIMIT_DOWN;          % 正常向下爬坡，MW/时段

SU_bio = bioData.START_RAMP;              % 启动允许功率变化，MW/时段
SD_bio = bioData.STOP_RAMP;               % 停机允许功率变化，MW/时段

m_bio_0 = bioData.INIT_MAINTENANCE;      % 周期前检修状态，0/1

plan_bio = maintenanceData(string(maintenanceData.Type) == "生物质", :);
for j = 1:height(plan_bio)
    i = string(bioData.UnitKey) == string(plan_bio.UnitKey(j));
    periods = Time >= plan_bio.StartTime(j) & Time < plan_bio.EndTime(j);
    m_bio(i, periods) = 1;
    if Time(1)-hours(1)>=plan_bio.StartTime(j) && Time(1)-hours(1)<plan_bio.EndTime(j)
        m_bio_0(i) = 1;
        P_bio_0(i) = 0;
    end
end

E_bio_limit = bioData.ENERGY_LIMIT_MWH;     % 本次可用发电量预算，全年时填年上限

% -------------------- 1.8 风电输入 --------------------
windData = readtable(unitFile, 'Sheet', '风电', 'VariableNamingRule', 'preserve');
windCurve = readtable(profileFile, 'Sheet', '风电', 'VariableNamingRule', 'preserve');
zone_wind = string(windData.('分区编号'));
name_wind = string(windData.('分区名称'));
W_capacity = windData.('装机容量_MW');       % 表内各分区风电装机，MW
c_wind_FOM = fixedCostScale * windData.FOM_COST_PERIOD;

% -------------------- 1.9 光伏输入 --------------------
pvData = readtable(unitFile, 'Sheet', '光伏', 'VariableNamingRule', 'preserve');
pvCurve = readtable(profileFile, 'Sheet', '光伏', 'VariableNamingRule', 'preserve');
zone_pv = string(pvData.('分区编号'));
name_pv = string(pvData.('分区名称'));
PV_capacity = pvData.('装机容量_MW');         % 表内各分区光伏装机，MW
c_pv_FOM = fixedCostScale * pvData.FOM_COST_PERIOD;

% -------------------- 1.10 负荷与曲线时间输入 --------------------
loadData = readtable(profileFile, 'Sheet', '负荷', 'VariableNamingRule', 'preserve');
baseData = readtable(profileFile, 'Sheet', '归一化基准', 'VariableNamingRule', 'preserve');
loadTime = loadData{:, 1};
windTime = windCurve{:, 1};
pvTime = pvCurve{:, 1};
% Excel日期序列转换为datetime；舍入仅消除Excel浮点日期的微小整点误差。
if isnumeric(loadTime)
    loadTime = datetime(loadTime, 'ConvertFrom', 'excel');
end
if isnumeric(windTime)
    windTime = datetime(windTime, 'ConvertFrom', 'excel');
end
if isnumeric(pvTime)
    pvTime = datetime(pvTime, 'ConvertFrom', 'excel');
end
loadTime = dateshift(loadTime, 'start', 'hour', 'nearest');
windTime = dateshift(windTime, 'start', 'hour', 'nearest');
pvTime = dateshift(pvTime, 'start', 'hour', 'nearest');
[~, loadRows] = ismember(Time, loadTime);
[~, windRows] = ismember(Time, windTime);
[~, pvRows] = ismember(Time, pvTime);

% -------------------- 1.11 分区及机组-分区对应关系 --------------------
% 汇集各类机组编号，自动得到分区数；编号不必连续，也不固定为5个。
areaData = readtable(unitFile, 'Sheet', '分区表', 'VariableNamingRule', 'preserve');
AreaID = unique([string(areaData.AreaID); zone_th; zone_gas; zone_ps; zone_nuc; zone_bio; zone_wind; zone_pv], 'stable');
% 分区表包括只有水电、储能、光热、友好电站或负荷的分区，不按设备数量推定分区数。
Narea = numel(AreaID);
% 分区表统一提供编号和名称；运行曲线按名称匹配，不依赖Excel列顺序。
mapID = string(areaData.AreaID);
mapName = string(areaData.AreaName);
[~, nameIndex] = ismember(AreaID, mapID);
AreaName = mapName(nameIndex);
[~, area_th] = ismember(zone_th, AreaID);
[~, area_gas] = ismember(zone_gas, AreaID);
[~, area_ps] = ismember(zone_ps, AreaID);
[~, area_nuc] = ismember(zone_nuc, AreaID);
[~, area_bio] = ismember(zone_bio, AreaID);
[~, area_wind] = ismember(zone_wind, AreaID); % 风电分区→AreaID顺序
[~, area_pv] = ismember(zone_pv, AreaID);     % 光伏分区→AreaID顺序
A_th = sparse(area_th, (1:N_th)', ones(N_th,1), Narea, N_th);
A_gas = sparse(area_gas, (1:N_gas)', ones(N_gas,1), Narea, N_gas);
A_ps = sparse(area_ps, (1:N_ps)', ones(N_ps,1), Narea, N_ps);
A_nuc = sparse(area_nuc, (1:N_nuc)', ones(N_nuc,1), Narea, N_nuc);
A_bio = sparse(area_bio, (1:N_bio)', ones(N_bio,1), Narea, N_bio);

% -------------------- 1.12 分区风电、光伏、负荷功率 --------------------
% 风电、光伏直接采用源容量因子，不再按全年峰值重归一化；乘以分区装机容量，
% 负荷乘以“归一化基准”得到2030规划负荷MW。
P_wind_available = zeros(Narea, T);
P_pv_available = zeros(Narea, T);
P_load = zeros(Narea, T);
W_cap_area = zeros(Narea,1); W_cap_area(area_wind) = W_capacity;  % 分区风电装机，MW
PV_cap_area = zeros(Narea,1); PV_cap_area(area_pv) = PV_capacity; % 分区光伏装机，MW
for r = 1:Narea
    P_wind_available(r,:) = windCurve{windRows, char(AreaName(r))}' * W_cap_area(r);
    P_pv_available(r,:) = pvCurve{pvRows, char(AreaName(r))}' * PV_cap_area(r);
    base_load = baseData{string(baseData{:,1}) == "负荷" & string(baseData{:,2}) == AreaName(r), 3};
    P_load(r,:) = loadData{loadRows, char(AreaName(r))}' * base_load;
end

% -------------------- 1.13 省内断面输入 --------------------
lineData = readtable(systemFile, 'Sheet', '省内断面', 'VariableNamingRule', 'preserve');
lineData = lineData(lineData.Enabled == 1, :);
N_line = height(lineData);
[~, area_from] = ismember(string(lineData.FromArea), AreaID);
[~, area_to] = ismember(string(lineData.ToArea), AreaID);
% 参考方向：送端-1、受端+1；省内交换不改变全省功率总量。
A_line = sparse([area_from;area_to], [(1:N_line)';(1:N_line)'], ...
    [-ones(N_line,1);ones(N_line,1)], Narea, N_line);
P_line_forward = lineData.ForwardMW;
P_line_reverse = lineData.ReverseMW;

% -------------------- 1.14 既定跨区送电输入 --------------------
P_trans = zeros(Narea, T);                 % 正受入、负送出，输入值不参与优化
if useFixedTransfer
    transData = readtable(systemFile, 'Sheet', '跨区送电', 'VariableNamingRule', 'preserve');
    transTime = transData{:,1};
    if isnumeric(transTime)
        transTime = datetime(transTime, 'ConvertFrom', 'excel');
    end
    transTime = dateshift(transTime, 'start', 'hour', 'nearest');
    [~, transRows] = ismember(Time, transTime);
    for r = 1:Narea
        P_trans(r,:) = transData{transRows, char(AreaName(r))}';
    end
end
% -------------------- 1.15 省级参数及经济参数 --------------------
provinceData = readtable(systemFile, 'Sheet', '省级约束', 'VariableNamingRule', 'preserve');
ProvinceID = unique(string(areaData.ProvinceID(nameIndex)), 'stable');
Nprovince = numel(ProvinceID);
[~, provinceRows] = ismember(ProvinceID, string(provinceData.ProvinceID));
provinceData = provinceData(provinceRows,:);
[~, areaProvince] = ismember(string(areaData.ProvinceID(nameIndex)), ProvinceID);
A_province = sparse(areaProvince, (1:Narea)', 1, Nprovince, Narea);
reserveDuration = provinceData.ReserveDuration_h;
adequacyFactor = provinceData.AdequacyFactor;
coldReserveRatio = provinceData.ColdReserveRatio;
hotReserveRatio = provinceData.HotReserveRatio; % 源旋转备用10%，独立约束
renewableUtilizationMin = provinceData.RenewableUtilizationMin;
CO2Limit = provinceData.CO2Limit_t;
c_hyd_curt = systemData.Value(parameter == "hydroCurtCost");
c_CO2 = systemData.Value(parameter == "co2Price");
c_SO2 = systemData.Value(parameter == "so2Price");
c_NOX = systemData.Value(parameter == "noxPrice");
c_dust = systemData.Value(parameter == "dustPrice");

% -------------------- 1.16 电化学储能输入 --------------------
esData = readtable(unitFile, 'Sheet', '电化学储能', 'VariableNamingRule', 'preserve');
N_es = height(esData);
[~, area_es] = ismember(string(esData.AreaID), AreaID);
A_es = sparse(area_es, (1:N_es)', ones(N_es,1), Narea, N_es);
P_es_max = esData.CAP;
E_es_max = esData.ENERGY_MWH;
E_es_min = E_es_max.*esData.SOC_MIN;
E_es_upper = E_es_max.*esData.SOC_MAX;
E_es_initial = E_es_max.*esData.SOC_INITIAL;
eta_es_ch = esData.ETA_CHARGE;
eta_es_dis = esData.ETA_DISCHARGE;
loss_es = esData.SELF_DISCHARGE;
c_es_degrade = (P_es_max.*esData.POWER_COST+E_es_max.*esData.ENERGY_COST) ...
    .*esData.REPLACE_FACTOR./(2*E_es_max.*esData.MAX_CYCLES); % 元/MWh吞吐量
c_es_FOM = fixedCostScale*esData.FOM_COST_PERIOD; % 本周期功率运维，元/MW
c_es_energy_FOM = fixedCostScale*esData.FOM_ENERGY_PERIOD; % 本周期能量运维，元/MWh

% -------------------- 1.17 径流式及库容式水电输入 --------------------
hydRorData = readtable(unitFile, 'Sheet', '径流式水电', 'VariableNamingRule', 'preserve');
hydResData = readtable(unitFile, 'Sheet', '库容式水电', 'VariableNamingRule', 'preserve');
N_hyd_ror = height(hydRorData);
N_hyd_res = height(hydResData);
[~, area_hyd_ror] = ismember(string(hydRorData.AreaID), AreaID);
[~, area_hyd_res] = ismember(string(hydResData.AreaID), AreaID);
A_hyd_ror = sparse(area_hyd_ror, (1:N_hyd_ror)', ones(N_hyd_ror,1), Narea, N_hyd_ror);
A_hyd_res = sparse(area_hyd_res, (1:N_hyd_res)', ones(N_hyd_res,1), Narea, N_hyd_res);
% 海南源表给出月出力。直接读取同一水文情景，并在输入段扩展为本次小时序列。
hydMonthData = readtable(systemFile, 'Sheet', '水电月参数', 'VariableNamingRule', 'preserve');
hydMonthData = hydMonthData(string(hydMonthData.Hydrology)==hydrologyScenario,:);
P_hyd_ror_min = zeros(N_hyd_ror,T);
P_hyd_ror_expected = zeros(N_hyd_ror,T);
P_hyd_res_min = zeros(N_hyd_res,T);
P_hyd_res_expected = zeros(N_hyd_res,T);
for i = 1:N_hyd_ror
    hydRows = hydMonthData(string(hydMonthData.UnitKey)==string(hydRorData.UnitKey(i)),:);
    [~, monthRows] = ismember(month(Time),hydRows.Month);
    P_hyd_ror_min(i,:) = hydRows.ForcedMW(monthRows)';
    P_hyd_ror_expected(i,:) = hydRows.ExpectedMW(monthRows)';
end
for i = 1:N_hyd_res
    hydRows = hydMonthData(string(hydMonthData.UnitKey)==string(hydResData.UnitKey(i)),:);
    [~, monthRows] = ismember(month(Time),hydRows.Month);
    P_hyd_res_min(i,:) = hydRows.ForcedMW(monthRows)';
    P_hyd_res_expected(i,:) = hydRows.ExpectedMW(monthRows)';
end
% 每个库容水电、每个覆盖月份为一个调节周期，部分月份只取覆盖小时的电量。
Month = unique(dateshift(Time,'start','month'),'stable');
N_hyd_period = N_hyd_res*numel(Month);
hydPeriodUnit = zeros(N_hyd_period,1);
hydPeriodHours = cell(N_hyd_period,1);
E_hyd_available = zeros(N_hyd_period,1);
for i = 1:N_hyd_res
    hydRows = hydMonthData(string(hydMonthData.UnitKey)==string(hydResData.UnitKey(i)),:);
    for j = 1:numel(Month)
        k = (i-1)*numel(Month)+j;
        hydPeriodUnit(k) = i;
        hydPeriodHours{k} = find(dateshift(Time,'start','month')==Month(j));
        averageMW = hydRows.AverageMW(hydRows.Month==month(Month(j)));
        E_hyd_available(k) = averageMW*numel(hydPeriodHours{k})*Delta_t;
    end
end

% -------------------- 1.18 光热输入 --------------------
cspData = readtable(unitFile, 'Sheet', '光热', 'VariableNamingRule', 'preserve');
N_csp = height(cspData);
[~, area_csp] = ismember(string(cspData.AreaID), AreaID);
A_csp = sparse(area_csp, (1:N_csp)', ones(N_csp,1), Narea, N_csp);
P_csp_max = cspData.CAP;
eta_csp = cspData.THERMAL_EFFICIENCY;
u_csp_0 = cspData.INIT_STATUS;
P_csp_0 = cspData.INIT_POWER;
cspTimeData = readtable(systemFile, 'Sheet', '光热时序', 'VariableNamingRule', 'preserve');
if isnumeric(cspTimeData.Time)
    cspTimeData.Time = datetime(cspTimeData.Time, 'ConvertFrom', 'excel');
end
DNI = nan(N_csp,T);
for i = 1:N_csp
    solarRows = cspTimeData(string(cspTimeData.UnitKey)==string(cspData.UnitKey(i)),:);
    [~, timeRows] = ismember(Time, dateshift(solarRows.Time,'start','hour','nearest'));
    DNI(i,:) = solarRows.DNI_W_m2(timeRows)';
end
Q_csp_sf = repmat(cspData.SOLAR_MULTIPLE.*P_csp_max./ ...
    (eta_csp.*cspData.DNI_REFERENCE),1,T).*DNI; % MWt，DNI比值无量纲

% -------------------- 1.19 新能源友好电站输入 --------------------
friendlyData = readtable(unitFile, 'Sheet', '新能源友好电站', 'VariableNamingRule', 'preserve');
N_fr = height(friendlyData);
[~, area_fr] = ismember(string(friendlyData.AreaID), AreaID);
A_fr = sparse(area_fr, (1:N_fr)', ones(N_fr,1), Narea, N_fr);
P_fr_es_max = friendlyData.ES_POWER;
E_fr_max = friendlyData.ENERGY_MWH;
E_fr_min = E_fr_max.*friendlyData.SOC_MIN;
E_fr_upper = E_fr_max.*friendlyData.SOC_MAX;
E_fr_initial = E_fr_max.*friendlyData.SOC_INITIAL;
friendlyTimeData = readtable(systemFile, 'Sheet', '友好电站时序', 'VariableNamingRule', 'preserve');
if isnumeric(friendlyTimeData.Time)
    friendlyTimeData.Time = datetime(friendlyTimeData.Time, 'ConvertFrom', 'excel');
end
P_fr_wind_available = nan(N_fr,T);
P_fr_pv_available = nan(N_fr,T);
for i = 1:N_fr
    frRows = friendlyTimeData(string(friendlyTimeData.UnitKey)==string(friendlyData.UnitKey(i)),:);
    [~, timeRows] = ismember(Time, dateshift(frRows.Time,'start','hour','nearest'));
    P_fr_wind_available(i,:) = frRows.WindAvailable_MW(timeRows)';
    P_fr_pv_available(i,:) = frRows.PVAvailable_MW(timeRows)';
end
c_fr_degrade = (P_fr_es_max.*friendlyData.POWER_COST+E_fr_max.*friendlyData.ENERGY_COST) ...
    .*friendlyData.REPLACE_FACTOR./(2*E_fr_max.*friendlyData.MAX_CYCLES);

% -------------------- 1.20 需求响应及失负荷输入 --------------------
loadParameterData = readtable(systemFile, 'Sheet', '分区负荷参数', 'VariableNamingRule', 'preserve');
[~, loadParameterRows] = ismember(AreaID, string(loadParameterData.AreaID));
loadParameterData = loadParameterData(loadParameterRows,:);
drTimeData = readtable(systemFile, 'Sheet', '需求响应时序', 'VariableNamingRule', 'preserve');
drDailyData = readtable(systemFile, 'Sheet', '需求响应日限额', 'VariableNamingRule', 'preserve');
if isnumeric(drTimeData.Time)
    drTimeData.Time = datetime(drTimeData.Time, 'ConvertFrom', 'excel');
end
if isnumeric(drDailyData.Date)
    drDailyData.Date = datetime(drDailyData.Date, 'ConvertFrom', 'excel');
end
Day = unique(dateshift(Time,'start','day'), 'stable');
Nday = numel(Day);
P_shift_out_max = nan(Narea,T);
P_shift_in_max = nan(Narea,T);
P_cut_max = nan(Narea,T);
E_cut_day_max = nan(Narea,Nday);
shiftFrom = cell(Narea,1);
shiftTo = cell(Narea,1);
for r = 1:Narea
    drRows = drTimeData(string(drTimeData.AreaID)==AreaID(r),:);
    [~, timeRows] = ismember(Time, dateshift(drRows.Time,'start','hour','nearest'));
    P_shift_out_max(r,:) = drRows.ShiftOutMax_MW(timeRows)';
    P_shift_in_max(r,:) = drRows.ShiftInMax_MW(timeRows)';
    P_cut_max(r,:) = drRows.CutMax_MW(timeRows)';
    drDayRows = drDailyData(string(drDailyData.AreaID)==AreaID(r),:);
    [~, dayRows] = ismember(Day, dateshift(drDayRows.Date,'start','day'));
    E_cut_day_max(r,:) = drDayRows.CutEnergyMax_MWh(dayRows)';
    % 只建立同日、非自身且满足时间窗的有向转移边，避免8760×8760稠密变量。
    fromByDay = cell(Nday,1);
    toByDay = cell(Nday,1);
    for d = 1:Nday
        dayHours = find(dateshift(Time,'start','day')==Day(d));
        [origin,destination] = ndgrid(dayHours,dayHours);
        allowed = abs(origin-destination)>0 & ...
            abs(origin-destination)*Delta_t<=loadParameterData.ShiftWindow_h(r);
        fromByDay{d} = origin(allowed);
        toByDay{d} = destination(allowed);
    end
    shiftFrom{r} = vertcat(fromByDay{:});
    shiftTo{r} = vertcat(toByDay{:});
end

% -------------------- 1.21 省间互济物理通道 --------------------
N_inter = 0;
A_inter = sparse(Narea,0);
interForward = zeros(0,1);
interReverse = zeros(0,1);
interPrice = zeros(0,1);
if useInterprovincial
    interLineData = readtable(systemFile, 'Sheet', '省间通道', 'VariableNamingRule', 'preserve');
    interLineData = interLineData(interLineData.Enabled==1,:);
    N_inter = height(interLineData);
    [~, interFrom] = ismember(string(interLineData.FromArea), AreaID);
    [~, interTo] = ismember(string(interLineData.ToArea), AreaID);
    A_inter = sparse([interFrom;interTo],[(1:N_inter)';(1:N_inter)'], ...
        [-ones(N_inter,1);ones(N_inter,1)],Narea,N_inter);
    interForward = interLineData.ForwardMW;
    interReverse = interLineData.ReverseMW;
    interPrice = interLineData.PenaltyCost; % 元/MWh，每条物理通道只计一次
end

% -------------------- 1.22 按省汇总每周负荷峰值 --------------------
weekStart = dateshift(Time,'start','day')-days(mod(weekday(Time)+5,7));
Week = unique(weekStart,'stable');
P_week_peak = zeros(Nprovince,T);
for p = 1:Nprovince
    provinceLoad = zeros(height(loadData),1);
    for r = find(areaProvince==p)'
        base_load = baseData{string(baseData{:,1})=="负荷" & string(baseData{:,2})==AreaName(r),3};
        provinceLoad = provinceLoad+loadData{:,char(AreaName(r))}*base_load;
    end
    for k = 1:numel(Week)
        sourceHours = loadTime>=Week(k) & loadTime<Week(k)+days(7);
        P_week_peak(p,weekStart==Week(k)) = max(provinceLoad(sourceHours));
    end
end

fprintf('输入已读取：%d个分区，%d小时。开始建立决策变量。\n', Narea, T);

%% 二、决策变量
% -------------------- 2.1 煤电 --------------------
yalmip('clear');
P_th = sdpvar(N_th, T, 'full');             % 机组实际出力，MW
u_th = binvar(N_th, T, 'full');             % 运行状态
v_th = binvar(N_th, T, 'full');             % 启动状态
w_th = binvar(N_th, T, 'full');             % 停机状态
% 连续累计量由启停变量递推确定，不新增整数决策；用于紧凑最小开停机约束。
V_th_count = sdpvar(N_th, T+1, 'full'); % 时段边界累计启动次数
W_th_count = sdpvar(N_th, T+1, 'full'); % 时段边界累计停机次数
R_th_hot = sdpvar(N_th, T, 'full'); % 热备用，MW
R_th_cold = sdpvar(N_th, T, 'full'); % 冷备用，MW
F_th = sdpvar(N_th, T, 'full');             % 燃料成本速率，元/h
CO2_th = sdpvar(N_th, T, 'full');           % 排放速率，t/h
lambda_th = cell(N_th, 1);                 % 每台机组6个成本节点权重
segment_th = cell(N_th, 1);                % 每台机组5段选择，SOS2的等价线性表示
for i = 1:N_th
    lambda_th{i} = sdpvar(6, T, 'full');
    segment_th{i} = binvar(5, T, 'full');
end

% -------------------- 2.2 气电 --------------------
P_gas = sdpvar(N_gas, T, 'full');           % 实际出力，MW
u_gas = binvar(N_gas, T, 'full');           % 运行状态
v_gas = binvar(N_gas, T, 'full');           % 启动状态
w_gas = binvar(N_gas, T, 'full');           % 停机状态
% 连续累计量由启停变量递推确定，不新增整数决策；用于紧凑最小开停机约束。
V_gas_count = sdpvar(N_gas, T+1, 'full'); % 时段边界累计启动次数
W_gas_count = sdpvar(N_gas, T+1, 'full'); % 时段边界累计停机次数
R_gas_hot = sdpvar(N_gas, T, 'full'); % 热备用，MW
R_gas_cold = sdpvar(N_gas, T, 'full'); % 冷备用，MW

% -------------------- 2.3 生物质 --------------------
P_bio = sdpvar(N_bio, T, 'full');           % 生物质实际出力，MW；不优化经济启停

% -------------------- 2.4 径流式及库容式水电 --------------------
P_hyd_ror = sdpvar(N_hyd_ror,T,'full');
P_hyd_res = sdpvar(N_hyd_res,T,'full');
R_hyd_hot = sdpvar(N_hyd_res,T,'full');
E_hyd_curt = sdpvar(N_hyd_period,1,'full'); % 调节周期弃水电量，MWh

% -------------------- 2.5 电化学储能 --------------------
P_es_charge = sdpvar(N_es,T,'full');        % 单机充电功率，MW
P_es_discharge = sdpvar(N_es,T,'full');     % 单机放电功率，MW
u_es_ch = binvar(N_es,T,'full');
u_es_dis = binvar(N_es,T,'full');
E_es = sdpvar(N_es,T+1,'full');
R_es_hot = sdpvar(N_es,T,'full');

% -------------------- 2.6 抽水蓄能 --------------------
P_ps_gen = sdpvar(N_ps, T, 'full');         % 发电功率，MW
P_ps_pump = sdpvar(N_ps, T, 'full');        % 抽水吸收功率，MW
u_ps_gen = binvar(N_ps, T, 'full');         % 单机发电状态
u_ps_pump = binvar(N_ps, T, 'full');        % 单机抽水状态
u_ps_area_gen = binvar(Narea, T, 'full');   % 分区发电方向
u_ps_area_pump = binvar(Narea, T, 'full');  % 分区抽水方向
R_ps_hot = sdpvar(N_ps, T, 'full');         % 抽蓄热备用，MW
E_ps = sdpvar(N_ps, T+1, 'full');           % 第1列为初始能量，后续列为时段末能量

% -------------------- 2.7 风电 --------------------
P_wind = sdpvar(Narea, T, 'full');          % 分区风电实际出力，MW
P_wind_curt = sdpvar(Narea, T, 'full');     % 分区弃风功率，MW

% -------------------- 2.8 光伏 --------------------
P_pv = sdpvar(Narea, T, 'full');            % 分区光伏实际出力，MW
P_pv_curt = sdpvar(Narea, T, 'full');       % 分区弃光功率，MW

% -------------------- 2.9 新能源友好电站 --------------------
P_fr_wind_out = sdpvar(N_fr,T,'full');
P_fr_wind_ch = sdpvar(N_fr,T,'full');
P_fr_wind_curt = sdpvar(N_fr,T,'full');
P_fr_pv_out = sdpvar(N_fr,T,'full');
P_fr_pv_ch = sdpvar(N_fr,T,'full');
P_fr_pv_curt = sdpvar(N_fr,T,'full');
P_fr_charge = sdpvar(N_fr,T,'full');
P_fr_discharge = sdpvar(N_fr,T,'full');
u_fr_ch = binvar(N_fr,T,'full');
u_fr_dis = binvar(N_fr,T,'full');
E_fr = sdpvar(N_fr,T+1,'full');

% -------------------- 2.10 核电 --------------------
P_nuc = sdpvar(N_nuc, T, 'full');           % 核电实际出力，MW；不优化经济启停

% -------------------- 2.11 光热 --------------------
P_csp_unit = sdpvar(N_csp,T,'full');        % 发电功率，MWe
u_csp = binvar(N_csp,T,'full');
v_csp = binvar(N_csp,T,'full');
w_csp = binvar(N_csp,T,'full');
Q_csp_in = sdpvar(N_csp,T,'full');          % 充热，MWt
Q_csp_out = sdpvar(N_csp,T,'full');         % 放热，MWt
Q_csp_curt = sdpvar(N_csp,T,'full');        % 弃热，MWt
Q_csp_pg = sdpvar(N_csp,T,'full');          % 汽轮机吸热，MWt
u_csp_in = binvar(N_csp,T,'full');
u_csp_out = binvar(N_csp,T,'full');
E_csp = sdpvar(N_csp,T+1,'full');           % 储热量，MWht

% -------------------- 2.12 负荷与省内交换 --------------------
P_shed = sdpvar(Narea, T, 'full');          % 失负荷，MW；不是需求响应P_cut
P_line = sdpvar(N_line, T, 'full');     % 双向通道功率，MW，可正可负

% -------------------- 2.13 需求响应与省间互济 --------------------
P_shift_out = sdpvar(Narea,T,'full');
P_shift_in = sdpvar(Narea,T,'full');
P_cut = sdpvar(Narea,T,'full');
P_shift_pair = cell(Narea,1);
for r = 1:Narea
    P_shift_pair{r} = sdpvar(numel(shiftFrom{r}),1,'full');
end
F_inter = sdpvar(N_inter,T,'full');
F_inter_abs = sdpvar(N_inter,T,'full');

fprintf('开始建立设备约束、备用及功率平衡。\n');

%% 三、约束条件
Constraints = [];

% -------------------- 3.1 煤电约束 --------------------
% PDF 2.1.1：燃煤产汽驱动汽轮机；含启停、检修、出力、爬坡、备用与分段成本/排放。
u_th_previous = [u_th_0, u_th(:,1:T-1)];
Constraints = [Constraints, u_th-u_th_previous == v_th-w_th];
Constraints = [Constraints, v_th+w_th <= 1, u_th <= 1-m_th];
Constraints = [Constraints, repmat(P_th_min,1,T).*u_th <= P_th];
% 热备用与实际出力共用在线容量；冷备用限于未检修的停机容量。
Constraints = [Constraints, P_th+R_th_hot <= repmat(P_th_max,1,T).*u_th];
Constraints = [Constraints, R_th_hot>=0, R_th_cold>=0];
Constraints = [Constraints, R_th_cold<=repmat(P_th_max,1,T).*(1-u_th)];
Constraints = [Constraints, R_th_cold<=repmat(P_th_max,1,T).*(1-m_th)];
P_th_previous = [P_th_0, P_th(:,1:T-1)];
Constraints = [Constraints, P_th-P_th_previous <= ...
    repmat(RU_th,1,T).*u_th_previous + repmat(SU_th,1,T).*(u_th-u_th_previous)];
Constraints = [Constraints, P_th_previous-P_th <= ...
    repmat(RD_th,1,T).*u_th + repmat(SD_th,1,T).*(u_th_previous-u_th)];

% 最小开停机约束：周期起点承接初始持续时间，尾端按PDF截断到T。
Constraints = [Constraints, V_th_count(:,1)==0, W_th_count(:,1)==0];
Constraints = [Constraints, V_th_count(:,2:T+1)-V_th_count(:,1:T)==v_th];
Constraints = [Constraints, W_th_count(:,2:T+1)-W_th_count(:,1:T)==w_th];
for i = 1:N_th
    remaining = max(0, u_th_0(i)*T_th_up(i) + (1-u_th_0(i))*T_th_down(i)-T_th_initial(i));
    if remaining > 0
        Constraints = [Constraints, u_th(i,1:min(T,remaining)) == u_th_0(i)];
    end
    % 最近U小时启动次数≤当前开机状态；最近D小时停机次数≤当前停机状态。
    % 与逐次启停后的保持状态要求等价；尾端自然截断，初始剩余义务仍由上方约束承接。
    % 累计量差仅含两个非零系数，避免逐滞后展开或稠密三角累计矩阵。
    if T_th_up(i)>0
        firstBoundary = max(1,(1:T)-T_th_up(i)+1);
        Constraints = [Constraints, V_th_count(i,2:T+1)-V_th_count(i,firstBoundary)<=u_th(i,:)];
    end
    if T_th_down(i)>0
        firstBoundary = max(1,(1:T)-T_th_down(i)+1);
        Constraints = [Constraints, W_th_count(i,2:T+1)-W_th_count(i,firstBoundary)<=1-u_th(i,:)];
    end
end
% 五段分段线性模型：停机时全部权重为0，运行时权重之和为1。
% 显式选择一段，仅该段两端节点允许正权重，与原SOS2完全等价。
% 按每台机组一次性建立全部时段，避免逐小时创建大量独立SOS2对象。
for i = 1:N_th
    Constraints = [Constraints, lambda_th{i}(:) >= 0];
    Constraints = [Constraints, sum(lambda_th{i},1) == u_th(i,:)];
    Constraints = [Constraints, P_th(i,:) == P_th_node(i,:)*lambda_th{i}];
    Constraints = [Constraints, F_th(i,:) == F_th_node(i,:)*lambda_th{i}];
    Constraints = [Constraints, CO2_th(i,:) == CO2_th_node(i,:)*lambda_th{i}];
    Constraints = [Constraints, sum(segment_th{i},1)==u_th(i,:)];
    Constraints = [Constraints, lambda_th{i}(1,:)<=segment_th{i}(1,:)];
    Constraints = [Constraints, lambda_th{i}(2:5,:)<=segment_th{i}(1:4,:)+segment_th{i}(2:5,:)];
    Constraints = [Constraints, lambda_th{i}(6,:)<=segment_th{i}(5,:)];
end

% -------------------- 3.2 气电约束 --------------------
% PDF 2.1.2：燃气驱动燃气轮机；含启停、检修、出力、爬坡、备用约束。
u_gas_previous = [u_gas_0, u_gas(:,1:T-1)];
Constraints = [Constraints, u_gas-u_gas_previous == v_gas-w_gas];
Constraints = [Constraints, v_gas+w_gas <= 1, u_gas <= 1-m_gas];
Constraints = [Constraints, repmat(P_gas_min,1,T).*u_gas <= P_gas];
% 热备用与实际出力共用在线容量；冷备用限于未检修的停机容量。
Constraints = [Constraints, P_gas+R_gas_hot <= repmat(P_gas_max,1,T).*u_gas];
Constraints = [Constraints, R_gas_hot>=0, R_gas_cold>=0];
Constraints = [Constraints, R_gas_cold<=repmat(P_gas_max,1,T).*(1-u_gas)];
Constraints = [Constraints, R_gas_cold<=repmat(P_gas_max,1,T).*(1-m_gas)];
P_gas_previous = [P_gas_0, P_gas(:,1:T-1)];
Constraints = [Constraints, P_gas-P_gas_previous <= ...
    repmat(RU_gas,1,T).*u_gas_previous + repmat(SU_gas,1,T).*(u_gas-u_gas_previous)];
Constraints = [Constraints, P_gas_previous-P_gas <= ...
    repmat(RD_gas,1,T).*u_gas + repmat(SD_gas,1,T).*(u_gas_previous-u_gas)];

% 最小开停机约束：周期起点承接初始持续时间，尾端按PDF截断到T。
Constraints = [Constraints, V_gas_count(:,1)==0, W_gas_count(:,1)==0];
Constraints = [Constraints, V_gas_count(:,2:T+1)-V_gas_count(:,1:T)==v_gas];
Constraints = [Constraints, W_gas_count(:,2:T+1)-W_gas_count(:,1:T)==w_gas];
for i = 1:N_gas
    remaining = max(0, u_gas_0(i)*T_gas_up(i) + (1-u_gas_0(i))*T_gas_down(i)-T_gas_initial(i));
    if remaining > 0
        Constraints = [Constraints, u_gas(i,1:min(T,remaining)) == u_gas_0(i)];
    end
    % 最近U小时启动次数≤当前开机状态；最近D小时停机次数≤当前停机状态。
    % 与逐次启停后的保持状态要求等价；尾端自然截断，初始剩余义务仍由上方约束承接。
    % 累计量差仅含两个非零系数，避免逐滞后展开或稠密三角累计矩阵。
    if T_gas_up(i)>0
        firstBoundary = max(1,(1:T)-T_gas_up(i)+1);
        Constraints = [Constraints, V_gas_count(i,2:T+1)-V_gas_count(i,firstBoundary)<=u_gas(i,:)];
    end
    if T_gas_down(i)>0
        firstBoundary = max(1,(1:T)-T_gas_down(i)+1);
        Constraints = [Constraints, W_gas_count(i,2:T+1)-W_gas_count(i,firstBoundary)<=1-u_gas(i,:)];
    end
end

% -------------------- 3.3 生物质约束 --------------------
% PDF 2.1.3：生物质产汽发电；检修外生，含出力、爬坡与年度电量约束。
u_bio = 1-m_bio;
Constraints = [Constraints, repmat(P_bio_min,1,T).*u_bio <= P_bio];
Constraints = [Constraints, P_bio <= repmat(P_bio_max,1,T).*u_bio];
P_bio_previous = [P_bio_0, P_bio(:,1:T-1)];
m_bio_previous = [m_bio_0, m_bio(:,1:T-1)];

u_bio_previous = 1-m_bio_previous;
Constraints = [Constraints, P_bio-P_bio_previous <= ...
    repmat(RU_bio,1,T).*u_bio_previous + repmat(SU_bio,1,T).*m_bio_previous];
Constraints = [Constraints, P_bio_previous-P_bio <= ...
    repmat(RD_bio,1,T).*u_bio + repmat(SD_bio,1,T).*m_bio];

% 年度/本次周期可用燃料对应的累计发电量上限。
Constraints = [Constraints, sum(P_bio,2)*Delta_t<=E_bio_limit];

% -------------------- 3.4 水电约束（PDF 2.1.4、2.1.5） --------------------
% 径流式出力介于强迫与预想间；库容式另含周期可用电量与旋转备用。
if N_hyd_ror>0
    Constraints = [Constraints, P_hyd_ror_min<=P_hyd_ror, P_hyd_ror<=P_hyd_ror_expected];
end
if N_hyd_res>0
    Constraints = [Constraints, P_hyd_res_min<=P_hyd_res];
    Constraints = [Constraints, R_hyd_hot>=0, P_hyd_res+R_hyd_hot<=P_hyd_res_expected];
    for k = 1:N_hyd_period
        i = hydPeriodUnit(k);
        periods = hydPeriodHours{k};
        % 备用预留电量也占用周期可用电量，按PDF原式计入。
        E_hyd_used = sum(P_hyd_res(i,periods)+R_hyd_hot(i,periods))*Delta_t;
        Constraints = [Constraints, E_hyd_used<=E_hyd_available(k)];
        Constraints = [Constraints, E_hyd_curt(k)==E_hyd_available(k)-E_hyd_used, E_hyd_curt(k)>=0];
    end
end
P_hyd = A_hyd_ror*P_hyd_ror+A_hyd_res*P_hyd_res;

% -------------------- 3.5 电化学储能约束（PDF 2.2.1） --------------------
% 电池充放互斥、SOC时序递推与上下限、首末相等及旋转备用。
if N_es>0
    Constraints = [Constraints, 0<=P_es_charge, P_es_charge<=repmat(P_es_max,1,T).*u_es_ch];
    Constraints = [Constraints, 0<=P_es_discharge, P_es_discharge<=repmat(P_es_max,1,T).*u_es_dis];
    Constraints = [Constraints, u_es_ch+u_es_dis<=1];
    Constraints = [Constraints, E_es(:,2:T+1)==repmat(1-loss_es,1,T).*E_es(:,1:T) + ...
        repmat(eta_es_ch,1,T).*P_es_charge*Delta_t - repmat(1./eta_es_dis,1,T).*P_es_discharge*Delta_t];
    Constraints = [Constraints, repmat(E_es_min,T+1,1)<=E_es(:), E_es(:)<=repmat(E_es_upper,T+1,1)];
    Constraints = [Constraints, E_es(:,1)==E_es_initial, E_es(:,T+1)==E_es_initial];
    Constraints = [Constraints, R_es_hot>=0];
    Constraints = [Constraints, P_es_discharge+R_es_hot<=repmat(P_es_max,1,T).*(1-u_es_ch)];
    Constraints = [Constraints, repmat(reserveDuration(areaProvince(area_es)),1,T).*R_es_hot <= ...
        repmat(eta_es_dis,1,T).*(E_es(:,2:T+1)-repmat(E_es_min,1,T))];
end
P_es_dis = A_es*P_es_discharge;
P_es_ch = A_es*P_es_charge;

% -------------------- 3.6 抽水蓄能约束 --------------------
% PDF 2.2.2：抽发互斥、分区方向一致、能量守恒及旋转备用。
Constraints = [Constraints, 0 <= P_ps_gen, P_ps_gen <= repmat(P_ps_max,1,T).*u_ps_gen];
Constraints = [Constraints, repmat(P_ps_pump_min,1,T).*u_ps_pump <= P_ps_pump];
Constraints = [Constraints, P_ps_pump <= repmat(P_ps_pump_max,1,T).*u_ps_pump];
Constraints = [Constraints, u_ps_gen+u_ps_pump <= 1];
Constraints = [Constraints, u_ps_gen <= 1-m_ps, u_ps_pump <= 1-m_ps];
% 同一分区内各机组的抽发方向一致，不能一部分抽水、另一部分发电。
Constraints = [Constraints, u_ps_area_gen+u_ps_area_pump <= 1];
Constraints = [Constraints, u_ps_gen <= u_ps_area_gen(area_ps,:)];
Constraints = [Constraints, u_ps_pump <= u_ps_area_pump(area_ps,:)];
% 时序能量守恒：抽水增加水库能量，发电消耗水库能量。
Constraints = [Constraints, E_ps(:,2:T+1) == E_ps(:,1:T) + ...
    repmat(eta_ps_pump,1,T).*P_ps_pump*Delta_t - ...
    repmat(1./eta_ps_gen,1,T).*P_ps_gen*Delta_t];
% 按列展开为逐元素界限，避免台数恰好等于T+1时被解释为矩阵不等式。
Constraints = [Constraints, repmat(E_ps_max.*SOC_ps_min,T+1,1) <= E_ps(:)];
Constraints = [Constraints, E_ps(:) <= repmat(E_ps_max.*SOC_ps_max,T+1,1)];
Constraints = [Constraints, E_ps(:,1) == E_ps_initial, E_ps(:,T+1) == E_ps_initial];

% 热备用同时受发电状态、功率余量和时段末储水能量限制。
Constraints = [Constraints, R_ps_hot>=0];
Constraints = [Constraints, P_ps_gen+R_ps_hot<=repmat(P_ps_max,1,T).*u_ps_gen];
Constraints = [Constraints, repmat(reserveDuration(areaProvince(area_ps)),1,T).*R_ps_hot <= ...
    repmat(eta_ps_gen,1,T).*(E_ps(:,2:T+1)-repmat(E_ps_max.*SOC_ps_min,1,T))];

% -------------------- 3.7 风电约束 --------------------
% PDF 2.3.1：风能经风轮发电；可用功率=实际出力+弃风。
Constraints = [Constraints, P_wind(:) >= 0, P_wind_curt(:) >= 0];
Constraints = [Constraints, P_wind+P_wind_curt == P_wind_available];

% -------------------- 3.8 光伏约束 --------------------
% PDF 2.3.2：光伏电池经逆变发电；可用功率=实际出力+弃光。
Constraints = [Constraints, P_pv(:) >= 0, P_pv_curt(:) >= 0];
Constraints = [Constraints, P_pv+P_pv_curt == P_pv_available];

% -------------------- 3.9 新能源友好电站约束（PDF 2.3.3） --------------------
% 风光分直送、站内充电、弃电三部分；储能仅由站内风光充电。
if N_fr>0
    Constraints = [Constraints, P_fr_wind_out>=0, P_fr_wind_ch>=0, P_fr_wind_curt>=0];
    Constraints = [Constraints, P_fr_pv_out>=0, P_fr_pv_ch>=0, P_fr_pv_curt>=0];
    Constraints = [Constraints, P_fr_wind_out+P_fr_wind_ch+P_fr_wind_curt==P_fr_wind_available];
    Constraints = [Constraints, P_fr_pv_out+P_fr_pv_ch+P_fr_pv_curt==P_fr_pv_available];
    % 储能仅由本站风光充电，不额外从电网吸收功率。
    Constraints = [Constraints, P_fr_charge==P_fr_wind_ch+P_fr_pv_ch];
    Constraints = [Constraints, 0<=P_fr_charge, P_fr_charge<=repmat(P_fr_es_max,1,T).*u_fr_ch];
    Constraints = [Constraints, 0<=P_fr_discharge, P_fr_discharge<=repmat(P_fr_es_max,1,T).*u_fr_dis];
    Constraints = [Constraints, u_fr_ch+u_fr_dis<=1];
    Constraints = [Constraints, E_fr(:,2:T+1)==repmat(1-friendlyData.SELF_DISCHARGE,1,T).*E_fr(:,1:T) + ...
        repmat(friendlyData.ETA_CHARGE,1,T).*P_fr_charge*Delta_t - ...
        repmat(1./friendlyData.ETA_DISCHARGE,1,T).*P_fr_discharge*Delta_t];
    Constraints = [Constraints, repmat(E_fr_min,T+1,1)<=E_fr(:), E_fr(:)<=repmat(E_fr_upper,T+1,1)];
    Constraints = [Constraints, E_fr(:,1)==E_fr_initial, E_fr(:,T+1)==E_fr_initial];
end
P_fr_unit = P_fr_wind_out+P_fr_pv_out+P_fr_discharge;
P_fr = A_fr*P_fr_unit;

% -------------------- 3.10 核电约束 --------------------
% PDF 2.4.1：核裂变产汽，基荷为主；检修外生，出力可有限调节。
u_nuc = 1-m_nuc;
Constraints = [Constraints, repmat(P_nuc_min,1,T).*u_nuc <= P_nuc];
Constraints = [Constraints, P_nuc <= repmat(P_nuc_max,1,T).*u_nuc];
P_nuc_previous = [P_nuc_0, P_nuc(:,1:T-1)];
m_nuc_previous = [m_nuc_0, m_nuc(:,1:T-1)];

% 检修边界按PDF以M=Pmax放松正常爬坡。
M_nuc = repmat(P_nuc_max,1,T).*(m_nuc+m_nuc_previous);
Constraints = [Constraints, P_nuc-P_nuc_previous <= repmat(RU_nuc,1,T)+M_nuc];
Constraints = [Constraints, P_nuc_previous-P_nuc <= repmat(RD_nuc,1,T)+M_nuc];

% -------------------- 3.11 光热约束（PDF 2.4.2） --------------------
% 聚光集热、储热放热驱动汽轮机；含储热平衡、出力与爬坡。
if N_csp>0
    Constraints = [Constraints, Q_csp_in>=0, Q_csp_out>=0, Q_csp_curt>=0, Q_csp_pg>=0];
    Constraints = [Constraints, Q_csp_in<=repmat(cspData.HEAT_CH_MAX,1,T).*u_csp_in];
    Constraints = [Constraints, Q_csp_out<=repmat(cspData.HEAT_DIS_MAX,1,T).*u_csp_out];
    Constraints = [Constraints, u_csp_in+u_csp_out<=1, Q_csp_in<=Q_csp_sf];
    Constraints = [Constraints, E_csp(:,2:T+1)==repmat(1-cspData.HEAT_LOSS,1,T).*E_csp(:,1:T) + ...
        repmat(cspData.HEAT_ETA_IN,1,T).*Q_csp_in*Delta_t - ...
        repmat(1./cspData.HEAT_ETA_OUT,1,T).*Q_csp_out*Delta_t];
    Constraints = [Constraints, repmat(cspData.HEAT_MIN,T+1,1)<=E_csp(:)];
    Constraints = [Constraints, E_csp(:)<=repmat(cspData.HEAT_MAX,T+1,1)];
    Constraints = [Constraints, E_csp(:,1)==cspData.HEAT_INITIAL, E_csp(:,T+1)==cspData.HEAT_INITIAL];
    Constraints = [Constraints, Q_csp_pg==Q_csp_sf-Q_csp_curt-Q_csp_in+Q_csp_out];
    Constraints = [Constraints, P_csp_unit==repmat(eta_csp,1,T).*Q_csp_pg];
    u_csp_previous = [u_csp_0,u_csp(:,1:T-1)];
    P_csp_previous = [P_csp_0,P_csp_unit(:,1:T-1)];
    Constraints = [Constraints, u_csp-u_csp_previous==v_csp-w_csp, v_csp+w_csp<=1];
    Constraints = [Constraints, 0<=P_csp_unit, P_csp_unit<=repmat(P_csp_max,1,T).*u_csp];
    Constraints = [Constraints, P_csp_unit-P_csp_previous<= ...
        repmat(cspData.RAMP_LIMIT_UP,1,T).*u_csp_previous + ...
        repmat(cspData.START_RAMP,1,T).*(u_csp-u_csp_previous)];
    Constraints = [Constraints, P_csp_previous-P_csp_unit<= ...
        repmat(cspData.RAMP_LIMIT_DOWN,1,T).*u_csp + ...
        repmat(cspData.STOP_RAMP,1,T).*(u_csp_previous-u_csp)];
end
P_csp = A_csp*P_csp_unit;

% -------------------- 3.12 省内断面约束 --------------------
% PDF 2.5.2：省内分区等效通道，双向功率受通道容量约束。
Constraints = [Constraints, -repmat(P_line_reverse,1,T) <= P_line];
Constraints = [Constraints, P_line <= repmat(P_line_forward,1,T)];
P_tr = A_line*P_line;                      % 分区净受入，全省各区之和为0

% -------------------- 3.13 省间互济约束（PDF 2.5.3） --------------------
% 省间联络通道按单一有符号功率建模，目标中按绝对值计惩罚。
if useInterprovincial && N_inter>0
    Constraints = [Constraints, -repmat(interReverse,1,T)<=F_inter, F_inter<=repmat(interForward,1,T)];
    Constraints = [Constraints, F_inter_abs>=0, F_inter_abs>=F_inter, F_inter_abs>=-F_inter];
end
P_inter = A_inter*F_inter;                 % 两端分区反号计入，同一通道只建一个变量
P_inter_province = A_province*P_inter;     % 各省净受入由实际通道汇总，不额外产生电量

% -------------------- 3.14 需求响应约束（PDF 2.6） --------------------
% 可转移负荷保总量、可削减负荷减用量，均受功率与电量限额。
Constraints = [Constraints, 0<=P_shift_out, P_shift_out<=P_shift_out_max];
Constraints = [Constraints, 0<=P_shift_in, P_shift_in<=P_shift_in_max];
Constraints = [Constraints, 0<=P_cut, P_cut<=P_cut_max];
for r = 1:Narea
    edgeCount = numel(shiftFrom{r});
    shiftOutMap = sparse(shiftFrom{r},(1:edgeCount)',ones(edgeCount,1),T,edgeCount);
    shiftInMap = sparse(shiftTo{r},(1:edgeCount)',ones(edgeCount,1),T,edgeCount);
    if edgeCount>0
        Constraints = [Constraints, P_shift_pair{r}>=0];
    end
    Constraints = [Constraints, P_shift_out(r,:)'==shiftOutMap*P_shift_pair{r}];
    Constraints = [Constraints, P_shift_in(r,:)'==shiftInMap*P_shift_pair{r}];
    for d = 1:Nday
        periods = find(dateshift(Time,'start','day')==Day(d));
        Constraints = [Constraints, sum(P_shift_out(r,periods))*Delta_t==sum(P_shift_in(r,periods))*Delta_t];
        Constraints = [Constraints, sum(P_cut(r,periods))*Delta_t<=E_cut_day_max(r,d)];
    end
end
Constraints = [Constraints, sum(P_cut,2)*Delta_t<=loadParameterData.AnnualCutEnergy_MWh];

% -------------------- 3.15 负荷及分区功率平衡 --------------------
% PDF 2.7.1：分区发电+储能净出力+省内/省间交换=响应后净负荷。
P_G = A_th*P_th + A_gas*P_gas + A_nuc*P_nuc + A_bio*P_bio + ...
    P_hyd + P_wind + P_pv + P_csp + P_fr;
P_sto = P_es_dis-P_es_ch + A_ps*(P_ps_gen-P_ps_pump);
P_D = P_load-P_shift_out+P_shift_in-P_cut;
Constraints = [Constraints, P_D>=0, 0<=P_shed];
Constraints = [Constraints, P_shed<=repmat(loadParameterData.ShedMaxRatio,1,T).*P_D];
% 对每个分区r、每个时段t建立平衡；不只约束第1个分区。
Constraints = [Constraints, P_G+P_sto+P_tr+P_inter+P_trans+P_shed == P_D];
% 抽水已在P_sto扣除，弃电不进入实际P_G，均不在平衡式重复扣除。

% -------------------- 3.16 新能源利用率（PDF 2.7.2） --------------------
% 省级新能源弃电量不超上限；采用交叉相乘避免除零。
P_RE_available = A_province*(P_wind_available+P_pv_available + ...
    A_fr*(P_fr_wind_available+P_fr_pv_available));
P_RE_curt = A_province*(P_wind_curt+P_pv_curt + A_fr*(P_fr_wind_curt+P_fr_pv_curt));
Constraints = [Constraints, sum(P_RE_curt,2)*Delta_t <= ...
    (1-renewableUtilizationMin).*sum(P_RE_available,2)*Delta_t];

% -------------------- 3.17 CO2排放总量（PDF 2.7.3） --------------------
% 煤电按六节点分段排放、气电线性排放，省级总量不超上限。
CO2_gas = repmat(CO2_gas_factor,1,T).*P_gas; % t/h
CO2_province = sum(A_province*(A_th*CO2_th+A_gas*CO2_gas),2)*Delta_t;
Constraints = [Constraints, CO2_province<=CO2Limit];

% -------------------- 3.18 周负荷峰值与备用（PDF 2.7.4） --------------------
% 按周负荷峰值确定热/冷备用需求，保证系统容量充裕度。
R_hot_province = A_province*(A_th*R_th_hot+A_gas*R_gas_hot + ...
    A_hyd_res*R_hyd_hot+A_ps*R_ps_hot+A_es*R_es_hot);
R_cold_province = A_province*(A_th*R_th_cold+A_gas*R_gas_cold);
Constraints = [Constraints, R_hot_province+R_cold_province>=repmat(adequacyFactor-1,1,T).*P_week_peak];
Constraints = [Constraints, R_hot_province>=repmat(hotReserveRatio,1,T).*P_week_peak];
Constraints = [Constraints, R_cold_province>=repmat(coldReserveRatio,1,T).*P_week_peak];

%% 四、目标函数
% PDF第1章：min F=发电+储能+需求响应+弃电+失负荷+省间互济六项成本。
% 省内分区交换仅受通道容量约束，不计入运行成本。
% -------------------- 4.1 煤电成本 --------------------
C_th_fuel = Delta_t*sum(F_th(:));
C_th_start = sum(c_th_start'*v_th);
C_th_stop = sum(c_th_stop'*w_th);
C_th_FOM = sum(c_th_FOM.*P_th_rated);
C_th_CO2 = c_CO2*Delta_t*sum(CO2_th(:));
C_th_SO2 = c_SO2*Delta_t*sum(SO2_th_factor'*P_th);
C_th_NOX = c_NOX*Delta_t*sum(NOX_th_factor'*P_th);
C_th_dust = c_dust*Delta_t*sum(dust_th_factor'*P_th);
C_th = C_th_fuel+C_th_start+C_th_stop+C_th_FOM+C_th_CO2+C_th_SO2+C_th_NOX+C_th_dust;

% -------------------- 4.2 气电成本 --------------------
C_gas_gen = Delta_t*sum(c_gas_gen'*P_gas);
C_gas_start = sum(c_gas_start'*v_gas);
C_gas_stop = sum(c_gas_stop'*w_gas);
C_gas_FOM = sum(c_gas_FOM.*P_gas_rated);
C_gas_CO2 = c_CO2*Delta_t*sum(CO2_gas(:));
C_gas = C_gas_gen+C_gas_start+C_gas_stop+C_gas_FOM+C_gas_CO2;

% -------------------- 4.3 生物质成本 --------------------
C_bio_gen = Delta_t*sum(c_bio_gen'*P_bio);
C_bio_FOM = sum(c_bio_FOM.*P_bio_rated);
C_bio = C_bio_gen+C_bio_FOM;

% -------------------- 4.4 水电成本 --------------------
% PDF水电仅计固定运维；库容式水电另计弃水惩罚。
C_hyd = fixedCostScale*sum(hydRorData.FOM_COST_PERIOD.*hydRorData.CAP) + ...
    fixedCostScale*sum(hydResData.FOM_COST_PERIOD.*hydResData.CAP);
C_hyd_curt = c_hyd_curt*sum(E_hyd_curt);

% -------------------- 4.5 电化学储能成本 --------------------
C_es_degrade = Delta_t*sum(c_es_degrade'*(P_es_charge+P_es_discharge));
C_es_FOM = sum(c_es_FOM.*P_es_max+c_es_energy_FOM.*E_es_max);
C_es = C_es_degrade+C_es_FOM;

% -------------------- 4.6 抽水蓄能成本 --------------------
C_ps = sum(c_ps_FOM.*P_ps_max);             % PDF仅计固定运维，不增加抽蓄启停成本

% -------------------- 4.7 风电成本 --------------------
C_wind = sum(c_wind_FOM.*W_capacity);
C_wind_curt = c_wind_curt*Delta_t*sum(P_wind_curt(:));

% -------------------- 4.8 光伏成本 --------------------
C_pv = sum(c_pv_FOM.*PV_capacity);
C_pv_curt = c_pv_curt*Delta_t*sum(P_pv_curt(:));

% -------------------- 4.9 新能源友好电站成本 --------------------
C_fr_degrade = Delta_t*sum(c_fr_degrade'*(P_fr_charge+P_fr_discharge));
C_fr_FOM = fixedCostScale*sum(friendlyData.WIND_FOM_PERIOD.*friendlyData.WIND_CAP + ...
    friendlyData.PV_FOM_PERIOD.*friendlyData.PV_CAP + ...
    friendlyData.FOM_COST_PERIOD.*P_fr_es_max + friendlyData.FOM_ENERGY_PERIOD.*E_fr_max);
C_fr = C_fr_degrade+C_fr_FOM;
C_fr_curt = Delta_t*(c_wind_curt*sum(P_fr_wind_curt(:))+c_pv_curt*sum(P_fr_pv_curt(:)));

% -------------------- 4.10 核电成本 --------------------
C_nuc_gen = Delta_t*sum(c_nuc_gen'*P_nuc);
C_nuc_FOM = sum(c_nuc_FOM.*P_nuc_max);
C_nuc = C_nuc_gen+C_nuc_FOM;

% -------------------- 4.11 光热成本 --------------------
C_csp = costScale*Delta_t*sum(cspData.GEN_COST'*P_csp_unit) + ...
    fixedCostScale*sum(cspData.FOM_COST_PERIOD.*P_csp_max);

% -------------------- 4.12 需求响应成本 --------------------
C_DR = Delta_t*sum(loadParameterData.ShiftCost'*P_shift_in) + ...
    Delta_t*sum(loadParameterData.CutCost'*P_cut) + fixedCostPeriodRatio*sum(loadParameterData.DRFixedCost);

% -------------------- 4.13 排放成本 --------------------
C_CO2 = C_th_CO2+C_gas_CO2;                % 已分别计入煤电、气电，汇总仅供输出
C_pollution = C_th_SO2+C_th_NOX+C_th_dust;

% -------------------- 4.14 系统成本与总目标 --------------------
C_gen = C_th+C_gas+C_nuc+C_bio+C_wind+C_pv+C_hyd+C_csp+C_fr;
C_sto = C_ps+C_es;
C_curt = C_wind_curt+C_pv_curt+C_hyd_curt+C_fr_curt;
C_shed = c_shed*Delta_t*sum(P_shed(:));
C_inter = Delta_t*sum(interPrice'*F_inter_abs);
Objective = C_gen+C_sto+C_DR+C_curt+C_shed+C_inter; % 与PDF六项总目标一致，排放已计入C_gen
% 固定运维按本周期参数计入，不使用原脚本的投资年化成本。

fprintf('约束已建立，开始调用%s求解器。\n', solverName);

%% 五、求解
options = sdpsettings('solver', solverName, 'verbose', 1);
options.gurobi.TimeLimit = solverTimeLimit;
options.gurobi.MIPFocus = 1;               % 优先寻找整数可行运行方案
options.gurobi.Heuristics = 0.2;           % 分配更多搜索时间给可行解启发式
options.gurobi.MIPGap = solverMIPGap;
options.gurobi.DualReductions = 0;         % 清楚区分不可行与无界，便于解释数据情景
options.gurobi.LogFile = fullfile(inputDir,'gurobi_hainan_last.log'); % 保留预处理、界限和可行解日志
options.savesolveroutput = 1;              % 保留原始求解状态，区分超时与不可行
solution = optimize(Constraints, Objective, options);
% 超时不等于不可行；有经过残差检查的可行解时允许输出，并明确尚未证明最优。
solverResult = solution.solveroutput;
if isfield(solverResult,'result'), solverResult = solverResult.result; end
isProvenOptimal = solution.problem==0;     % 求解器在设定MIPGap内完成
if solution.problem~=0
    hasIncumbent = isfield(solverResult,'x') && ~isempty(solverResult.x) && ...
        all(isfinite(solverResult.x(:))) && isfinite(value(Objective));
    if solution.problem==3 && hasIncumbent
        constraintResidual = check(Constraints);
        if ~all(isfinite(constraintResidual)) || min(constraintResidual)<-1e-4
            fprintf('达到时限，但候选解未通过约束残差检查，不输出运行结果。\n');
            return;
        end
        fprintf('达到时限，输出当前可行解，尚未证明达到目标精度。\n');
        if isfield(solverResult,'mipgap'), fprintf('当前相对间隙：%.2f%%。\n',100*solverResult.mipgap); end
    elseif solution.problem==3
        fprintf('达到求解时限，尚无可用可行解；这不是不可行证明。详见同目录gurobi_hainan_last.log。\n');
        return;
    else
        fprintf('求解未正常完成：%s\n',solution.info);
        return;
    end
end

%% 六、结果输出
% 输入、决策变量及以下数值结果都保留在MATLAB工作区，便于直接查看。
P_th_result = value(P_th);
P_gas_result = value(P_gas);
P_ps_gen_result = value(P_ps_gen);
P_ps_pump_result = value(P_ps_pump);
E_ps_result = value(E_ps);
P_nuc_result = value(P_nuc);
P_bio_result = value(P_bio);
P_wind_result = value(P_wind);
P_pv_result = value(P_pv);
P_wind_curt_result = value(P_wind_curt);
P_pv_curt_result = value(P_pv_curt);
P_shed_result = value(P_shed);
P_tr_result = value(P_tr);
P_hyd_result = value(P_hyd);
P_csp_result = value(P_csp);
P_fr_result = value(P_fr);
P_es_charge_result = value(P_es_charge);
P_es_discharge_result = value(P_es_discharge);
E_es_result = value(E_es);
E_csp_result = value(E_csp);
E_fr_result = value(E_fr);
P_shift_out_result = value(P_shift_out);
P_shift_in_result = value(P_shift_in);
P_cut_result = value(P_cut);
P_inter_result = value(P_inter);
R_hot_result = value(R_hot_province);
R_cold_result = value(R_cold_province);
CO2_result = value(CO2_province);
TotalCost = value(Objective);
BalanceResidual = value(P_G+P_sto+P_tr+P_inter+P_trans+P_shed-P_D);
fprintf('分区数：%d，仿真小时数：%d，总成本：%.3f元。\n', Narea, T, TotalCost);
fprintf('最大功率平衡残差：%.3g MW。\n', max(abs(BalanceResidual(:))));
REAvailableEnergy = sum(P_RE_available,2)*Delta_t;
RECurtailedEnergy = sum(value(P_RE_curt),2)*Delta_t;
RenewableUtilization = nan(Nprovince,1); % 无风光能量时，利用率没有定义
hasRenewable = REAvailableEnergy>0;
RenewableUtilization(hasRenewable) = 1-RECurtailedEnergy(hasRenewable)./REAvailableEnergy(hasRenewable);
fprintf('新能源利用率：%.2f%%，弃风光电量：%.3f MWh，失负荷：%.3f MWh。\n', ...
    100*RenewableUtilization(1),sum(RECurtailedEnergy),sum(P_shed_result(:))*Delta_t);
EnergyNotServed = sum(P_shed_result(:))*Delta_t; % 未供电量，MWh，不是实际发电
LossOfLoadHours = sum(any(P_shed_result>1e-4,1))*Delta_t;
MaxLoadShortfall = max(sum(P_shed_result,1));    % 全省最大同时缺口，MW
fprintf('缺电小时：%.0f h，最大缺口：%.3f MW。\n',LossOfLoadHours,MaxLoadShortfall);
toc;

%% 七、设备运行图像
% 默认展示仿真起点后的168小时；修改下面两项即可查看任意连续区间。
% 保留逐小时柱状图，不把8760小时平均成日数据，避免掩盖充放电过程。
plotStartHour = 1;                        % 从第几个仿真小时开始，范围1至T
plotHours = min(168,T);                   % 展示小时数；设为T可查看整个仿真周期
plotRows = plotStartHour:min(T,plotStartHour+plotHours-1);
plotTime = Time(plotRows);

% 每列数据对应一种设备，均先汇总至分区。发电为正，充电和抽水为负。
% 友好电站拆分为站内风电、光伏和储能：风光计入直送及充电前的已利用功率，
% 再扣除站内储能充电，四项之和恰好等于电站对外出力，不重复计算。
plotDeviceByType = { ...
    A_th*P_th_result, A_gas*P_gas_result, A_bio*P_bio_result, ...
    A_hyd_ror*value(P_hyd_ror), A_hyd_res*value(P_hyd_res), ...
    A_nuc*P_nuc_result, P_wind_result, P_pv_result, P_csp_result, ...
    A_fr*value(P_fr_wind_out+P_fr_wind_ch), ...
    A_fr*value(P_fr_pv_out+P_fr_pv_ch), A_fr*value(P_fr_discharge), ...
    A_ps*P_ps_gen_result, A_es*P_es_discharge_result, ...
    -A_ps*P_ps_pump_result, -A_es*P_es_charge_result, -A_fr*value(P_fr_charge), ...
    P_tr_result, P_inter_result, P_trans, P_shed_result};
% 单分区或空设备乘积可能返回稀疏矩阵；三维拼接前统一转为普通数值矩阵。
plotDeviceByType = cellfun(@full,plotDeviceByType,'UniformOutput',false);
plotDeviceData = cat(3,plotDeviceByType{:});
plotDeviceNames = {'煤电','气电','生物质','径流式水电','库容式水电', ...
    '核电','风电','光伏','光热','友好电站风电','友好电站光伏','友好电站放电', ...
    '抽蓄发电','电化学储能放电','抽蓄抽水','电化学储能充电','友好电站充电', ...
    '省内净交换','省间互济','既定跨区输电','未供电负荷'};
% 同类储能的充、放电使用相同颜色，位置由正负半轴区分。
plotColors = [ ...
    0.36 0.40 0.46; 0.75 0.49 0.32; 0.50 0.62 0.32; ...
    0.36 0.72 0.86; 0.14 0.48 0.72; 0.65 0.51 0.72; ...
    0.30 0.66 0.51; 0.95 0.73 0.25; 0.89 0.45 0.23; ...
    0.57 0.78 0.64; 0.98 0.85 0.49; 0.76 0.38 0.55; ...
    0.27 0.42 0.70; 0.38 0.69 0.70; 0.27 0.42 0.70; ...
    0.38 0.69 0.70; 0.76 0.38 0.55; 0.66 0.70 0.76; ...
    0.49 0.47 0.65; 0.71 0.63 0.48; 0.85 0.26 0.30];

% 先绘制全系统概览，多省时再分省绘制，最后分别绘制各分区。
plotScopeMap = ones(1,Narea);
plotScopeNames = "海南全省";
if Nprovince>1
    plotScopeMap = [plotScopeMap;full(A_province)];
    plotScopeNames = [plotScopeNames;ProvinceID+"省级汇总"];
end
if Narea>1
    plotScopeMap = [plotScopeMap;eye(Narea)];
    plotScopeNames = [plotScopeNames;AreaName+"分区"];
end
operationFigures = gobjects(size(plotScopeMap,1),1);
plotNetLoad = value(P_D);                 % 转移、削减后的实际负荷，与功率平衡一致

for g = 1:size(plotScopeMap,1)
    generationBars = zeros(numel(plotRows),numel(plotDeviceNames));
    for k = 1:numel(plotDeviceNames)
        generationBars(:,k) = (plotScopeMap(g,:)*plotDeviceData(:,plotRows,k))';
    end
    generationBars(abs(generationBars)<1e-6) = 0; % 仅隐藏求解浮点噪声，不修改原始结果
    visibleDevices = any(abs(generationBars)>0,1);
    originalLoad = (plotScopeMap(g,:)*P_load(:,plotRows))';
    actualLoad = (plotScopeMap(g,:)*plotNetLoad(:,plotRows))';

    operationFigures(g) = figure('Color','w','Name',char(plotScopeNames(g)+"设备运行"), ...
        'NumberTitle','off','Position',[80 80 1450 700]);
    chartLayout = tiledlayout(operationFigures(g),1,1,'Padding','compact','TileSpacing','compact');
    ax = nexttile(chartLayout);
    hold(ax,'on');
    x = 1:numel(plotRows);
    loadMarker = 'none';
    if numel(plotRows)==1
        loadMarker = 'o';                 % 单小时也能看见负荷值
    end
    % MATLAB stacked柱状图分别在零线上下堆叠正负值，交换功率也按实际方向显示。
    if any(visibleDevices)
        bars = bar(ax,x,generationBars(:,visibleDevices),'stacked','BarWidth',0.96,'EdgeColor','none');
        visibleNames = plotDeviceNames(visibleDevices);
        visibleColors = plotColors(visibleDevices,:);
        for k = 1:numel(bars)
            bars(k).FaceColor = visibleColors(k,:);
            bars(k).DisplayName = visibleNames{k};
        end
    end
    plot(ax,x,actualLoad,'-','Color',[0.13 0.16 0.20],'LineWidth',1.8,'Marker',loadMarker,'DisplayName','响应后负荷');
    if any(abs(originalLoad-actualLoad)>1e-6)
        plot(ax,x,originalLoad,'--','Color',[0.46 0.47 0.49],'LineWidth',1.3,'Marker',loadMarker,'DisplayName','原始负荷');
    end
    yline(ax,0,'-','Color',[0.25 0.28 0.32],'LineWidth',0.8,'HandleVisibility','off');
    tickRows = unique(round(linspace(1,numel(plotRows),min(9,numel(plotRows)))));
    ax.XTick = tickRows;
    ax.XTickLabel = cellstr(datestr(plotTime(tickRows),'mm/dd HH:MM'));
    ax.XLim = [0.4,numel(plotRows)+0.6];
    ax.FontName = 'Microsoft YaHei';
    ax.FontSize = 11;
    ax.TickDir = 'out';
    ax.Box = 'off';
    ax.YGrid = 'on';
    ax.XGrid = 'off';
    ax.GridAlpha = 0.12;
    ax.Layer = 'top';
    ylabel(ax,'功率 / MW');
    xlabel(ax,'时间（月/日 时:分）');
    title(ax,plotScopeNames(g)+"设备运行",'FontSize',16,'FontWeight','bold');
    subtitle(ax,sprintf('%s 至 %s  |  逐小时出力；充电、抽水及净送出位于负半轴', ...
        datestr(plotTime(1),'yyyy-mm-dd HH:MM'),datestr(plotTime(end),'yyyy-mm-dd HH:MM')), ...
        'FontSize',10,'Color',[0.40 0.43 0.48]);
    lgd = legend(ax,'show','Location','southoutside','NumColumns',6,'Box','off','FontSize',10);
    lgd.Layout.Tile = 'south';
    hold(ax,'off');
end
% operationFigures保留各图句柄。需要导出时可直接使用：
% exportgraphics(operationFigures(1),'全系统设备运行.png','Resolution',300);

%% 八、成本构成分析
% 六大项与Objective逐项对应。排放已包含在发电成本，不能再次加到六大项之外。
% 友好站储能老化费用按原目标函数计在发电项；独立储能和抽蓄计在储能项。
% 金额统计覆盖全部T小时，显示单位万元；固定运维已按T/8760折算。
costNames = ["发电成本";"储能成本";"需求响应";"弃电惩罚";"失负荷惩罚";"省间互济"];
costYuan = value([C_gen;C_sto;C_DR;C_curt;C_shed;C_inter]);
costShare = nan(size(costYuan));
if abs(TotalCost)>1e-8, costShare = 100*costYuan/TotalCost; end
CostSummary = table(costNames,costYuan,costYuan/1e4,costShare, ...
    'VariableNames',{'Category','Cost_Yuan','Cost_WanYuan','Share_Percent'});

% 按费用性质重新分组，与上述六大项是同一总成本的两种视角，不能相加。
C_fixed_all = C_th_FOM+C_gas_FOM+C_bio_FOM+C_nuc_FOM+C_hyd+C_es_FOM+C_ps + ...
    C_wind+C_pv+C_fr_FOM+fixedCostScale*sum(cspData.FOM_COST_PERIOD.*P_csp_max);
C_variable_all = C_th_fuel+C_gas_gen+C_bio_gen+C_nuc_gen + ...
    costScale*Delta_t*sum(cspData.GEN_COST'*P_csp_unit);
detailNames = ["燃料及变动发电";"启动与停机";"固定运维";"碳排放";"大气污染物"; ...
    "电池老化";"需求响应";"弃电惩罚";"失负荷惩罚";"省间互济"];
detailYuan = value([C_variable_all;C_th_start+C_th_stop+C_gas_start+C_gas_stop; ...
    C_fixed_all;C_CO2;C_pollution;C_es_degrade+C_fr_degrade;C_DR;C_curt;C_shed;C_inter]);
CostDetail = table(detailNames,detailYuan,detailYuan/1e4, ...
    'VariableNames',{'Category','Cost_Yuan','Cost_WanYuan'});
CostReconciliationError = [sum(costYuan)-TotalCost;sum(detailYuan)-TotalCost];
fprintf('\n成本构成（全部%d小时，万元）：\n',T);
disp(CostSummary);
fprintf('两种成本分解与目标函数的核对差额：%.6g / %.6g 元。\n',CostReconciliationError);

analysisFigures = gobjects(0,1);
analysisFileNames = strings(0,1);
f = figure('Color','w','Name','海南成本构成','Position',[80 80 1450 720]);
tl = tiledlayout(f,1,2,'Padding','compact','TileSpacing','compact');
ax = nexttile(tl);
barh(ax,costYuan/1e4,0.65,'FaceColor',[0.16 0.43 0.65],'EdgeColor','none');
ax.YTick = 1:numel(costNames); ax.YTickLabel = costNames; ax.YDir = 'reverse';
xlabel(ax,'成本 / 万元'); title(ax,'目标函数六大项');
ax = nexttile(tl);
barh(ax,detailYuan/1e4,0.65,'FaceColor',[0.21 0.60 0.51],'EdgeColor','none');
ax.YTick = 1:numel(detailNames); ax.YTickLabel = detailNames; ax.YDir = 'reverse';
xlabel(ax,'成本 / 万元'); title(ax,'按费用性质拆分');
title(tl,sprintf('海南成本构成｜全周期%d小时，总成本 %.2f 万元',T,TotalCost/1e4));
subtitle(tl,'两幅图为同一总成本的不同分解；固定运维按本次时长折算');
analysisFigures(end+1,1) = f; analysisFileNames(end+1,1) = "01_成本构成";

%% 九、煤电与气电启停分析
% 启停次数直接取v/w，不通过出力是否为零推断。统计包含第一个小时相对初态的切换。
% 检修为外生计划，不是经济停机；状态图使用单独颜色标出。
u_thermal_result = round(value([u_th;u_gas]));
v_thermal_result = round(value([v_th;v_gas]));
w_thermal_result = round(value([w_th;w_gas]));
thermalMaintenance = [m_th;m_gas];
thermalPower = [P_th_result;P_gas_result];
thermalRated = [P_th_rated;P_gas_rated];
thermalKeys = [string(thData.UnitKey);string(gasData.UnitKey)];
thermalNames = [string(thData.NAME);string(gasData.NAME)];
thermalTypes = [repmat("煤电",N_th,1);repmat("气电",N_gas,1)];
ThermalSummary = table(thermalKeys,thermalNames,thermalTypes, ...
    sum(v_thermal_result,2),sum(w_thermal_result,2),sum(u_thermal_result,2)*Delta_t, ...
    sum(thermalMaintenance,2)*Delta_t,sum(thermalPower,2)*Delta_t, ...
    sum(thermalPower,2)*Delta_t./thermalRated, ...
    'VariableNames',{'UnitKey','Name','Type','Starts','Stops','Online_h','Maintenance_h','Energy_MWh','EquivalentFullLoad_h'});
if ~isempty(thermalKeys)
    f = figure('Color','w','Name','海南火电启停','Position',[80 60 1500 900]);
    tl = tiledlayout(f,2,2,'Padding','compact','TileSpacing','compact');
    ax = nexttile(tl,[1 2]);
    stateDisplay = u_thermal_result(:,plotRows);
    stateDisplay(thermalMaintenance(:,plotRows)>0.5) = 2;
    imagesc(ax,1:numel(plotRows),1:numel(thermalKeys),stateDisplay,[0 2]);
    colormap(ax,[0.91 0.93 0.95;0.19 0.55 0.72;0.92 0.62 0.27]);
    cb = colorbar(ax); cb.Ticks = [0 1 2]; cb.TickLabels = {'停机','运行','检修'};
    ax.YTick = 1:numel(thermalKeys); ax.YTickLabel = thermalKeys+" "+thermalNames;
    ax.XTick = tickRows; ax.XTickLabel = cellstr(datestr(plotTime(tickRows),'mm/dd HH:MM'));
    title(ax,'逐台运行状态'); xlabel(ax,'时间（月/日 时:分）');
    ax = nexttile(tl); hold(ax,'on');
    stairs(ax,1:numel(plotRows),sum(u_thermal_result(1:N_th,plotRows),1),'LineWidth',1.7);
    stairs(ax,1:numel(plotRows),sum(u_thermal_result(N_th+1:end,plotRows),1),'LineWidth',1.7);
    ax.XTick = tickRows; ax.XTickLabel = cellstr(datestr(plotTime(tickRows),'mm/dd HH:MM'));
    ax.XLim = [0.5,numel(plotRows)+0.5]; ylabel(ax,'在线机组数 / 台');
    legend(ax,{'煤电','气电'},'Location','best','Box','off'); title(ax,'在线机组数量');
    ax = nexttile(tl);
    eventCount = [sum(v_thermal_result(1:N_th,:),'all'),sum(w_thermal_result(1:N_th,:),'all'); ...
        sum(v_thermal_result(N_th+1:end,:),'all'),sum(w_thermal_result(N_th+1:end,:),'all')];
    bar(ax,eventCount,'grouped'); ax.XTick = [1 2]; ax.XTickLabel = {'煤电','气电'};
    ylabel(ax,'次数'); legend(ax,{'启动','停机'},'Location','best','Box','off');
    title(ax,sprintf('全部%d小时启停次数（含初态切换）',T));
    title(tl,'海南火电运行与启停');
    analysisFigures(end+1,1) = f; analysisFileNames(end+1,1) = "02_火电启停";
end

%% 十、储能充放电及SOC分析
% E(:,1)为初始能量，E(:,t+1)为第t小时结束能量。SOC图包括首尾T+1个边界点。
% 每台设备单独绘制SOC；抽蓄SOC是等效水库能量比例，不是实测水位比例。
% 表中等效放电循环=累计放电量/额定能量，是输出侧等效次数，不作为寿命损耗模型。
storageTypes = ["抽水蓄能";"独立电化学储能";"友好电站储能"];
storageNames = {string(psData.NAME),string(esData.NAME),string(friendlyData.NAME)};
storageKeys = {string(psData.UnitKey),string(esData.UnitKey),string(friendlyData.UnitKey)};
storageEnergy = {E_ps_result,E_es_result,E_fr_result};
storageRatedEnergy = {E_ps_max,E_es_max,E_fr_max};
storageCharge = {P_ps_pump_result,P_es_charge_result,value(P_fr_charge)};
storageDischarge = {P_ps_gen_result,P_es_discharge_result,value(P_fr_discharge)};
storageSOCMin = {SOC_ps_min,esData.SOC_MIN,friendlyData.SOC_MIN};
storageSOCMax = {SOC_ps_max,esData.SOC_MAX,friendlyData.SOC_MAX};
storageRows = cell(0,9);
for s = 1:3
    if isempty(storageRatedEnergy{s}), continue; end
    soc = 100*storageEnergy{s}./storageRatedEnergy{s};
    for i = 1:numel(storageRatedEnergy{s})
        chargeMWh = sum(storageCharge{s}(i,:))*Delta_t;
        dischargeMWh = sum(storageDischarge{s}(i,:))*Delta_t;
        storageRows(end+1,:) = {storageTypes(s),storageKeys{s}(i),storageNames{s}(i), ...
            chargeMWh,dischargeMWh,min(soc(i,:)),max(soc(i,:)), ...
            dischargeMWh/storageRatedEnergy{s}(i),storageEnergy{s}(i,end)-storageEnergy{s}(i,1)};
    end
    f = figure('Color','w','Name',char(storageTypes(s)+"运行"),'Position',[100 80 1450 800]);
    tl = tiledlayout(f,2,1,'Padding','compact','TileSpacing','compact');
    ax = nexttile(tl); hold(ax,'on');
    powerBars = [sum(storageDischarge{s}(:,plotRows),1)',-sum(storageCharge{s}(:,plotRows),1)'];
    b = bar(ax,1:numel(plotRows),powerBars,'stacked','EdgeColor','none','BarWidth',0.9);
    b(1).FaceColor = [0.18 0.49 0.70]; b(2).FaceColor = [0.25 0.70 0.65];
    yline(ax,0,'Color',[0.4 0.4 0.4],'HandleVisibility','off');
    legend(ax,{'发电/放电','抽水/充电'},'Location','northoutside','Orientation','horizontal','Box','off');
    ax.XLim = [0.5,numel(plotRows)+0.5]; ax.XTick = tickRows;
    ax.XTickLabel = cellstr(datestr(plotTime(tickRows),'mm/dd HH:MM'));
    ylabel(ax,'功率 / MW'); title(ax,'同类型功率汇总，充电为负');
    ax = nexttile(tl); hold(ax,'on');
    boundaryRows = plotRows(1):(plotRows(end)+1);
    boundaryTime = Time(1)+hours((boundaryRows-1)*Delta_t);
    boundaryX = 0:numel(plotRows);
    colors = lines(size(soc,1));
    for i = 1:size(soc,1)
        plot(ax,boundaryX,soc(i,boundaryRows),'Color',colors(i,:),'LineWidth',1.7, ...
            'DisplayName',char(storageNames{s}(i)));
        yline(ax,100*storageSOCMin{s}(i),'--','Color',colors(i,:),'HandleVisibility','off');
        yline(ax,100*storageSOCMax{s}(i),'--','Color',colors(i,:),'HandleVisibility','off');
    end
    boundaryTicks = unique(round(linspace(1,numel(boundaryRows),min(9,numel(boundaryRows)))));
    ax.XTick = boundaryTicks-1; ax.XTickLabel = cellstr(datestr(boundaryTime(boundaryTicks),'mm/dd HH:MM'));
    ax.XLim = [0,numel(plotRows)]; ylim(ax,[0 100]); ylabel(ax,'SOC / %');
    xlabel(ax,'时段边界时间（月/日 时:分）'); title(ax,'逐台SOC轨迹，虚线为各自上下限');
    legend(ax,'show','Location','northoutside','Orientation','horizontal','Box','off');
    title(tl,"海南"+storageTypes(s)+"运行分析");
    analysisFigures(end+1,1) = f; analysisFileNames(end+1,1) = sprintf('%02d_%s',s+2,storageTypes(s));
end
StorageSummary = cell2table(storageRows,'VariableNames',{'Type','UnitKey','Name','Charge_MWh', ...
    'Discharge_MWh','MinSOC_Percent','MaxSOC_Percent','EquivalentDischargeCycles','TerminalEnergyError_MWh'});

%% 十一、各设备出力及风光消纳分析
% 发电/放电量列包含储能放电，不将其解释为一次能源电量占比。
% 友好站按净上网功率计一次，不与站内风光和放电重复计入设备汇总。
deviceNames = ["煤电";"气电";"生物质";"核电";"常规水电";"普通风电";"普通光伏"; ...
    "光热";"新能源友好站净上网";"抽蓄发电";"独立储能放电"];
devicePower = [sum(P_th_result,1);sum(P_gas_result,1);sum(P_bio_result,1);sum(P_nuc_result,1); ...
    sum(P_hyd_result,1);sum(P_wind_result,1);sum(P_pv_result,1);sum(P_csp_result,1); ...
    sum(P_fr_result,1);sum(P_ps_gen_result,1);sum(P_es_discharge_result,1)];
DeviceSummary = table(deviceNames,sum(devicePower,2)*Delta_t,max(devicePower,[],2), ...
    'VariableNames',{'Device','OutputEnergy_MWh','PeakOutput_MW'});
f = figure('Color','w','Name','海南各设备出力统计','Position',[100 80 1400 650]);
tl = tiledlayout(f,1,2,'Padding','compact','TileSpacing','compact');
ax = nexttile(tl); barh(ax,DeviceSummary.OutputEnergy_MWh/1e4,'FaceColor',[0.19 0.55 0.72],'EdgeColor','none');
ax.YTick = 1:numel(deviceNames); ax.YTickLabel = deviceNames; ax.YDir = 'reverse';
xlabel(ax,'发电/放电量 / 万MWh'); title(ax,'全周期电量');
ax = nexttile(tl); barh(ax,DeviceSummary.PeakOutput_MW,'FaceColor',[0.30 0.65 0.52],'EdgeColor','none');
ax.YTick = 1:numel(deviceNames); ax.YTickLabel = deviceNames; ax.YDir = 'reverse';
xlabel(ax,'最大出力 / MW'); title(ax,'全周期最大出力');
title(tl,sprintf('海南设备出力统计｜全部%d小时',T));
subtitle(tl,'储能放电单列；友好站计净上网量，不能将本图解释为一次能源结构');
analysisFigures(end+1,1) = f; analysisFileNames(end+1,1) = "06_设备出力统计";

% 风光消纳包含独立电站与友好站，站内充电视为已利用，保持新能源约束原口径。
renewAvailable = [sum(P_wind_available,1)+sum(P_fr_wind_available,1); ...
    sum(P_pv_available,1)+sum(P_fr_pv_available,1)];
renewCurt = [sum(P_wind_curt_result,1)+sum(value(P_fr_wind_curt),1); ...
    sum(P_pv_curt_result,1)+sum(value(P_fr_pv_curt),1)];
renewRatio = nan(2,1); renewEnergy = sum(renewAvailable,2)*Delta_t;
renewCurtailed = sum(renewCurt,2)*Delta_t;
renewRatio(renewEnergy>0) = 100*(1-renewCurtailed(renewEnergy>0)./renewEnergy(renewEnergy>0));
RenewableSummary = table(["风电";"光伏"],renewEnergy,renewCurtailed,renewRatio, ...
    'VariableNames',{'Type','Available_MWh','Curtailed_MWh','Utilization_Percent'});
f = figure('Color','w','Name','海南风光消纳','Position',[100 80 1450 750]);
tl = tiledlayout(f,2,1,'Padding','compact','TileSpacing','compact');
for k = 1:2
    ax = nexttile(tl); hold(ax,'on');
    plot(ax,1:numel(plotRows),renewAvailable(k,plotRows),'--','Color',[0.55 0.57 0.61],'LineWidth',1.5);
    plot(ax,1:numel(plotRows),renewAvailable(k,plotRows)-renewCurt(k,plotRows),'-','Color',[0.17 0.57 0.49],'LineWidth',1.7);
    bar(ax,1:numel(plotRows),renewCurt(k,plotRows),0.6,'FaceColor',[0.92 0.57 0.29],'EdgeColor','none','FaceAlpha',0.55);
    ax.XLim = [0.5,numel(plotRows)+0.5]; ax.XTick = tickRows;
    ax.XTickLabel = cellstr(datestr(plotTime(tickRows),'mm/dd HH:MM')); ylabel(ax,'功率 / MW');
    legend(ax,{'可用功率','已利用功率','弃电功率'},'Location','northoutside','Orientation','horizontal','Box','off');
    title(ax,sprintf('%s｜全周期利用率 %.2f%%',RenewableSummary.Type(k),renewRatio(k)));
end
title(tl,'海南风光消纳（包含友好电站）');
analysisFigures(end+1,1) = f; analysisFileNames(end+1,1) = "07_风光消纳";

%% 十二、分析结果保存
% 统计表均保存数值，便于继续分析或与Python逐项核对；无须再次求解即可读取。
% 每次运行建立独立时间戳目录，避免不同仿真周期或不同可行解的图像相互覆盖。
analysisDir = fullfile(projectDir,'分析结果', ...
    sprintf('%s_%dh_%s',datestr(Time(1),'yyyymmdd_HHMM'),T,char(datetime('now','Format','yyyyMMdd_HHmmss_SSS'))));
if ~exist(analysisDir,'dir'), mkdir(analysisDir); end
for k = 1:numel(analysisFigures)
    axesList = findall(analysisFigures(k),'Type','axes');
    for j = 1:numel(axesList)
        axesList(j).FontName = 'Microsoft YaHei'; axesList(j).FontSize = 10;
        axesList(j).Box = 'off'; axesList(j).TickDir = 'out';
        axesList(j).TickLabelInterpreter = 'none';  % 机组编号中的下划线按原文显示。
        if ~strcmp(analysisFileNames(k),"02_火电启停")
            grid(axesList(j),'on'); axesList(j).GridAlpha = 0.12;
        end
    end
    exportgraphics(analysisFigures(k),fullfile(analysisDir,analysisFileNames(k)+".png"),'Resolution',180);
    savefig(analysisFigures(k),fullfile(analysisDir,analysisFileNames(k)+".fig"));
end
for g = 1:numel(operationFigures)
    exportgraphics(operationFigures(g),fullfile(analysisDir,sprintf('00_设备堆叠_%d.png',g)),'Resolution',180);
end
AnalysisRun = struct('StartTime',Time(1),'EndTime',Time(end),'Hours',T,'Hydrology',hydrologyScenario, ...
    'TotalCost_Yuan',TotalCost,'SolverInfo',solution.info,'MIPGap',NaN, ...
    'PlotStartTime',plotTime(1),'PlotEndTime',plotTime(end),'EnergyNotServed_MWh',EnergyNotServed);
if isfield(solverResult,'mipgap'), AnalysisRun.MIPGap = solverResult.mipgap; end
% 自动生成本次运行的文字摘要；统计范围为整个求解周期，不限于图像窗口。
reportFile = fullfile(analysisDir,'分析摘要.txt');
reportFID = fopen(reportFile,'w','n','UTF-8');
if reportFID < 0, error('无法创建分析摘要：%s',reportFile); end
fprintf(reportFID,'海南算例分析\n仿真起点：%s；共%d小时。\n',datestr(Time(1),'yyyy-mm-dd HH:MM'),T);
fprintf(reportFID,'总成本：%.4f万元；实际相对求解间隙：%.4f%%。\n',TotalCost/1e4,100*AnalysisRun.MIPGap);
fprintf(reportFID,'时序图窗口：%s 至 %s（小时起点）；统计表覆盖全部求解时段。\n\n', ...
    datestr(plotTime(1),'yyyy-mm-dd HH:MM'),datestr(plotTime(end),'yyyy-mm-dd HH:MM'));
fprintf(reportFID,'一、成本构成（与目标函数一致）\n');
for k = 1:height(CostSummary)
    fprintf(reportFID,'%s：%.4f万元，占总成本%.2f%%。\n',costNames(k),costYuan(k)/1e4,CostSummary.Share_Percent(k));
end
fprintf(reportFID,'\n按费用性质拆分如下，与上述六项为同一总成本的两种分解，不能重复相加。\n');
for k = 1:height(CostDetail)
    fprintf(reportFID,'%s：%.4f万元。\n',CostDetail.Category(k),CostDetail.Cost_WanYuan(k));
end
fprintf(reportFID,'固定运维按本次仿真时长折算；碳排放及污染物费用已包含在发电成本中。\n');
fprintf(reportFID,'电池老化为模型中的退化费用；充放电效率损耗通过能量平衡影响调度，不另加一笔损耗费用。\n');
fprintf(reportFID,'成本分项与目标函数核对差额：%.6g / %.6g元。\n\n',CostReconciliationError);
fprintf(reportFID,'二、火电运行\n煤电和气电合计启动%d次、停机%d次。\n',sum(ThermalSummary.Starts),sum(ThermalSummary.Stops));
fprintf(reportFID,'启停次数包含首时段相对初始状态的切换；检修单列，不将其全部归因于经济调峰。\n逐台结果见MAT文件中的ThermalSummary。\n\n');
fprintf(reportFID,'三、储能运行\n');
for k = 1:height(StorageSummary)
    fprintf(reportFID,'%s / %s：充电%.2fMWh，放电%.2fMWh，SOC范围%.2f%%—%.2f%%，放电侧等效循环%.3f次，首末能量差%.6gMWh。\n', ...
        StorageSummary.Type(k),StorageSummary.Name(k),StorageSummary.Charge_MWh(k),StorageSummary.Discharge_MWh(k), ...
        StorageSummary.MinSOC_Percent(k),StorageSummary.MaxSOC_Percent(k), ...
        StorageSummary.EquivalentDischargeCycles(k),StorageSummary.TerminalEnergyError_MWh(k));
end
fprintf(reportFID,'SOC=储存能量/额定能量；抽蓄为等效能量比例，并非实际水位。等效循环=放电量/额定能量，不等同于寿命模型循环次数。\n\n');
fprintf(reportFID,'四、风光消纳与供电\n');
for k = 1:height(RenewableSummary)
    fprintf(reportFID,'%s：可用电量%.2fMWh，弃电%.2fMWh，利用率%.2f%%。\n', ...
        RenewableSummary.Type(k),renewEnergy(k),renewCurtailed(k),renewRatio(k));
end
fprintf(reportFID,'风光统计包含友好电站，站内充电计入已利用电量；未供电电量%.2fMWh。\n',EnergyNotServed);
fprintf(reportFID,'以上仅对应本次周期与参数，不代表全年运行结论；有限求解间隙下，逐时调度可能存在多个近优方案。\n');
fclose(reportFID);
save(fullfile(analysisDir,'海南分析结果.mat'),'AnalysisRun','CostSummary','CostDetail','CostReconciliationError', ...
    'ThermalSummary','StorageSummary','DeviceSummary','RenewableSummary','Time', ...
    'u_thermal_result','v_thermal_result','w_thermal_result','thermalMaintenance','thermalPower', ...
    'storageEnergy','storageRatedEnergy','storageCharge','storageDischarge','storageKeys', ...
    'renewAvailable','renewCurt','P_load','P_shift_in_result','P_shift_out_result','P_cut_result','P_shed_result');
fprintf('\n火电启停统计（全部%d小时）：\n',T); disp(ThermalSummary);
fprintf('储能运行统计（全部%d小时）：\n',T); disp(StorageSummary);
fprintf('风光消纳统计（全部%d小时）：\n',T); disp(RenewableSummary);
fprintf('分析图像、可编辑FIG和数值MAT已保存到：\n%s\n',analysisDir);
