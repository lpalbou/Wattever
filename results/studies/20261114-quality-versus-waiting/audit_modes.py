"""Offline renderer/Responses parity audit. Imports frontend modules only; no engine/server starts."""
from pathlib import Path
import argparse, hashlib, json, subprocess, sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def worker(family):
    vendor=ROOT/'experimental-maya/source-1.1.59' if family=='glm' else Path('C:/Users/user/AppData/Local/Strata/terminal/current').resolve()
    sys.path.insert(0,str(vendor));sys.path.insert(0,str(vendor/'tools'))
    from serve.frontend import ChatTemplate, effort_kwargs, openai_to_messages
    from serve import responses
    paths=[vendor/'serve/glm5_chat_template.jinja'] if family=='glm' else [ROOT/'models/packs'/name/'tokenizer/chat_template.jinja' for name in ('iq3_s','unsloth-ud-iq4_xs')]
    plan=json.loads((HERE/'plan.json').read_text(encoding='utf-8'))
    probes=[plan['tasks'][0]['prompt'],next(t['prompt'] for t in plan['tasks'] if t['id']=='coding8-stable_unique')]
    result={'family':family,'vendor':str(vendor),'server_sha256':sha(vendor/'serve/server.py'),
            'frontend_sha256':sha(vendor/'serve/frontend.py'),'responses_sha256':sha(vendor/'serve/responses.py'),
            'effort_mapping':{},'parity':[],'engines_started':False}
    for word in ('none','low','medium','high','xhigh','max','auto','invalid'):
        try:result['effort_mapping'][word]={'accepted':True,'template_kwargs':effort_kwargs(word)}
        except ValueError as exc:result['effort_mapping'][word]={'accepted':False,'error':str(exc)}
    for path in paths:
        template=ChatTemplate(path)
        for mode in ('none','low'):
            for prompt in probes:
                req={'model':'audit-only','input':prompt,'reasoning':{'effort':mode},'max_output_tokens':2048,'store':False}
                chat={'messages':[{'role':'user','content':prompt}],'reasoning_effort':mode}
                chat_messages,chat_tools,chat_kwargs=openai_to_messages(chat)
                if family=='glm':
                    normalized=responses.normalize_request(req)
                    response_messages,response_tools,response_kwargs=openai_to_messages(normalized)
                else:
                    response_messages=responses.input_messages(req);response_tools=None
                    response_kwargs=responses.template_kwargs(req,{})
                def render(messages,tools,kwargs):
                    value=template.render(messages,tools=tools,**kwargs)
                    if family=='glm' and kwargs.get('enable_thinking') is False and value.endswith('<think>'):value+='</think>'
                    return value
                first=render(chat_messages,chat_tools,chat_kwargs);second=render(response_messages,response_tools,response_kwargs)
                assert first==second,(family,mode,path)
                result['parity'].append({'template':str(path),'template_sha256':sha(path),'mode':mode,
                    'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'chat_responses_render_identical':True,
                    'rendered_sha256':hashlib.sha256(first.encode()).hexdigest(),'rendered_prefix':first[:220],
                    'rendered_suffix':first[-100:],'thinking_open_for_generation':mode!='none'})
    result['budget_policy']=('Maya config thinking_budget0: no reasoning cap inside2048; Low is a prompt hint.' if family=='glm' else
        'Strata answer_room defaults true: at2048 it caps thinking near1536 to leave answer room; Low is a prompt hint. effort_position defaults start; verify active config before measurement.')
    (HERE/(family+'-mode-audit.json')).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'family':family,'parity_cases':len(result['parity']),'engines_started':False}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--family',choices=['glm','qwen']);a=p.parse_args()
    if a.family:worker(a.family)
    else:
        for family in ('glm','qwen'):
            subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--family',family],check=True)
