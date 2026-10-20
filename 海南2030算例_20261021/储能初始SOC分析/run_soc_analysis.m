function run_soc_analysis(maxCases)
% 初始SOC敏感性批量计算。仅操作本文件所在子文件夹，不改父目录模型。
% 运行方式：在MATLAB中打开本目录，将其设为当前目录，执行run_soc_analysis。
% 每个场景保留独立日志、MAT和JSON；已有JSON会跳过，便于断点继续。
studyDir = fileparts(mfilename('fullpath'));
if nargin<1,maxCases=1;end % 默认每进程一个场景，规避求解器反复调用后的内存错误
completedCases=0;
resultDir = fullfile(studyDir,'场景结果');
if ~exist(resultDir,'dir'), mkdir(resultDir); end
for studyHours = [24 168 336] % 720小时暂不分析，待边界处理方式确定后再补充
    for studySOC = [0.5 0.2 0.8]
        studyKey = sprintf('H%03d_SOC%02d',studyHours,round(100*studySOC));
        studyExisting=dir(fullfile(resultDir,[studyKey '.json']));
        if ~isempty(studyExisting) && studyExisting.bytes>0
            priorRecord=jsondecode(fileread(fullfile(resultDir,[studyKey '.json'])));
            if priorRecord.HasFeasible && (studyHours<720 || isfield(priorRecord,'WarmStart'))
                fprintf('SKIP %s\n',studyKey);continue;
            end
        end
        fprintf('START %s\n',studyKey);
        solve_soc_case(studyDir,resultDir,studyHours,studySOC,studyKey);
        completedCases=completedCases+1;
        if completedCases>=maxCases,return;end
    end
end
fprintf('SOC_STUDY_FINISHED\n');
end

