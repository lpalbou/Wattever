"""Grade constrained pure Python functions in a bounded CPU child; no engine access."""
import ast, builtins, ctypes, json, os, re, subprocess, sys, tempfile
from pathlib import Path

ATTRS={'append','extend','pop','get','items','keys','values','add','discard','remove','sort','count','index',
       'startswith','endswith','replace','copy','lower','upper','strip','split','join','isdigit','isalnum',
       'fromkeys','setdefault','update','clear','reverse','is_integer','__name__',
       'lstrip','rstrip','splitlines','casefold','removeprefix','removesuffix','isspace'}
BUILTINS={'abs','all','any','bool','dict','divmod','enumerate','filter','float','frozenset','int','isinstance',
          'len','list','map','max','min','next','ord','chr','range','reversed','round','set','slice','sorted',
          'str','sum','tuple','type','zip','Exception','ValueError','TypeError','IndexError','KeyError','StopIteration'}

def validate(code):
    if len(code)>40000:raise ValueError('code_length_limit')
    tree=ast.parse(code)
    if len(list(ast.walk(tree)))>5000:raise ValueError('AST_size_limit')
    for statement in tree.body:
        if not isinstance(statement,ast.FunctionDef) and not (isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Constant) and isinstance(statement.value.value,str)):
            raise ValueError('only_function_definitions_at_module_level')
    for node in ast.walk(tree):
        if isinstance(node,(ast.Import,ast.ImportFrom,ast.ClassDef,ast.Global,ast.Nonlocal,ast.AsyncFunctionDef,ast.With,ast.AsyncWith)):
            raise ValueError('unsupported_statement_'+type(node).__name__)
        if isinstance(node,ast.Name) and node.id.startswith('__'):raise ValueError('dunder_name')
        if isinstance(node,ast.Attribute) and node.attr not in ATTRS:raise ValueError('unsupported_attribute_'+node.attr)
        if isinstance(node,ast.FunctionDef) and (node.decorator_list or node.name.startswith('__')):raise ValueError('unsupported_function_definition')
    return tree

def same(a,b):
    # JSON fixtures encode ordered pairs as arrays; Python tuples represent the
    # same pair/order unless the question explicitly requires a list container.
    if isinstance(b,list) and isinstance(a,(list,tuple)):
        return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
    if type(a) is not type(b):return False
    if isinstance(a,list):return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
    if isinstance(a,dict):return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
    return a==b

def worker():
    data=json.load(sys.stdin);sys.setrecursionlimit(500)
    tree=validate(data['code'])
    scope={'__builtins__':{name:getattr(builtins,name) for name in BUILTINS}}
    exec(compile(tree,'<model-function>','exec'),scope)
    function=scope.get(data['function'])
    if not callable(function):raise ValueError('required_function_missing')
    results=[]
    for case in data['cases']:
        args=json.loads(json.dumps(case['args']));before=json.dumps(args,sort_keys=True)
        try:
            actual=function(*args)
            list_required=data['function'] in ('stable_unique','rle_encode','allocate_cents')
            contract_ok=not list_required or type(actual) is list
            results.append({'passed':same(actual,case['expected']) and contract_ok and json.dumps(args,sort_keys=True)==before,
                            'container_contract_passed':contract_ok,
                            'input_mutated':json.dumps(args,sort_keys=True)!=before})
        except Exception as exc:
            results.append({'passed':False,'error_type':type(exc).__name__})
    print(json.dumps({'cases':results,'passed':all(r['passed'] for r in results)}))

