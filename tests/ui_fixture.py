import sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wattever import server, store, runner, capabilities
from http.server import ThreadingHTTPServer
session = tempfile.TemporaryDirectory()
store.RUNS_DIR = session.name + '/runs'
store.TRASH_DIR = session.name + '/trash'
store.CONFIG_PATH = session.name + '/config.json'
capabilities.RUNS_DIR = session.name + '/capabilities'
run = {'id':'smoke', 'label':'NVIDIA GeForce RTX 5060 Ti', 'created_at':'2026-10-26T12:00:00+00:00', 'status':'completed', 'gpu':{'name':'NVIDIA GeForce RTX 5060 Ti','vram_total_mb':16384, 'power_readable':True}, 'host':{'cpu':'Smoke CPU','ram_gb':32}, 'config':dict(runner.DEFAULT_CONFIG, models=['model-a']), 'results':[{'model':'model-a','target_tokens':512,'status':'ok','concurrency':1,'prefill_tps':100,'decode_tps':50}, {'model':'model-a','target_tokens':512,'status':'ok','concurrency':2,'aggregate_completion_tps':75,'completed_requests':2,'requests':[{'status':'ok','decode_tps':40,'prompt_tokens':512},{'status':'ok','decode_tps':35,'prompt_tokens':512}]}]}
run['results'].extend([
    {'model':'model-b','target_tokens':512,'status':'ok','concurrency':1,'prefill_tps':150,'decode_tps':60},
    {'model':'model-c','target_tokens':512,'status':'ok','concurrency':1,'prefill_tps':200,'decode_tps':70},
])
for row in run['results']:
    if row.get('concurrency', 1) == 1:
        row['ttft_s'] = 0.5
        row['prompt_tokens'] = int(row['prefill_tps'] * .5)
        row['decode_token_count'] = 99
        row['decode_s'] = 99 / row['decode_tps']
        row['completion_tokens'] = 100
        row['wall_s'] = 2.5
# Simulated concurrency evidence only; these fixtures never run a real model.
run['config'].update(sizes=[512],concurrency_levels=[1,2,4,8])
run['results'][1].update(aggregate_completion_tokens=200,wall_s=200/75)
for index,request in enumerate(run['results'][1]['requests']):
    request.update(completion_tokens=100,decode_token_count=99,decode_s=99/request['decode_tps'],start_offset_s=0,ttft_s=.6+index*.2)
    request['wall_s'] = request['ttft_s'] + request['decode_s'] + .02
run['results'][1]['wall_s'] = max(q['wall_s'] for q in run['results'][1]['requests'])
run['results'].extend([
    {'model':'model-a','target_tokens':512,'concurrency':4,'status':'ok','completed_requests':4,
     'aggregate_completion_tokens':400,'aggregate_completion_tps':400/6,'wall_s':6,
     'requests':[{'status':'ok','prompt_tokens':512,'completion_tokens':100,'decode_tps':25+i,
                  'start_offset_s':0,'ttft_s':1.1+i*.1,'wall_s':5+i/3} for i in range(4)]},
    {'model':'model-a','target_tokens':512,'concurrency':8,'status':'failed','fail_kind':'timeout',
     'completed_requests':0,'wall_s':15,'timeout_budget_s':15,
     'requests':[{'status':'failed','fail_kind':'timeout'} for _ in range(8)]},
])
run['telemetry'] = {
    'summary': {'observations': 4, 'metrics': {
        'gpu_util': {'mean': 60, 'max': 80, 'last': 80, 'early_60s_mean': 50, 'late_60s_mean': 70, 'late_minus_early': 20, 'coverage_fraction': .95},
        'temp_c': {'mean': 60, 'max': 65, 'last': 65, 'early_60s_mean': 58, 'late_60s_mean': 62, 'late_minus_early': 4, 'coverage_fraction': .95}}},
    'history': {'retention': {'retained_points': 4, 'total_observations': 4}, 'points': [
        {'elapsed_s': i, 'gpu_util': 40 + 10*i, 'cpu_util_percent': 20+i, 'system_memory_percent': 40+i,
         'vram_percent': 70+i, 'temp_c': 58+i, 'cpu_temp_c': 50+i} for i in range(4)]}}
