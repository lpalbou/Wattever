"""Owned diagnostic only, gated on the preceding cohort/report and idle engines."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.standalone_screen import validate_prior_completion, verify_idle, verify_gpu_mapping, verify_no_standalone_engines, write_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--smoke',action='store_true');args=p.parse_args()
    prior=validate_prior_completion()
    import psutil
    idle=verify_idle();verify_no_standalone_engines(psutil);mapping=verify_gpu_mapping()
    # Changes affect this owned diagnostic child only; never a user service.
    os.environ['CUDA_DEVICE_ORDER']='PCI_BUS_ID'
    os.environ.pop('CUDA_VISIBLE_DEVICES',None)
    from tools import gpu_communication_probe as probe
    from tools.diagnostic_telemetry import DiagnosticTelemetry
    from wattever.power import PowerSampler
    directory=ROOT/'untracked/communication-investigation'/('communication-'+('smoke-' if args.smoke else 'full-')+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    directory.mkdir(exist_ok=False)
    sources=[Path(__file__),ROOT/'tools/gpu_communication_probe.py',ROOT/'tools/diagnostic_telemetry.py',ROOT/'wattever/power.py']
    source_hashes={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    write_json(directory/'manifest.json',{'capture_kind':'diagnostic_microbenchmark','inference_repeats':None,'prior':prior,'idle':idle,'physical_nvidia_mapping':mapping,'cuda_order':'PCI_BUS_ID','command':sys.argv,'source_sha256':source_hashes})
    counters=DiagnosticTelemetry(pids=[os.getpid()],interval_ms=100)
    power=PowerSampler(interval_ms=150)
    start=None;end=None;failure=None;cleanup=[]
    try:
        power.start();counters.start();time.sleep(.5);start=time.perf_counter()
        transfer_result=probe.run(directory/'transfers.json',(4096,probe.MIB) if args.smoke else probe.SIZES)
        released=transfer_result.get('cleanup',{})
        if (transfer_result.get('status')!='completed' or released.get('errors') or
            released.get('contexts_released') is not True or
            released.get('remaining_device_allocations')!=0 or
            released.get('remaining_host_allocations')!=0):
            raise RuntimeError('CUDA capture or cleanup incomplete; halt subsequent GPU work')
        end=time.perf_counter();time.sleep(.3)
    except BaseException as error:
        failure=repr(error);raise
    finally:
        end=end or time.perf_counter()
        for name,sampler in [('diagnostic',counters),('power',power)]:
            try:sampler.stop()
            except BaseException as error:cleanup.append({'sampler':name,'error':repr(error)})
        write_json(directory/'telemetry.json',{'capture_kind':'diagnostic_microbenchmark','power_scope':'Whole diagnostic capture including allocations, warmups, validation and checkpoint I/O; not isolated transfer energy','start_perf':start,'end_perf':end,'diagnostic':counters.summary(),'power_summary':power.summary(),'power_samples':power.history(),'measured_power_window':power.window(start,end) if start else None,'failure':failure,'cleanup_errors':cleanup})
    if cleanup:raise RuntimeError('Diagnostic sampler cleanup failed; inspect capture')
    print('COMMUNICATION_CAPTURE_COMPLETE',directory,flush=True)

if __name__=='__main__':main()
