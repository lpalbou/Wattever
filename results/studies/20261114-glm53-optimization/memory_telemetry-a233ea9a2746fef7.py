"""GLM Windows paging and reclaimable file-cache telemetry. No lifecycle operations."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import threading
import time
import psutil

class Number(C.Union):
    _fields_ = [('longValue',C.c_int32),('doubleValue',C.c_double),('largeValue',C.c_int64)]
class Formatted(C.Structure):
    _anonymous_ = ('number',)
    _fields_ = [('status',C.c_uint32),('number',Number)]

class Paging:
    paths = {
        'pages_input_s':r'\Memory\Pages Input/sec',
        'pages_output_s':r'\Memory\Pages Output/sec',
        'page_reads_s':r'\Memory\Page Reads/sec',
        'page_writes_s':r'\Memory\Page Writes/sec',
        'page_faults_s':r'\Memory\Page Faults/sec',
        'transition_faults_s':r'\Memory\Transition Faults/sec',
        'system_cache_resident_bytes':r'\Memory\System Cache Resident Bytes',
        'standby_cache_normal_bytes':r'\Memory\Standby Cache Normal Priority Bytes',
        'standby_cache_reserve_bytes':r'\Memory\Standby Cache Reserve Bytes',
        'standby_cache_core_bytes':r'\Memory\Standby Cache Core Bytes',
        'modified_page_list_bytes':r'\Memory\Modified Page List Bytes',
        'committed_bytes':r'\Memory\Committed Bytes',
        'commit_limit_bytes':r'\Memory\Commit Limit',
        'pagefile_usage_percent':r'\Paging File(_Total)\% Usage',
    }
    def __init__(self,path):
        self.path=Path(path); self.rows=[]; self.end=threading.Event(); self.errors={}
        self.dll=C.WinDLL('pdh.dll'); self.query=C.c_void_p(); self.counters={}
        for name,args in {
            'PdhOpenQueryW':[C.c_wchar_p,C.c_size_t,C.POINTER(C.c_void_p)],
            'PdhAddEnglishCounterW':[C.c_void_p,C.c_wchar_p,C.c_size_t,C.POINTER(C.c_void_p)],
            'PdhCollectQueryData':[C.c_void_p],
            'PdhGetFormattedCounterValue':[C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.POINTER(Formatted)],
            'PdhCloseQuery':[C.c_void_p],
        }.items():
            fn=getattr(self.dll,name); fn.argtypes=args; fn.restype=C.c_uint32
        status=self.dll.PdhOpenQueryW(None,0,C.byref(self.query))
        if status: raise OSError(f'PDH open: {status}')
        for key,path in self.paths.items():
            counter=C.c_void_p()
            status=self.dll.PdhAddEnglishCounterW(self.query,path,0,C.byref(counter))
            if status: self.errors[key]=hex(status)
            else: self.counters[key]=counter
        self.dll.PdhCollectQueryData(self.query)
        self.thread=threading.Thread(target=self.run,daemon=True)
    def start(self): self.thread.start()
    def stop(self):
        self.end.set(); self.thread.join(5); self.dll.PdhCloseQuery(self.query)
    def run(self):
        process=None; next_probe=0
        with self.path.open('w',encoding='utf-8') as stream:
            while not self.end.wait(.5):
                now=time.perf_counter(); self.dll.PdhCollectQueryData(self.query)
                vm=psutil.virtual_memory()
                row={'t':now,'available_bytes':vm.available,'used_percent':vm.percent}
                for key,counter in self.counters.items():
                    val=Formatted()
                    status=self.dll.PdhGetFormattedCounterValue(counter,0x200,None,C.byref(val))
                    row[key]=val.doubleValue if not status and val.status in (0,1) else None
                if now>=next_probe:
                    next_probe=now+5
                    for p in psutil.process_iter(['name']):
                        if (p.info['name'] or '').lower()=='maya.exe': process=p; break
                if process:
                    try:
                        m=process.memory_info()
                        row['engine']={'pid':process.pid,'rss':m.rss,'private_bytes':getattr(m,'private',m.vms),
                                       'page_faults_total':getattr(m,'num_page_faults',None)}
                    except psutil.Error: process=None
                try:
                    row['disks']={k:{'read_bytes':v.read_bytes,'write_bytes':v.write_bytes,
                                     'read_time_ms':v.read_time,'write_time_ms':v.write_time}
                                  for k,v in psutil.disk_io_counters(perdisk=True).items()}
                except Exception: pass
                self.rows.append(row); stream.write(json.dumps(row)+'\n'); stream.flush()
    def window(self,start,end):
        rows=[r for r in self.rows if start<=r['t']<=end]
        if not rows: return {'available':False,'errors':self.errors}
        result={'available':True,'samples':len(rows),'errors':self.errors,
                'available_bytes_min':min(r['available_bytes'] for r in rows),
                'ram_used_percent_max':max(r['used_percent'] for r in rows),
                'interpretation':'Available RAM includes reclaimable standby cache; report cache counters separately. PagesInput/PageReads and process faults include mmap model reads; they alone do not prove pagefile thrashing.'}
        for key in self.paths:
            values=[r[key] for r in rows if r.get(key) is not None]
            result[key]={'mean':sum(values)/len(values),'max':max(values),'min':min(values)} if values else None
        # Report continuous heavy page-outs separately from ambiguous page-ins.
        longest=0; began=None
        for r in rows:
            heavy=(r.get('pages_output_s') or 0)>=1024
            if heavy:
                began=r['t'] if began is None else began
                longest=max(longest,r['t']-began)
            else: began=None
        result['longest_pageouts_ge_1024_pages_s_seconds']=longest
        a,b=rows[0],rows[-1]
        result['disk_deltas']={}
        for key,v in b.get('disks',{}).items():
            if key in a.get('disks',{}):
                result['disk_deltas'][key]={k:v[k]-a['disks'][key][k] for k in v}
        engine=[r['engine'] for r in rows if r.get('engine')]
        if engine:
            result['engine_rss_min']=min(r['rss'] for r in engine)
            result['engine_rss_max']=max(r['rss'] for r in engine)
            result['engine_faults_delta']=engine[-1]['page_faults_total']-engine[0]['page_faults_total']
        return result
