import sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wattever import server, store, runner
from http.server import ThreadingHTTPServer
session = tempfile.TemporaryDirectory()
store.RUNS_DIR = session.name + '/runs'
store.TRASH_DIR = session.name + '/trash'
store.CONFIG_PATH = session.name + '/config.json'
run = {'id':'smoke', 'label':'NVIDIA GeForce RTX 5060 Ti', 'created_at':'2026-10-26T12:00:00+00:00', 'status':'completed', 'gpu':{'name':'NVIDIA GeForce RTX 5060 Ti','vram_total_mb':16384, 'power_readable':True}, 'host':{'cpu':'Smoke CPU','ram_gb':32}, 'config':dict(runner.DEFAULT_CONFIG, models=['model-a']), 'results':[{'model':'model-a','target_tokens':512,'status':'ok','concurrency':1,'prefill_tps':100,'decode_tps':50}, {'model':'model-a','target_tokens':512,'status':'ok','concurrency':2,'aggregate_completion_tps':75,'completed_requests':2,'requests':[{'status':'ok','decode_tps':40,'prompt_tokens':512},{'status':'ok','decode_tps':35,'prompt_tokens':512}]}]}
run['results'].extend([
    {'model':'model-b','target_tokens':512,'status':'ok','concurrency':1,'prefill_tps':150,'decode_tps':60},
    {'model':'model-c','target_tokens':512,'status':'ok','concurrency':1,'prefill_tps':200,'decode_tps':70},
])
for row in run['results']:
    if row.get('concurrency', 1) == 1:
        row['ttft_s'] = 0.5
store.save(run)
import copy
comparison = copy.deepcopy(run)
comparison.update(id='comparison', label='GTX 1080 Ti 11 GB VRAM / 64 GB RAM / Intel Core i7 desktop processor', created_at='2026-10-25T12:00:00+00:00')
comparison['gpu']['name'] = 'NVIDIA GeForce GTX 1080 Ti'
comparison['results'][0].update(prefill_tps=70, decode_tps=30)
comparison['results'][1]['aggregate_completion_tps'] = 50
comparison['results'] = [row for row in comparison['results'] if row['model'] != 'model-c']
store.save(comparison)
class FakeBench:
    _start_lock = threading.Lock()
    running = False
    run = None
    events = []
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

class Handler(server.Handler):
    def do_POST(self):
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
                      'prefill_tps':321 + 333 * len(live['results']), 'decode_tps':42, 'ttft_s':0.5}
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

    def env_payload(self):
        return {'gpu':run['gpu'], 'gpus':[], 'host':run['host'], 'hardware':{'os':{'name':'Windows'},'cpu':{'name':'Smoke CPU'},'system':{'memory_bytes':34359738368},'gpus':[{'name':'NVIDIA GeForce RTX 5060 Ti','sensor_index':0,'memory_bytes':17179869184}]}, 'lms':{'cli':True,'server':True},'power':{'source':'none'},'models':[{'id': name, 'type':'llm','max_context_length':300000, 'quantization':'Q4_K_M'} for name in ['model-a', 'model-b'] + ['publisher/qwen-large-instruct-model-%s-GGUF' % i for i in range(10)]], 'default_models':['model-a'],'missing_defaults':[],'corpus':{'approx_tokens':260000}, 'defaults':runner.DEFAULT_CONFIG,'last_config':None,'running':False}
httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(httpd.server_port,flush=True)
try: httpd.serve_forever()
finally:
    httpd.server_close()
    session.cleanup()