function solve_soc_case(studyDir,resultDir,studyHours,studySOC,studyKey)
% 读取本目录模型的输入、变量、约束和目标，沿用全部建模逻辑。
% 仅覆盖仿真小时数和socStudyInitial；不用重新运行原脚本的大量绘图。
yalmip('clear');
fid=fopen(fullfile(studyDir,'synergy_hainan_30.m'),'rb');
studySource=native2unicode(fread(fid,Inf,'*uint8')','UTF-8'); fclose(fid);
studySource=strrep(studySource,'clear;','');
studySource=char(strrep(studySource,"projectDir = fileparts(mfilename('fullpath'));", ...
    "projectDir = '"+string(studyDir)+"';"));
studySource=regexprep(studySource,'T = \d+;',sprintf('T = %d;',studyHours),'once');
studySource=regexprep(studySource,'socStudyInitial = [0-9.]+;',sprintf('socStudyInitial = %.2f;',studySOC),'once');
studyCuts=strfind(studySource,'%% ');
eval(studySource(1:studyCuts(5)-1));
studyTimeLimit=120;
if studyHours>=336,studyTimeLimit=1200;end
studyOptions=sdpsettings('solver','gurobi','verbose',1,'savesolveroutput',1, ...
    'gurobi.TimeLimit',studyTimeLimit,'gurobi.MIPGap',0.0001,'gurobi.Threads',4, ...
    'gurobi.Seed',0,'gurobi.MIPFocus',1,'gurobi.Heuristics',0.2,'gurobi.LogToConsole',0, ...
    'gurobi.LogFile',fullfile(resultDir,[studyKey '.log']));
if studyHours>=720
    % 仅提供部分整数搜索起点，不增加约束，不固定机组状态。
    % 可用火电先尝试开机，未赋值的变量由Gurobi补全或修复。
    assign(u_th,1-m_th);
    studyGasStart=1-m_gas;
    if N_gas>0
        % 每小时预留一台可用气电作冷备用，避免“全部开机”明显违反备用要求。
        [~,studyStandby]=max(repmat(P_gas_max,1,T).*(1-m_gas),[],1);
        studyGasStart(sub2ind(size(studyGasStart),studyStandby,1:T))=0;
    end
    assign(u_gas,studyGasStart);
    studyOptions.usex0=1;
    studyOptions.gurobi.StartNodeLimit=1000;
end
studyTimer=tic;
studySolution=optimize(Constraints,Objective,studyOptions);
studyRuntime=toc(studyTimer);
studySolver=studySolution.solveroutput;
if isfield(studySolver,'result'),studySolver=studySolver.result;end
studyRecord=struct('Key',studyKey,'Hours',studyHours,'InitialSOC',studySOC, ...
    'Problem',studySolution.problem,'Info',studySolution.info,'Runtime',studyRuntime, ...
    'HasFeasible',false,'TimeLimit',studyTimeLimit,'TargetGap',0.0001);
if studyHours>=720,studyRecord.WarmStart='thermal units on with one gas unit reserved; partial start only';end
if isfield(studySolver,'x') && ~isempty(studySolver.x) && isfinite(value(Objective))
    studyResidual=check(Constraints);
    studyRecord.HasFeasible=all(isfinite(studyResidual)) && min(studyResidual)>=-1e-4;
    studyRecord.MinResidual=min(studyResidual);
end
if studyRecord.HasFeasible
    studyRecord.Cost=value(Objective);
    studyRecord.Gap=studySolver.mipgap;
    % Gurobi目标可能省略常数项，补回相同常数，保证上下界口径一致。
    studyRecord.LowerBound=studySolver.objbound+studyRecord.Cost-studySolver.objval;
    studyRecord.LoadMWh=sum(P_load(:))*Delta_t;
    studyRecord.CostPerMWh=studyRecord.Cost/studyRecord.LoadMWh;
    studyRecord.CostParts=value([C_gen C_sto C_DR C_curt C_shed C_inter]);
    studyRecord.AvailableRE=sum(P_RE_available(:))*Delta_t;
    studyRecord.CurtailedRE=sum(value(P_RE_curt(:)))*Delta_t;
    studyRecord.ShedMWh=sum(value(P_shed(:)))*Delta_t;
    studyRecord.EsCapacityMW=sum(P_es_max);
    studyRecord.EsEnergyMWh=sum(E_es_max);
    studyRecord.EsSOC=value(E_es)./E_es_max;
    studyRecord.EsCharge=value(P_es_charge);
    studyRecord.EsDischarge=value(P_es_discharge);
    studyRecord.EsChargeMWh=sum(studyRecord.EsCharge(:))*Delta_t;
    studyRecord.EsDischargeMWh=sum(studyRecord.EsDischarge(:))*Delta_t;
    studyRecord.EsTerminalError=max(abs(value(E_es(:,end))-E_es_initial));
    studyRecord.PsSOC=value(E_ps)./E_ps_max;
    studyRecord.FrSOC=value(E_fr)./E_fr_max;
    studyRecord.PsCharge=value(P_ps_pump); studyRecord.PsDischarge=value(P_ps_gen);
    studyRecord.FrCharge=value(P_fr_charge); studyRecord.FrDischarge=value(P_fr_discharge);
    studyRecord.PsEnergyMWh=sum(E_ps_max); studyRecord.FrEnergyMWh=sum(E_fr_max);
    studyRecord.PsTerminalError=max(abs(value(E_ps(:,end))-E_ps_initial));
    studyRecord.FrTerminalError=max(abs(value(E_fr(:,end))-E_fr_initial));
    studyRecord.Load=sum(P_load,1);
    studyRecord.RECurt=sum(value(P_RE_curt),1);
    studyRecord.ThermalMW=sum(value(P_th),1)+sum(value(P_gas),1);
    studyRecord.NuclearMin=P_nuc_min;
    studyRecord.SOCMin=esData.SOC_MIN;
    studyRecord.SOCMax=esData.SOC_MAX;
    studyRecord.NuclearMax=P_nuc_max;
    save(fullfile(resultDir,[studyKey '.mat']),'studyRecord','studySolution');
end
% 部分YALMIP结果为稀疏double，JSON只接受普通数值数组。
studyFields=fieldnames(studyRecord);
for studyFieldIndex=1:numel(studyFields)
    studyField=studyFields{studyFieldIndex};
    if isnumeric(studyRecord.(studyField)),studyRecord.(studyField)=full(studyRecord.(studyField));end
end
fid=fopen(fullfile(resultDir,[studyKey '.json']),'w','n','UTF-8');
fwrite(fid,jsonencode(studyRecord),'char');fclose(fid);
fprintf('DONE %s feasible=%d runtime=%.1fs\n',studyKey,studyRecord.HasFeasible,studyRuntime);
end
