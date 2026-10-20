"""读取批量MATLAB结果，生成初始SOC分析图；聚焦20%/80%两个已收敛场景。
336小时的50%场景在时限内未收敛，不纳入绘图，仅保留在汇总数据中。"""
from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'分析图';OUT.mkdir(exist_ok=True)
records=[json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'场景结果').glob('*.json') if p.stat().st_size]
R={(r['Hours'],round(r['InitialSOC'],1)):r for r in records if r.get('HasFeasible')}
H=[24,168,336]; S=[.2,.8]; colors=['#3578A4','#D47F35']
assert all((h,s) in R for h in H for s in S),'缺少24/168/336小时的20%或80%可行场景'
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei'],'axes.unicode_minus':False,'font.size':10,
 'axes.spines.top':False,'axes.spines.right':False,'axes.titleweight':'bold','figure.facecolor':'white'})
def finish(fig,name):
 fig.savefig(OUT/name,dpi=190,bbox_inches='tight');plt.close(fig)

# 图1：成本随周期收敛 + 影响幅度衰减
fig,axs=plt.subplots(1,2,figsize=(11.5,3.7))
xs=[0,1,2];tick=[f'{h}小时' for h in H]
for s,c in zip(S,colors):
 ys=np.array([R[h,s]['CostPerMWh'] for h in H]);los=np.array([R[h,s]['LowerBound']/R[h,s]['LoadMWh'] for h in H])
 axs[0].errorbar(xs,ys,yerr=[np.maximum(ys-los,0),np.zeros(3)],fmt='-o',color=c,capsize=4,lw=1.6,label=f'初始SOC {s:.0%}')
 for x,y in zip(xs,ys):axs[0].annotate(f'{y:.1f}',(x,y),textcoords='offset points',xytext=(0,8),ha='center',color=c,fontsize=9)
axs[0].set_xticks(xs,tick);axs[0].set_xlabel('仿真周期');axs[0].set_ylabel('单位成本（元/MWh）')
axs[0].set_title('单位成本随周期收敛（竖线=求解间隙）',fontsize=10);axs[0].grid(alpha=.15);axs[0].legend(frameon=False)
diff=np.array([R[h,.8]['CostPerMWh']-R[h,.2]['CostPerMWh'] for h in H])
axs[1].bar(xs,diff,width=.5,color=['#C44E52' if v>0 else '#55A868' for v in diff],alpha=.85)
axs[1].axhline(0,color='#333333',lw=.8)
for k,v in enumerate(diff):axs[1].text(k,v+(0.10 if v>=0 else -0.22),f'{v:+.2f}',ha='center',fontsize=9)
axs[1].set_xticks(xs,tick);axs[1].set_xlabel('仿真周期');axs[1].set_ylabel('单位成本差 SOC80%−SOC20%（元/MWh）')
axs[1].set_title('初始SOC的影响幅度（正=低SOC优，负=高SOC优）',fontsize=10);axs[1].grid(axis='y',alpha=.15)
fig.tight_layout();finish(fig,'01_初始SOC影响随周期衰减.png')

# 图2：弃电与储能使用强度
fig,axs=plt.subplots(1,2,figsize=(11.5,3.6))
for s,c in zip(S,colors):
 axs[0].plot(H,[100*R[h,s]['CurtailedRE']/R[h,s]['AvailableRE'] for h in H],'-o',color=c,label=f'{s:.0%}')
 axs[1].plot(H,[(sum(R[h,s]['PsDischarge'])+sum(R[h,s]['EsDischarge'])+sum(R[h,s]['FrDischarge']))/(h/24) for h in H],'-o',color=c,label=f'{s:.0%}')
for ax in axs:ax.set_xticks(H,[f'{h}h' for h in H]);ax.set_xlabel('仿真周期');ax.grid(alpha=.15);ax.legend(frameon=False)
axs[0].set_ylabel('风光弃电率（%）');axs[0].set_title('风光消纳',fontsize=10)
axs[1].set_ylabel('三类储能日均放电量（MWh/日）');axs[1].set_title('储能使用强度',fontsize=10)
fig.tight_layout();finish(fig,'02_弃电与储能使用.png')

# 图3：三类储能SOC轨迹（20%与80%）
fig,axs=plt.subplots(3,3,figsize=(12.5,7.2),sharey=True)
for row,(field,label) in enumerate([('PsSOC','抽水蓄能'),('EsSOC','独立电化学储能'),('FrSOC','友好电站储能')]):
 for col,h in enumerate(H):
  ax=axs[row,col]
  for s,c in zip(S,colors):ax.plot(np.arange(h+1)/24,np.array(R[h,s][field]).reshape(-1)*100,color=c,lw=1,label=f'{s:.0%}')
  ax.axhline(10,color='#999999',ls='--',lw=.6);ax.axhline(90,color='#999999',ls='--',lw=.6)
  ax.set_ylim(0,100);ax.set_xlim(0,h/24);ax.set_title(f'{label}  {h}小时',fontsize=10);ax.grid(alpha=.13)
  if row==2:ax.set_xlabel('距仿真开始的天数')
  if col==0:ax.set_ylabel('SOC（%）')
axs[0,0].legend(ncol=2,fontsize=8,frameon=False,loc='lower left');fig.tight_layout();finish(fig,'03_三类储能SOC.png')

# 汇总（保留全部9个场景数据，供查证）
summary=[]
for h in H:
 for s in [.2,.5,.8]:
  r=R.get((h,s))
  if r is None:continue
  summary.append({k:r[k] for k in ['Key','Hours','InitialSOC','Cost','LowerBound','Gap','CostPerMWh','CurtailedRE','AvailableRE','ShedMWh','Runtime']})
(ROOT/'汇总指标.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print('FIGURES_READY')
