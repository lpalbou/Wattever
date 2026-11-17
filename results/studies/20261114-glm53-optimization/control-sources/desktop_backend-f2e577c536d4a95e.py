"""Run the installed server with request-triggered engine restarts disabled."""
import os
from collections import deque
from pathlib import Path
import sys
import threading
import time
from gateway_timings import TRACE_HEADER, valid_trace


def install_manual_load_policy(server):
    def require_loaded(service):
        if not service.loaded():
            raise server.EngineDied('The model is not loaded. Load it from Strata Control; API requests cannot start it.')
    server.Service.ensure_loaded = require_loaded
    def died(service, error):
        print(f'[Strata Control] {error}. Reload the model from the desktop app.', flush=True)
    server.Service._say_died = died
    if hasattr(server.Service, 'v1_status'):
        original_status = server.Service.v1_status
        def status(service):
            result = original_status(service)
            result['auto_load'] = False
            return result
        server.Service.v1_status = status


def install_expert_device_policy(server):
    """Keep native Qwen35 expert devices out of the older Flash layer-split adapter."""
    original_split = server.layer_split_of
    def layer_split(config):
        if '--expert-devices' in (config.get('args') or []):
            return False
        return original_split(config)
    server.layer_split_of = layer_split


def install_request_wall_metrics(server, *, clock=time.perf_counter, timestamp=time.time):
    """Time backend POST dispatch through completion, including body/prepare/queue.

    History rows are captured by object identity in the handler's own thread.
    This does not enable the installed API monitor or retain request contents.
    Gateway model loading and client-side network time are outside this scope.
    """
    factory = getattr(server, 'make_handler', None)
    if factory is None or getattr(factory, '_strata_control_wall', False):
        return
    current = threading.local()

    class TimedHistory(deque):
        def __init__(self, rows, maxlen, owner):
            super().__init__(rows, maxlen=maxlen)
            self.owner = owner

        def append(self, row):
            super().append(row)
            context = getattr(current, 'request', None)
            if context is not None and context['service'] is self.owner and isinstance(row, dict):
                context['rows'].append(row)

    def make_handler(service):
        if not isinstance(service.history, TimedHistory):
            service.history = TimedHistory(service.history, maxlen=service.history.maxlen, owner=service)
        original = factory(service)

        class Handler(original):
            def do_POST(self):
                previous = getattr(current, 'request', None)
                context = {'clock': clock(), 'time': timestamp(), 'rows': [], 'service': service}
                trace = getattr(self, 'headers', {}).get(TRACE_HEADER)
                if not valid_trace(trace):
                    trace = None
                current.request = context
                try:
                    return super().do_POST()
                finally:
                    elapsed = max(0.0, clock() - context['clock'])
                    finished = timestamp()
                    try:
                        with service.status_lock:
                            for row in context['rows']:
                                row.update(request_wall_s=round(elapsed, 6), wall_scope='backend_http',
                                           request_started_at=context['time'], request_finished_at=finished)
                                if trace is not None:
                                    row['gateway_trace_id'] = trace
                                if len(context['rows']) > 1:
                                    row['http_request_engine_runs'] = len(context['rows'])
                    finally:
                        current.request = previous

        return Handler

    make_handler._strata_control_wall = True
    server.make_handler = make_handler


def main():
    if os.environ.get('STRATA_CONTROL_PARENT') != str(os.getppid()):
        raise RuntimeError('Open Strata Control to load a model.')
    # The parent supplies the previously verified installed terminal as cwd.
    sys.path.insert(0, str(Path.cwd()))
    from serve import server
    install_manual_load_policy(server)
    install_expert_device_policy(server)
    install_request_wall_metrics(server)
    return server.main()


if __name__ == '__main__':
    raise SystemExit(main())
