# 每个场景使用独立MATLAB进程；已完成的非空JSON自动跳过。
param([string]$MatlabExe='D:/matlab/bin/matlab.exe')
$ErrorActionPreference='Stop'
$studyPath=$PSScriptRoot.Replace("'","''")
for($caseNumber=1;$caseNumber -le 9;$caseNumber++) {
    & $MatlabExe -batch "addpath('$studyPath'); run_soc_analysis(1);"
    if($LASTEXITCODE -ne 0){throw "MATLAB场景进程退出，代码 $LASTEXITCODE。已完成场景保留，再次运行可继续。"}
}