store.save(run)
import copy
comparison = copy.deepcopy(run)
comparison.update(id='comparison', label='GTX 1080 Ti 11 GB VRAM / 64 GB RAM / Intel Core i7 desktop processor', created_at='2026-10-25T12:00:00+00:00')
comparison['gpu']['name'] = 'NVIDIA GeForce GTX 1080 Ti'
comparison['results'][0].update(prefill_tps=70, prompt_tokens=35, decode_tps=30,decode_s=99/30,wall_s=.5+99/30+.02)
comparison['results'][1]['aggregate_completion_tps'] = 50
comparison['results'] = [row for row in comparison['results'] if row['model'] != 'model-c']
store.save(comparison)
class FakeBench:
    _start_lock = threading.Lock()
    running = False
    run = None
    events = []
    last_extension = None
    def extension_options(self, run_id, selection=None):
        if (selection or {}).get('connection', {}).get('provider') == 'openai':
            return {'models':[{'id':'external/model','type':'llm','quantization':'IQ3_S','max_context_length':32768}],
                    'config':dict(runner.DEFAULT_CONFIG,provider='openai',models=[],sizes=[512],concurrency_levels=[1]),
                    'sizes':[512],'concurrency_levels':[1]}
        return {'models': [{'id':'model-a', 'type':'llm'}, {'id':'model-b', 'type':'llm'}],
                'sizes': [512], 'concurrency_levels': [1]}
    def plan_extension(self, body):
        modes = body.get('model_modes', {})
        existing = int('off' in modes.get('model-a', []))
        new = int('on' in modes.get('model-b', []))
        retry = int(bool(body.get('retry_failed')) and 'off' in modes.get('model-b', []))
        rerun = existing if body.get('rerun_existing') else 0
        return {'existing': existing, 'new_tests': new, 'retry_tests': retry, 'rerun_tests': rerun,
                'total_tests': new + retry + rerun, 'warnings': []}
    def extend(self, body):
        self.last_extension = copy.deepcopy(body)
        return body['run_id']
    def status(self):
        return {'running': self.running, 'run_id': (self.run or {}).get('id'),
                'run': store.summarise(self.run) if self.run else None,
                'live': {'phase': 'prefill' if self.running else 'idle', 'done': len((self.run or {}).get('results', [])), 'total': 4},
                'cancelling': False}
    def events_since(self, cursor):
        return [event for event in self.events if event['seq'] >= cursor], len(self.events)
    def emit(self, kind, **payload):
        self.events.append(dict(payload, kind=kind, seq=len(self.events)))

server.BENCH = FakeBench()
# Every benchmark in this fixture is simulated. Keep the isolated UI test
# independent of jobs running in a real helper on the same workstation.
server._other_helper_running = lambda current_port: False

