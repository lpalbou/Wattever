"""Experimental Maya API, started only by Strata Control with active-only lifecycle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import math
from pathlib import Path
import sys

import psutil
import maya_hardware
from desktop_backend import install_request_wall_metrics

ROOT = Path(__file__).resolve().parent


def control_ancestor_matches(expected_pid, expected_birth, chain):
    """Allow at most two Python redirectors, never a shell or unknown parent."""
    for row in chain[:3]:
        if row['pid'] == expected_pid:
            def is_control(arg):
                path = Path(arg)
                if not path.is_absolute():
                    path = Path(row.get('cwd') or ROOT) / path
                return path.resolve() == ROOT / 'strata_app.py'
            return (row['born'] == expected_birth
                    and any(is_control(arg) for arg in row['argv']))
        if Path(row['exe']).name.lower() not in ('python.exe', 'pythonw.exe'):
            return False
    return False


def install_manual_policy(server, expected_model, *, requested_mtp=None, condition=None):
    """Guard the vendor module in memory; never modify its pinned source files."""
    constructed = []
    initial_engine = server.StrataEngine.__init__
    initial_service = server.Service.__init__
    initial_status = server.Service.v1_status
    initial_handler = server.make_handler
    kv_streaming = (condition or {}).get('kv_streaming')
    ram_tier=((condition or {}).get('memory_policy_requested') or {}).get('ram_tier_global_gib')
    ram_auto = ((condition or {}).get('memory_policy_requested') or {}).get('ram_tier_policy') == 'auto'
    hardware_plan = (condition or {}).get('hardware_plan')
    tuning = (condition or {}).get('runtime_tuning_requested')
    if ram_tier is not None and (type(ram_tier) not in (int,float) or not math.isfinite(ram_tier) or ram_tier < 0):
        raise ValueError('Invalid explicit RAM tier in the reviewed condition.')
    if (requested_mtp is not None or kv_streaming in ('on', 'off') or ram_tier is not None or ram_auto or hardware_plan or tuning is not None) and hasattr(server, 'child_env'):
        initial_env = server.child_env
        def child_env(cfg):
            env = initial_env(cfg)
            if tuning is not None:
                maya_hardware.apply_runtime_tuning_env(env, tuning)
            if requested_mtp is not None:
                for key in ('MAYA_GLM_NO_MTP', 'STRATA_GLM_NO_MTP', 'MAYA_GLM_NO_SPEC', 'STRATA_GLM_NO_SPEC'):
                    env.pop(key, None)
                    if not requested_mtp:
                        env[key] = '1'
                for key in ('MAYA_GLM_MTP', 'STRATA_GLM_MTP'):
                    env.pop(key, None)
                    if requested_mtp:
                        env[key] = '1'
            if kv_streaming in ('on', 'off'):
                for key in ('MAYA_GLM_KV_RESIDENT', 'STRATA_GLM_KV_RESIDENT'):
                    env[key] = '32768' if kv_streaming == 'on' else '0'
            if hardware_plan:
                for key in ('MAYA_GLM_DEVS','STRATA_GLM_DEVS','MAYA_GLM_DEV1','STRATA_GLM_DEV1','MAYA_GLM_SPLIT','STRATA_GLM_SPLIT'):
                    env.pop(key, None)
            if ram_auto:
                for key in ('MAYA_GLM_RAM_GB','STRATA_GLM_RAM_GB'):
                    env.pop(key, None)
                for key in ('MAYA_GLM_RAM_HEADROOM_GB','STRATA_GLM_RAM_HEADROOM_GB'):
                    env[key]='12'
            if ram_tier is not None:
                for key in ('MAYA_GLM_RAM_GB','STRATA_GLM_RAM_GB'):
                    env[key]=format(ram_tier, 'g')
                for key in ('MAYA_GLM_RAM_HEADROOM_GB','STRATA_GLM_RAM_HEADROOM_GB'):
                    env[key]='12'
            return env
        server.child_env = child_env

    def denied(*args, **kwargs):
        raise server.EngineDied('Load or unload this model in Strata Control. API lifecycle is disabled.')

    def construct(engine, *args, **kwargs):
        if constructed:
            denied()
        # Record before construction: failure never authorizes a hidden retry.
        constructed.append(engine)
        initial_engine(engine, *args, **kwargs)

    def service_init(service, engine, *args, **kwargs):
        if not constructed or engine is not constructed[0]:
            denied()
        initial_service(service, engine, *args, **kwargs)
        if service.model != expected_model:
            denied()
        service._control_engine = engine
        service.model_management = False
        service.idle_unload_s = 0

    def require_active(service):
        if (getattr(service, 'model', None) != expected_model
                or getattr(service, 'engine', None) is not getattr(service, '_control_engine', None)
                or not callable(getattr(service.engine, 'alive', None))
                or service.engine.alive() is not True):
            denied()

    def status(service):
        value = initial_status(service)
        try:
            require_active(service)
            loaded = True
        except server.EngineDied:
            loaded = False
        value.update(loaded=loaded, auto_load=False, model_routing='active-only',
                     backend='project-maya-experimental', model_management=False)
        value['engine_info'] = dict(getattr(service.engine, 'info', {}) or {})
        value['gpu_placement'] = list(getattr(service.engine, 'vram', []) or [])
        value['expert_tier_statistics'] = dict(getattr(service.engine, 'stat', {}) or {})
        value['expert_tier_statistics_scope'] = 'Latest native STAT snapshot, emitted every8tokens; rounded per-token rates for the current/last request, not raw transferred bytes or lifetime totals.'
        value['recorded_condition'] = dict(condition or {})
        return value

    def make_handler(service):
        base = initial_handler(service)

        class ActiveOnlyHandler(base):
            def _json(self, code, value):
                if getattr(self, 'path', '').split('?')[0].rstrip('/') == '/props' and code == 200 and isinstance(value, dict):
                    value = dict(value, service='maya', backend='project-maya-experimental',
                                 engine_version=server.MAYA_VERSION,
                                 build_info='Maya ' + str(server.MAYA_VERSION) + ' / core ' + str(getattr(service.engine, 'info', {}).get('version', 'unknown')),
                                 context_allocation_note='Engine maximum; verify every additional slot against the loaded sequence allocation and SEQ_CTX environment.')
                return super()._json(code, value)

            def end_headers(self):
                if (self.headers.get('Access-Control-Request-Private-Network', '').lower() == 'true'
                        and self._cors_allow()):
                    self.send_header('Access-Control-Allow-Private-Network', 'true')
                return super().end_headers()

            def do_GET(self):
                path = self.path.split('?')[0].rstrip('/')
                if path == '/health':
                    # Explicit backend identity for generic endpoint discovery; no lifecycle action.
                    self._json(200, {'status': 'ok', 'service': 'maya',
                                     'backend': 'project-maya-experimental',
                                     'engine_version': server.MAYA_VERSION,
                                     'model_routing': 'active-only', 'auto_load': False})
                    return
                if path == '/v1/models':
                    try:
                        require_active(service)
                        models = [{'id': expected_model, 'object': 'model',
                                   'created': int(getattr(service, 'started_at', 0)),
                                   'owned_by': 'zai-org', 'backend': 'project-maya-experimental',
                                   'engine_version': server.MAYA_VERSION,
                                   'quantization': (condition or {}).get('quantization', 'Maya-XS mixed IQ1_S + IQ2_XXS + BF16-compat')}]
                    except server.EngineDied:
                        models = []
                    self._json(200, {'object': 'list', 'data': models})
                    return
                if (path not in ('/health', '/status', '/metrics', '/metrics/prometheus', '/v1/models', '/v1/status', '/slots', '/props')
                        and not path.startswith('/v1/responses/')):
                    self._json(404, {'error': {'message': 'This experimental endpoint exposes inference and read-only status.'}})
                    return
                return super().do_GET()

            def do_POST(self):
                path = self.path.split('?')[0].rstrip('/')
                allowed = path in ('/v1/chat/completions', '/v1/responses', '/v1/messages', '/v1/strata/cancel')
                cancel = path.startswith('/v1/responses/') and path.endswith('/cancel')
                if not allowed and not cancel:
                    self.close_connection = True
                    self._json(403, {'error': {'type': 'permission_error', 'message': 'Use Strata Control for model and server settings.'}})
                    return
                try:
                    require_active(service)
                except server.EngineDied as error:
                    self.close_connection = True
                    self._json(503, {'error': {'type': 'model_not_loaded', 'message': str(error)}})
                    return
                return super().do_POST()

            def do_DELETE(self):
                self.close_connection = True
                self._json(403, {'error': {'message': 'API model lifecycle is disabled.'}})

            def _checked_model(self, req, parent):
                if not isinstance(req, dict) or req.get('model') != expected_model:
                    self._json(400, {'error': {'type': 'invalid_request_error', 'code': 'model_not_available',
                                             'param': 'model', 'message': 'Use the currently loaded model returned by /v1/models.'}})
                    return
                try:
                    require_active(service)
                    return parent(req)
                except server.EngineDied as error:
                    if getattr(self, 'answer_started', False):
                        return self._fail(503, 'model_not_loaded', str(error))
                    self.close_connection = True
                    self._json(503, {'error': {'type': 'model_not_loaded', 'message': str(error)}})

            def _openai(self, req):
                return self._checked_model(req, super()._openai)

            def _responses(self, req):
                return self._checked_model(req, super()._responses)

            def _anthropic(self, req):
                return self._checked_model(req, super()._anthropic)

        return ActiveOnlyHandler

    server.StrataEngine.__init__ = construct
    server.StrataEngine.restart = denied
    server.Service.__init__ = service_init
    server.Service.ensure_engine = require_active
    server.Service.v1_status = status
    server.make_handler = make_handler
    for name in ('_reload', '_restart_with', '_restart_into', '_restart_on_port',
                 '_restart_for_models', 'switch_model'):
        if hasattr(server.Service, name):
            setattr(server.Service, name, denied)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vendor-root', required=True)
    parser.add_argument('--server-sha256', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--port', type=int, default=8097)
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default=None)
    parser.add_argument('--control-parent-birth', type=float, required=True)
    args = parser.parse_args()
    expected = os.environ.get('STRATA_CONTROL_PARENT', '')
    chain = []
    for parent in psutil.Process().parents()[:3]:
        chain.append(dict(pid=parent.pid, born=parent.create_time(), exe=parent.exe(),
                          cwd=parent.cwd(), argv=parent.cmdline()))
    if not expected.isdigit() or not control_ancestor_matches(int(expected), args.control_parent_birth, chain):
        raise RuntimeError('Open Strata Control to load the experimental Maya model.')
    vendor = Path(args.vendor_root).resolve(strict=True)
    source = vendor / 'serve/server.py'
    if hashlib.sha256(source.read_bytes()).hexdigest() != args.server_sha256:
        raise RuntimeError('Pinned experimental server source changed; prepare and review it again.')
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if config.get('model_management') is not False or config.get('idle_unload_s') != 0:
        raise RuntimeError('Experimental backend must disable API model management and idle reload.')
    listen = args.host or config.get('host') or '127.0.0.1'
    if listen not in ('127.0.0.1', '0.0.0.0'):
        raise RuntimeError('Experimental backend host must be 127.0.0.1 or 0.0.0.0.')
    if config.get('host') not in (None, listen):
        raise RuntimeError('Experimental backend host does not match the prepared control config.')
    sys.path.insert(0, str(vendor))
    from serve import server
    install_manual_policy(server, config['model_name'], requested_mtp=config['control_mtp_enabled'],
                          condition=config['control_condition'])
    install_request_wall_metrics(server)
    sys.argv = [str(source), '--engine', 'maya', '--config', args.config,
                '--host', listen, '--port', str(args.port)]
    return server.main()


if __name__ == '__main__':
    raise SystemExit(main())