class CPUJob:
    def __init__(self, memory):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                      ('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('active',w.DWORD),
                      ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(ctypes.Structure):_fields_=[(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Limits(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                      ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.restype=w.HANDLE
        self.kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        info=Limits();info.basic.flags=0x2000|0x100|0x200|0x8
        info.basic.active=1;info.process_memory=memory;info.job_memory=memory
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)):
            self.close();raise OSError(ctypes.get_last_error(),'CPU grading Job limits could not be installed')
    def assign(self,p):
        if not self.kernel.AssignProcessToJobObject(self.handle,int(p._handle)):
            raise OSError(ctypes.get_last_error(),'CPU grading child ownership failed')
    def close(self):
        if getattr(self,'handle',None):self.kernel.CloseHandle(self.handle);self.handle=None

def extract(text):
    blocks=re.findall(r'```(?:python|py)?\s*\n(.*?)\n```',text,re.S)
    if len(blocks)==1:return blocks[0],bool(re.fullmatch(r'\s*```python\s*\n.*?\n```\s*',text,re.S))
    if not blocks:return text.strip(),False
    raise ValueError('ambiguous_multiple_code_blocks')

def grade(text,scorer):
    format_ok=False
    try:code,format_ok=extract(text);validate(code)
    except SyntaxError as exc:return {'content_passed':False,'format_passed':format_ok,'failure':'code_syntax_error','detail':str(exc)[:300]}
    except ValueError as exc:return {'content_passed':None,'format_passed':format_ok,'failure':'execution_policy_unsupported','detail':str(exc)[:300]}
    if os.name!='nt':raise RuntimeError('This reviewed runner requires Windows Job limits; no code executed.')
    job=CPUJob(scorer.get('process_memory_bytes',256*2**20));p=None
    try:
        with tempfile.TemporaryDirectory(prefix='strata-code-grade-') as directory:
            # The trusted worker blocks on stdin until ownership/limits are installed.
            p=subprocess.Popen([sys._base_executable,'-I','-S','-B',str(Path(__file__).resolve()),'--worker'],
                cwd=directory,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,env={'SystemRoot':os.environ['SystemRoot'],'PYTHONUTF8':'1'})
            job.assign(p)
            try:
                out,err=p.communicate(json.dumps(dict(scorer,code=code)).encode(),timeout=scorer.get('cpu_timeout_s',3))
            except subprocess.TimeoutExpired:
                job.close();p.wait(timeout=5)
                return {'content_passed':False,'format_passed':format_ok,'failure':'execution_timeout'}
            if p.returncode:
                return {'content_passed':False,'format_passed':format_ok,'failure':'execution_error','returncode':p.returncode,'detail':err.decode(errors='replace')[-400:]}
            value=json.loads(out)
            return {'content_passed':value['passed'],'format_passed':format_ok,'cases':value['cases'],
                    'failure':None if value['passed'] and format_ok else 'format_only' if value['passed'] else 'unit_case_failure'}
    finally:
        job.close()
        if p is not None and p.poll() is None:p.kill();p.wait(timeout=5)

if __name__=='__main__':
    if '--worker' in sys.argv:
        try:worker()
        except Exception as exc:print(json.dumps({'passed':False,'worker_error':type(exc).__name__}));raise SystemExit(1)
    else:
        plan=json.loads((Path(__file__).parent/'plan.json').read_text(encoding='utf-8'))
        for task in plan['tasks']:
            if task['scorer']['kind']!='python_function':continue
            good=grade('```python\n'+task['scorer']['reference']+'\n```',task['scorer']);assert good['content_passed'] and good['format_passed'],(task['id'],good)
            bad=grade('```python\ndef '+task['scorer']['function']+'(*args):\n    return None\n```',task['scorer']);assert not bad['content_passed']
        for bad in ['import os\ndef f(): return 1','def f(): return (1).__class__','def f(): return "{0.__class__}".format(1)']:
            try:validate(bad)
            except ValueError:pass
            else:raise AssertionError('Unsafe AST accepted')
        smoke={'function':'f','cases':[{'args':[],'expected':1}],'cpu_timeout_s':.4}
        assert grade('def f():\n    while True: pass',smoke)['failure']=='execution_timeout'
        print('Eight reference functions and eight wrong implementations checked; unsafe AST and infinite loop rejected. No engines used.')