class Handler(server.Handler):
    def do_GET(self):
        if self.path.startswith('/api/system/history'):
            # Synthetic recorded history: one hour three hours ago, a gap, then the last ten minutes. No sensors start.
            import time, urllib.parse
            from wattever.telemetry_log import choose_bucket, downsample
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            now = time.time()
            end = min(now, float((query.get('end') or [now])[0]))
            start = max(now - 86400, float((query.get('start') or [end - 900])[0]))
            bucket = choose_bucket(end - start, int(float((query.get('max_points') or [900])[0])))
            times = [t for t in range(int(now) - 3 * 3600, int(now) - 2 * 3600)] + [t for t in range(int(now) - 600, int(now) - 2)]
            records = [{'t': float(t), 'cpu_util_percent': 30 + t % 20, 'system_memory_percent': 25, 'gpu_util': 40, 'vram_percent': 50,
                        'gpu_w': 150, 'system_w_est': 230, 'temp_c': 60, 'cpu_temp_c': 50} for t in times if start <= t <= end]
            return self._json({'start': start, 'end': end, 'bucket_s': bucket, 'gap_s': max(4.0, 2.5 * bucket), 'retention_s': 86400,
                               'oldest': now - 3 * 3600, 'log': {'mode': 'owner', 'persistent': True, 'retention_s': 86400, 'reason': None},
                               'points': downsample(records, bucket)})
        if self.path == '/api/system':
            import time
            from datetime import datetime, timezone
            return self._json({'timestamp': time.time(), 'source': 'fixture', 'error': None,
                'scope': {'scope': 'benchmark_client_all_nvidia_gpus'},
                'capture': {'continuous': True, 'sampler': 'monitor', 'record_interval_ms': 1000,
                            'log': {'mode': 'owner', 'persistent': True, 'retention_s': 86400, 'reason': None}},
                'telemetry': {'timestamp': datetime.now(timezone.utc).isoformat(), 'sample_age_s': 0,
                    'cpu_util_percent': 20, 'system_memory_used_bytes': 8 * 1073741824, 'system_memory_percent': 25,
                    'gpu_w': 190, 'system_w_est': 250, 'gpu_util': 45, 'vram_mb': 18000, 'vram_percent': 56,
                    'temp_c': 65, 'cpu_temp_c': 55,
                    'gpus': [{'index': 0, 'name': 'NVIDIA GeForce RTX 5070 Ti', 'gpu_w': 120,
                              'gpu_util': 50, 'vram_mb': 10000, 'vram_percent': 62.5, 'temp_c': 65, 'clock_mhz': 2700},
                             {'index': 1, 'name': 'NVIDIA GeForce RTX 5060 Ti', 'gpu_w': 70,
                              'gpu_util': 40, 'vram_mb': 8000, 'vram_percent': 50, 'temp_c': 59, 'clock_mhz': 2400}]}})
        if self.path == '/test/last-extension':
            return self._json(server.BENCH.last_extension)
        return super().do_GET()
    def do_POST(self):
        if self.path == '/test/discovery-failure':
            self.server.discovery_failure = bool(self._body().get('enabled'))
            return self._json({'ok': True})
        if self.path == '/test/last-config':
            # What the helper saved from the last benchmark started, as /api/env reports it.
            self.server.last_config = self._body().get('config')
            return self._json({'ok': True})
        if self.path == '/api/hardware/gpu-setup':
            from wattever.gpu_setup import validate
            return self._json({'saved':True,'choice':validate(self._body()),'model_started':False,'model_switched':False})
        if self.path == '/api/endpoint/check':
            checked = self._body()
            if getattr(self.server, 'discovery_failure', False):
                return self._error(503, 'Fixture discovery unavailable')
            # Port 1234 plays this computer's own LM Studio reached through its OpenAI-compatible address.
            return self._json({'ok': True, 'helper_lmstudio': ':1234' in str(checked.get('endpoint_base_url')), 'models': [{'id': 'external/model', 'type': 'llm', 'quantization': 'IQ3_S', 'quantization_source': 'endpoint_reported', 'max_context_length': 32768}]})
        if self.path == '/test/merge-pair':
            # One machine, two engines: an LM Studio benchmark and an endpoint benchmark saved as remote.
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from test_merge import benchmark
            for item in (benchmark('merge-lm'), benchmark('merge-strata', provider='openai', models=('qwen',),
                                                          created='2026-11-13T10:00:00+00:00')):
                store.save(item)
            return self._json({'ok': True})
        if self.path == '/test/start':
            live = copy.deepcopy(run)
            live.update(id='live', label='Ongoing benchmark', created_at='2026-11-01T12:00:00+00:00', status='running', results=[])
            server.BENCH.run = live
            server.BENCH.running = True
            server.BENCH.events = []
            store.save(live)
            return self._json({'ok': True})
        if self.path == '/test/result':
            live = server.BENCH.run
            result = {'model':'model-a', 'target_tokens':1000 + 1000 * len(live['results']), 'status':'ok', 'concurrency':1,
                      'prefill_tps':321 + 333 * len(live['results']), 'prompt_tokens':321 + 333 * len(live['results']), 'completion_tokens':100, 'decode_token_count':99, 'decode_s':99/42, 'wall_s':1+99/42+.02, 'decode_tps':42, 'ttft_s':1}
            live['results'].append(result)
            store.save(live)
            server.BENCH.emit('result', run_id='live', result=result)
            return self._json({'ok': True})
        if self.path == '/test/finish':
            server.BENCH.run['status'] = 'completed'
            store.save(server.BENCH.run)
            server.BENCH.running = False
            server.BENCH.emit('done', run_id='live', status='completed')
            return self._json({'ok': True})
        return super().do_POST()

    def env_payload(self, provider=None):
        return {'gpu':run['gpu'], 'gpus':[], 'host':run['host'], 'hardware':{'os':{'name':'Windows'},'cpu':{'name':'Smoke CPU'},'system':{'memory_bytes':34359738368},'gpus':[{'name':'NVIDIA GeForce RTX 5060 Ti','sensor_index':0,'memory_bytes':17179869184}]}, 'lms':{'cli':True,'server':True},'power':{'source':'none'},'models':[{'id': name, 'type':'llm','max_context_length':300000, 'quantization':'Q4_K_M'} for name in ['model-a', 'model-b'] + ['publisher/qwen-large-instruct-model-%s-GGUF' % i for i in range(10)]], 'default_models':['model-a'],'missing_defaults':[],'corpus':{'approx_tokens':260000}, 'defaults':runner.DEFAULT_CONFIG,'last_config':getattr(self.server, 'last_config', None),'running':False, 'requested_provider':provider, 'capabilities':{'extend':True}}
httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(httpd.server_port,flush=True)
try: httpd.serve_forever()
finally:
    httpd.server_close()
    session.cleanup()
