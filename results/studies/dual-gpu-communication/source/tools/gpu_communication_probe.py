"""Windows CUDA communication microbenchmark; no model/engine management.

Inspect peer capability without creating contexts:
  python tools/gpu_communication_probe.py --inspect
Run only after independently verifying inference engines are idle:
  python tools/gpu_communication_probe.py --run --output capture.json

Each sample is a microbenchmark sample, not an inference repeat. Joint rates use
the actual joint elapsed interval and transferred payload. Unsupported peer
access uses explicit portable-pinned host staging, never called direct P2P.
"""
import argparse
import concurrent.futures
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import threading
import time

MIB=1024**2
SIZES=(4,64,256,4096,16384,65536,MIB,16*MIB,64*MIB,256*MIB)
def utc():return datetime.now(timezone.utc).isoformat()
def checked_median(values):return statistics.median(values) if values else None

def positive_integer(value,name,maximum=None):
    if isinstance(value,bool) or not isinstance(value,int) or value<=0 or (maximum is not None and value>maximum):
        raise ValueError(f'{name} must be a positive integer'+(f' <= {maximum}' if maximum else ''))
    return value
def write(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,indent=2),encoding='utf-8');temporary.replace(path)

class CUDA:
    def __init__(self):
        if os.name!='nt':raise RuntimeError('This implementation requires Windows')
        self.dll=C.WinDLL(str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'nvcuda.dll'))
        self.contexts={};self.buffers={};self.host=[];self.streams=[]
        p=C.c_void_p;u=C.c_uint64;n=C.c_size_t;i=C.c_int
        definitions={
            'cuInit':[C.c_uint],'cuDeviceGetCount':[C.POINTER(i)],
            'cuDeviceGetName':[C.c_char_p,i,i],'cuDeviceGetPCIBusId':[C.c_char_p,i,i],
            'cuDriverGetVersion':[C.POINTER(i)],'cuDeviceCanAccessPeer':[C.POINTER(i),i,i],
            'cuDevicePrimaryCtxRetain':[C.POINTER(p),i],'cuDevicePrimaryCtxRelease_v2':[i],
            'cuCtxSetCurrent':[p],'cuCtxSynchronize':[],'cuCtxEnablePeerAccess':[p,C.c_uint],
            'cuMemGetInfo_v2':[C.POINTER(n),C.POINTER(n)],'cuMemAlloc_v2':[C.POINTER(u),n],
            'cuMemFree_v2':[u],'cuMemHostAlloc':[C.POINTER(p),n,C.c_uint],'cuMemFreeHost':[p],
            'cuMemcpyHtoDAsync_v2':[u,p,n,p],'cuMemcpyDtoHAsync_v2':[p,u,n,p],
            'cuMemcpyDtoH_v2':[p,u,n],'cuMemcpyPeerAsync':[u,p,u,p,n,p],
            'cuMemcpyDtoDAsync_v2':[u,u,n,p],'cuMemsetD8_v2':[u,C.c_ubyte,n],
            'cuStreamCreate':[C.POINTER(p),C.c_uint],'cuStreamSynchronize':[p],
            'cuStreamDestroy_v2':[p],'cuEventCreate':[C.POINTER(p),C.c_uint],
            'cuEventRecord':[p,p],'cuEventSynchronize':[p],
            'cuEventElapsedTime':[C.POINTER(C.c_float),p,p],'cuEventDestroy_v2':[p],
            'cuGetErrorString':[i,C.POINTER(C.c_char_p)]}
        for name,args in definitions.items():
            fn=getattr(self.dll,name);fn.argtypes=args;fn.restype=i
        self.call('cuInit',0)
        count=i();self.call('cuDeviceGetCount',C.byref(count));self.count=count.value
        version=i();self.call('cuDriverGetVersion',C.byref(version));self.version=version.value
    def call(self,name,*args):
        result=getattr(self.dll,name)(*args)
        if result:
            message=C.c_char_p();self.dll.cuGetErrorString(result,C.byref(message))
            raise RuntimeError(f'{name}: CUDA {result}: '+(message.value.decode() if message.value else 'unknown'))
    def set(self,device):self.call('cuCtxSetCurrent',self.contexts[device])
    def inspect(self):
        devices=[]
        for index in range(self.count):
            name=C.create_string_buffer(256);bus=C.create_string_buffer(32)
            self.call('cuDeviceGetName',name,len(name),index)
            self.call('cuDeviceGetPCIBusId',bus,len(bus),index)
            devices.append({'cuda_ordinal':index,'name':name.value.decode(),'pci_bus_address':bus.value.decode()})
        peers=[]
        for a in range(self.count):
            for b in range(self.count):
                if a==b:continue
                value=C.c_int();self.call('cuDeviceCanAccessPeer',C.byref(value),a,b)
                peers.append({'accessor_device':a,'peer_allocation_device':b,'can_access_peer':bool(value.value)})
        return {'driver_api_version':self.version,'devices':devices,'peer_capability':peers,
                'capability_scope':'cuDeviceCanAccessPeer; inspection creates no CUDA context or allocation'}
    def allocate(self,maximum):
        positive_integer(maximum,'maximum allocation bytes',1024*MIB)
        if self.count!=2:raise RuntimeError('Explicitly select exactly two visible CUDA GPUs')
        for device in (0,1):
            context=C.c_void_p();self.call('cuDevicePrimaryCtxRetain',C.byref(context),device)
            self.contexts[device]=context;self.set(device)
            free,total=C.c_size_t(),C.c_size_t();self.call('cuMemGetInfo_v2',C.byref(free),C.byref(total))
            if free.value<2*maximum+2*1024**3:raise RuntimeError('Refusing allocations without 2 GiB free headroom')
            self.buffers[device]={}
            for role in ('source','destination'):
                pointer=C.c_uint64();self.call('cuMemAlloc_v2',C.byref(pointer),maximum)
                self.buffers[device][role]=pointer.value
            for _ in range(2):
                pointer=C.c_void_p();self.call('cuMemHostAlloc',C.byref(pointer),maximum,1)
                self.host.append(pointer)
        self.maximum=maximum
        for device in (0,1):
            block=random.Random(7463+device).randbytes(65536)
            contents=(block*((maximum+65535)//65536))[:maximum]
            C.memmove(self.host[device*2],contents,maximum)
            C.memset(self.host[device*2+1],0,maximum)
            del contents
            self.set(device);stream=self.new_stream(device)
            self.call('cuMemcpyHtoDAsync_v2',self.buffers[device]['source'],self.host[device*2],maximum,stream.handle)
            self.call('cuStreamSynchronize',stream.handle)
        self.peer_enabled={};self.peer_enable_results=[]
        for source in (0,1):
            destination=1-source;can=C.c_int()
            self.call('cuDeviceCanAccessPeer',C.byref(can),source,destination)
            code=None
            if can.value:
                self.set(source);code=self.dll.cuCtxEnablePeerAccess(self.contexts[destination],0)
            enabled=bool(can.value and code in (0,704))
            self.peer_enabled[source,destination]=enabled
            self.peer_enable_results.append({'accessor':source,'peer':destination,'capability':bool(can.value),
                'enable_attempted':bool(can.value),'enable_status':code,'enabled':enabled})
    def new_stream(self,device):
        self.set(device);return Stream(self,device)
    def validate(self,device,size,expected_source,host_only=False):
        self.set(device);target=self.host[device*2+1]
        if not host_only:self.call('cuMemcpyDtoH_v2',target,self.buffers[device]['destination'],size)
        self.call('cuCtxSynchronize')
        expected=hashlib.sha256(C.string_at(self.host[expected_source*2],size)).hexdigest()
        actual=hashlib.sha256(C.string_at(target,size)).hexdigest()
        return {'bytes_checked':size,'expected_pattern_source':expected_source,
                'expected_sha256':expected,'observed_sha256':actual,'passed':actual==expected}
    def poison(self,device,size,host_only=False):
        """Invalidate prior destinations outside timed samples to detect no-op paths."""
        self.set(device)
        if host_only:C.memset(self.host[device*2+1],0xA5,size)
        else:self.call('cuMemsetD8_v2',self.buffers[device]['destination'],0xA5,size)
        self.call('cuCtxSynchronize')
    def close(self):
        errors=[]
        for stream in self.streams:
            try:stream.close()
            except Exception as error:errors.append(str(error))
        self.streams=[]
        for device,buffers in self.buffers.items():
            try:self.set(device)
            except Exception as error:
                errors.append(f'device {device} context selection: {error}');continue
            for pointer in buffers.values():
                try:self.call('cuMemFree_v2',pointer)
                except Exception as error:errors.append(str(error))
        self.buffers={}
        for pointer in self.host:
            try:self.call('cuMemFreeHost',pointer)
            except Exception as error:errors.append(str(error))
        self.host=[]
        try:self.call('cuCtxSetCurrent',None)
        except Exception as error:errors.append(str(error))
        released=True
        for device in self.contexts:
            try:self.call('cuDevicePrimaryCtxRelease_v2',device)
            except Exception as error:errors.append(str(error));released=False
        self.contexts={}
        return {'errors':errors,'remaining_device_allocations':None if errors else 0,
                'remaining_host_allocations':None if errors else 0,'contexts_released':released}

class Stream:
    def __init__(self,cuda,device):
        self.cuda,self.device=cuda,device;self.handle=C.c_void_p();self.events=[]
        cuda.call('cuStreamCreate',C.byref(self.handle),1)
        # Register immediately so partial event initialization still gets cleaned up.
        cuda.streams.append(self)
        for _ in range(2):
            event=C.c_void_p();cuda.call('cuEventCreate',C.byref(event),0);self.events.append(event)
    def timed(self,operation,copies):
        cuda=self.cuda;cuda.set(self.device)
        cuda.call('cuEventRecord',self.events[0],self.handle)
        for _ in range(copies):operation()
        cuda.call('cuEventRecord',self.events[1],self.handle)
        cuda.call('cuEventSynchronize',self.events[1])
        elapsed=C.c_float();cuda.call('cuEventElapsedTime',C.byref(elapsed),*self.events)
        return elapsed.value
    def close(self):
        self.cuda.set(self.device);errors=[]
        for name,args in [('cuStreamSynchronize',(self.handle,))]+[('cuEventDestroy_v2',(event,)) for event in self.events]+[('cuStreamDestroy_v2',(self.handle,))]:
            try:self.cuda.call(name,*args)
            except Exception as error:errors.append(str(error))
        if errors:raise RuntimeError('; '.join(errors))

class Route:
    def __init__(self,cuda,kind,source,destination=None):
        if kind not in ('HtoD','DtoH','DtoD','cross'):raise ValueError('Unknown transfer kind')
        if source not in (0,1) or (destination is not None and destination not in (0,1)):
            raise ValueError('Transfer device must be CUDA ordinal 0 or 1')
        if kind=='cross' and (destination is None or source==destination):raise ValueError('Cross copy requires distinct devices')
        if kind!='cross' and destination not in (None,source):raise ValueError('Local transfer destination must be its source device')
        self.cuda,self.kind,self.source=cuda,kind,source
        self.destination=source if destination is None else destination
        self.stream=cuda.new_stream(source)
        self.other_stream=cuda.new_stream(self.destination) if kind=='cross' else None
        peer=cuda.peer_enabled.get((source,self.destination),False)
        self.transport=('peer_enabled_cuda_copy_transport_not_independently_traced' if peer else 'explicit_portable_pinned_host_staging') if kind=='cross' else kind
    def execute(self,size,copies):
        positive_integer(size,'transfer bytes',self.cuda.maximum)
        positive_integer(copies,'copy count',65536)
        c=self.cuda;s=self.source;d=self.destination;stream=self.stream
        if self.kind=='HtoD':
            elapsed=stream.timed(lambda:c.call('cuMemcpyHtoDAsync_v2',c.buffers[s]['destination'],c.host[s*2],size,stream.handle),copies)
            return {'HtoD_gpu_event_ms':elapsed}
        if self.kind=='DtoH':
            elapsed=stream.timed(lambda:c.call('cuMemcpyDtoHAsync_v2',c.host[s*2+1],c.buffers[s]['source'],size,stream.handle),copies)
            return {'DtoH_gpu_event_ms':elapsed}
        if self.kind=='DtoD':
            elapsed=stream.timed(lambda:c.call('cuMemcpyDtoDAsync_v2',c.buffers[s]['destination'],c.buffers[s]['source'],size,stream.handle),copies)
            return {'DtoD_gpu_event_ms':elapsed}
        if c.peer_enabled[s,d]:
            elapsed=stream.timed(lambda:c.call('cuMemcpyPeerAsync',c.buffers[d]['destination'],c.contexts[d],c.buffers[s]['source'],c.contexts[s],size,stream.handle),copies)
            return {'peer_api_gpu_event_ms':elapsed}
        first=stream.timed(lambda:c.call('cuMemcpyDtoHAsync_v2',c.host[s*2+1],c.buffers[s]['source'],size,stream.handle),copies)
        # Host synchronization completes every source read before destination use.
        second=self.other_stream.timed(lambda:c.call('cuMemcpyHtoDAsync_v2',c.buffers[d]['destination'],c.host[s*2+1],size,self.other_stream.handle),copies)
        return {'DtoH_gpu_event_ms':first,'HtoD_gpu_event_ms':second}
    def validate(self,size):
        return self.cuda.validate(self.destination,size,self.source,host_only=self.kind=='DtoH')
    def poison(self,size):
        self.cuda.poison(self.destination,size,host_only=self.kind=='DtoH')

def sample(pool,routes,size,copies):
    positive_integer(size,'transfer bytes')
    if not routes or len(routes)!=len(copies):raise ValueError('One positive copy count is required per route')
    if len(routes)>getattr(pool,'_max_workers',len(routes)):
        raise ValueError('Insufficient executor workers for the synchronized group')
    for count in copies:positive_integer(count,'copy count',65536)
    # The barrier action runs once, before any participant is released. Starting
    # before main-thread wait() would also charge worker arrival/dispatch delay.
    release=[]
    barrier=threading.Barrier(len(routes)+1,action=lambda:release.append(time.perf_counter_ns()),timeout=30)
    def worker(route,count):
        try:
            route.cuda.set(route.source);barrier.wait();start=time.perf_counter_ns()
            timings=route.execute(size,count);end=time.perf_counter_ns()
        except BaseException:
            barrier.abort();raise
        finally:route.cuda.call('cuCtxSetCurrent',None)
        return {'source_gpu':route.source,'destination_gpu':route.destination,'transport':route.transport,
                'copies':count,'payload_bytes':size*count,'host_start_ns':start,'host_end_ns':end,
                'host_elapsed_s':(end-start)/1e9,**timings}
    futures=[]
    try:
        for route,count in zip(routes,copies):futures.append(pool.submit(worker,route,count))
    except BaseException:
        barrier.abort();concurrent.futures.wait(futures);raise
    barrier_error=None
    try:barrier.wait()
    except threading.BrokenBarrierError as error:barrier_error=error
    # Never release CUDA allocations while a sibling worker could still use them.
    concurrent.futures.wait(futures)
    errors=[future.exception() for future in futures if future.exception() is not None]
    original=next((error for error in errors if not isinstance(error,threading.BrokenBarrierError)),None)
    if original:raise original
    if errors:raise errors[0]
    if barrier_error:raise barrier_error
    results=[future.result() for future in futures];end=time.perf_counter_ns();start=release[0]
    elapsed=(end-start)/1e9;payload=sum(row['payload_bytes'] for row in results)
    return {'host_start_ns':start,'host_end_ns':end,'elapsed_s':elapsed,'payload_bytes':payload,
            'payload_bytes_per_second':payload/elapsed,'routes':results,
            'timing_scope':'Host barrier release through all synchronized routes and worker completion; includes Python dispatch/context/event overhead',
            'host_worker_intervals_overlap':len(results)>1 and max(r['host_start_ns'] for r in results)<min(r['host_end_ns'] for r in results)}

def configurations(cuda):
    def route(kind,source,destination=None):return Route(cuda,kind,source,destination)
    cases=[]
    for device in (0,1):
        for kind in ('HtoD','DtoH','DtoD'):
            cases.append((kind+str(device),[route(kind,device)],[1]))
    cases += [('cross_0_to_1',[route('cross',0,1)],[1]),('cross_1_to_0',[route('cross',1,0)],[1]),
              ('simultaneous_HtoD_equal',[route('HtoD',0),route('HtoD',1)],[1,1]),
              ('simultaneous_HtoD_weighted_2_1',[route('HtoD',0),route('HtoD',1)],[2,1]),
              ('simultaneous_DtoH_equal',[route('DtoH',0),route('DtoH',1)],[1,1]),
              ('bidirectional_cross',[route('cross',0,1),route('cross',1,0)],[1,1]),
              ('mixed_HtoD0_DtoH1',[route('HtoD',0),route('DtoH',1)],[1,1]),
              ('mixed_DtoH0_HtoD1',[route('DtoH',0),route('HtoD',1)],[1,1])]
    return cases

def run(path,sizes=SIZES):
    path=Path(path);sizes=tuple(sizes)
    if not sizes:raise ValueError('At least one transfer size is required')
    for size in sizes:positive_integer(size,'transfer bytes',1024*MIB)
    if path.exists():raise ValueError('Preserve the existing capture; choose a new output path')
    cuda=None;result={'schema_version':1,'protocol':'cuda_communication_micro_v1',
        'capture_kind':'diagnostic_microbenchmark','inference_repeat_count':None,'started_at':utc(),
        'status':'running','measurements':[],
        'host_placement':'Portable CUDA-pinned pages, OS default first touch; CPU affinity/NUMA not pinned',
        'rate_definition':'Transferred end-to-end payload / independently timed joint wall interval; no sum of independent rates',
        'staging_definition':'Source DtoH batch synchronized before destination HtoD batch; no within-route double-buffer pipeline',
        'peer_label_policy':'Peer capability/enabling does not independently trace transport. Unsupported access uses explicitly staged copies; no fallback is labeled direct P2P.'}
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        cuda=CUDA();result['inventory']=cuda.inspect()
        cuda.allocate(max(sizes));result['peer_enable_results']=cuda.peer_enable_results
        cases=configurations(cuda)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for size in sizes:
                for name,routes,weights in cases:
                    for mode,count in (('synchronized_group_latency',1),('batched_throughput',max(1,min(256,64*MIB//size)))):
                        copies=[count*w for w in weights]
                        for _ in range(3):sample(pool,routes,size,copies)
                        # Warmups may already have populated the destination. Poison
                        # after warmup, before measured copies, to detect no-op paths.
                        for route in routes:route.poison(size)
                        samples=[sample(pool,routes,size,copies) for _ in range(15 if mode=='synchronized_group_latency' else 7)]
                        validation=[route.validate(size) for route in routes]
                        passed=all(v['passed'] for v in validation)
                        row={'case':name,'timing_mode':mode,'transfer_bytes':size,'copies_per_route':copies,
                            'internal_samples':len(samples),'warmup_batches':3,'samples':samples,'validation':validation,
                            'validation_passed':passed,'validation_scope':'Full destination after final measured batch; poisoned after warmup, outside timing; earlier samples are not individually validated','statistic':'median',
                            'median_elapsed_s':checked_median([s['elapsed_s'] for s in samples]),
                            'median_payload_bytes_per_second':checked_median([s['payload_bytes_per_second'] for s in samples])}
                        result['measurements'].append(row);write(path,result)
                        print('TRANSFER',name,size,mode,'validated',passed,flush=True)
                        if not passed:raise RuntimeError('Full destination validation failed')
        result['status']='completed'
    except BaseException as error:
        result.update(status='failed',error=f'{type(error).__name__}: {error}');raise
    finally:
        try:result['cleanup']=cuda.close() if cuda is not None else {'errors':[],'contexts_released':True,'remaining_device_allocations':0,'remaining_host_allocations':0}
        except BaseException as error:
            result['cleanup']={'errors':[f'{type(error).__name__}: {error}'],'contexts_released':False,
                               'remaining_device_allocations':None,'remaining_host_allocations':None}
        if result['cleanup'].get('errors'):
            result['status']='failed';result.setdefault('error','CUDA cleanup incomplete; allocations/context release not confirmed')
        result['finished_at']=utc();write(path,result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--inspect',action='store_true');action.add_argument('--run',action='store_true')
    parser.add_argument('--output');parser.add_argument('--smoke',action='store_true',help='Diagnostic size subset; still performs real CUDA copies')
    args=parser.parse_args()
    if args.inspect:print(json.dumps(CUDA().inspect(),indent=2))
    else:
        if not args.output:parser.error('--run requires a new --output path')
        capture=run(Path(args.output),(4096,MIB) if args.smoke else SIZES)
        if capture['status']!='completed':raise SystemExit(1)
