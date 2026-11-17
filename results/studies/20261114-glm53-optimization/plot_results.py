"""Standalone measured-data figures. Uses CPU-only Matplotlib; never calls an endpoint."""
from pathlib import Path
import json, statistics
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from matplotlib.ticker import FixedLocator, FuncFormatter

HERE=Path(__file__).resolve().parent
OUT=Path('C:/tests/gpu-bench/results/studies/20261114-glm53-optimization')
groups=[
 ('XS / Auto RAM / 1500 MiB reserve',['xs1159-cap1-bufdirect1-auto-ctx131-ppauto-pcie1-cap512.json'],'#0072B2','o'),
 ('XS / Auto RAM / 750 MiB reserve',['xs1159-cap1-bufdirect1-auto-ctx131-vram750-pcie1-cap512.json','xs1159-cap1-bufdirect1-auto-ctx131-vram750-pcie1-cap512-long.json'],'#009E73','s'),
 ('XS / 0% optional expert RAM',['xs1159-cap1-bufdirect1-ram0-ctx131-ppauto-pcie1-cap512.json'],'#D55E00','^'),
 ('S24 / Auto RAM / 750 MiB reserve',['s24-cap1-bufdirect1-auto-ctx131-vram750-pcie1-cap512.json','s24-cap1-bufdirect1-auto-ctx131-vram750-pcie1-cap512-long.json'],'#CC79A7','D'),
]
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                     'svg.fonttype':'none','savefig.facecolor':'white'})
fig,axes=plt.subplots(1,2,figsize=(12.8,5.8),layout='constrained')
used=[];cap_notes=[]
for label,names,color,marker in groups:
    reports=[]
    for name in names:
        path=HERE/name
        if not path.exists():continue
        d=json.loads(path.read_text(encoding='utf-8'))
        if not d.get('finished_unix') or d.get('error') or not d.get('results'):continue
        assert d['context']==131072 and d['sampling']['max_tokens']==512
        assert d['sampling']['reasoning_effort']=='none'
        reports.append(d);used.append({'label':label,'file':name})
    rows=[r for d in reports for r in d['results']]
    if not rows:continue
    targets=sorted({r['target'] for r in rows})
    for axis,metric,title in zip(axes,['engine_prefill_tps','engine_decode_tps'],['Native prefill','Native decode']):
        xs=[];med=[];lo=[];hi=[]
        for target in targets:
            rs=[r for r in rows if r['target']==target];assert len(rs)==3
            values=[r[metric] for r in rs];m=statistics.median(values)
            xs.append(statistics.median(r['prompt_tokens'] for r in rs)/1000);med.append(m)
            lo.append(m-min(values));hi.append(max(values)-m)
            if metric=='engine_decode_tps' and any(r['finish_reason']=='length' for r in rs):
                cap_notes.append(label+' at '+str(target//1000)+'k: output-cap hit')
        axis.errorbar(xs,med,yerr=[lo,hi],marker=marker,color=color,capsize=3,linewidth=1.7,label=label)
        axis.set_title(title,fontweight='bold');axis.set_xlabel('Actual prompt tokens (log scale)');axis.set_ylabel('Tokens / second')
        axis.set_xscale('log');axis.xaxis.set_major_locator(FixedLocator([5,20,40,80,120]))
        axis.xaxis.set_major_formatter(FuncFormatter(lambda value,pos:f'{value:g}k'))
        axis.minorticks_off();axis.grid(axis='y',alpha=.2);axis.set_ylim(bottom=0)
handles,labels=axes[0].get_legend_handles_labels()
fig.legend(handles,labels,loc='outside lower center',ncols=2,frameon=False,fontsize=9)
fig.suptitle('GLM-5.3-Flash: two RTX 5070 Ti cards + 64 GiB DDR4\n131,072 reserved context · MTP on · INT8 streamed KV · Samsung 990 PRO\nXS = Maya-XS mixed IQ1_S/IQ2_XXS · median [range], three measured requests\nReasoning off · 512-token output cap · one excluded warm-up per size',fontsize=11,fontweight='bold')
if cap_notes:
    axes[1].text(.97,.10,'120k XS / 750 MiB: one of three answers hit the cap.\nRetrieval passed; complete-answer correctness did not.',
                 ha='right',va='bottom',transform=axes[1].transAxes,fontsize=8,color='#5b5151')
for suffix in ['png','svg']:fig.savefig(OUT/('glm53-context-throughput.'+suffix),dpi=180)
plt.close(fig)
(OUT/'figure-provenance.json').write_text(json.dumps({'script':str(Path(__file__).resolve()),'sources':used,
    'intervals':'Median and full range of three measured requests; one excluded warm-up per size.',
    'scope':'Reasoning none, 512-token cap, no background download. Native phase rates, not client end-to-end throughput.',
    'quality_notes':cap_notes,'matplotlib_version':matplotlib.__version__},indent=2)+'\n',encoding='utf-8')
print(json.dumps({'figure':'glm53-context-throughput','source_cohorts':len(used),'quality_notes':cap_notes}))
