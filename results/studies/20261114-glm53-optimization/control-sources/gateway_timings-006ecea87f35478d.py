"""Bounded payload-free timings from gateway ASGI ingress to response completion."""
from collections import OrderedDict
import asyncio
import re
import threading
import time
import uuid

TRACE_HEADER = 'x-strata-control-request-trace'
TRACE_STATE = 'strata_control_request_trace'


def valid_trace(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None


class RequestTimings:
    def __init__(self, limit=500):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('Timing retention must be between 1 and 500')
        self.limit = limit
        self._rows = OrderedDict()
        self._lock = threading.Lock()

    def finish(self, trace_id, started_at, finished_at, elapsed, status, outcome):
        # No URL/query, model identifier, headers, messages or generated text.
        row = {'trace_id': trace_id, 'started_at': started_at, 'finished_at': finished_at,
               'wall_s': round(max(0.0, elapsed), 6), 'status_code': status, 'outcome': outcome}
        with self._lock:
            self._rows[trace_id] = row
            self._rows.move_to_end(trace_id)
            while len(self._rows) > self.limit:
                self._rows.popitem(last=False)

    def snapshot(self):
        with self._lock:
            return [dict(row) for row in reversed(self._rows.values())]

    def clear(self):
        with self._lock:
            self._rows.clear()


class GatewayWallMiddleware:
    """Include gateway validation, lock wait, model load, relay and final send.

    The last ASGI body send marks completion, not return from a route that may
    still have an SSE stream to send. Client receipt/network time is excluded.
    """
    def __init__(self, app, store, clock=time.perf_counter, timestamp=time.time, trace_factory=None):
        self.app, self.store = app, store
        self.clock, self.timestamp = clock, timestamp
        self.trace_factory = trace_factory or (lambda: uuid.uuid4().hex)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') != '/v1/chat/completions':
            return await self.app(scope, receive, send)
        started, started_at = self.clock(), self.timestamp()
        trace_id = self.trace_factory()
        # Never accept a client-chosen trace. Do not publish the internal ID on
        # completion responses; only the loopback diagnostics endpoint lists it.
        scope = dict(scope, headers=[(key, value) for key, value in scope.get('headers', [])
                                    if key.lower() != TRACE_HEADER.encode('ascii')],
                     state=dict(scope.get('state') or {}))
        scope['state'][TRACE_STATE] = trace_id
        status, recorded, disconnected = None, False, False

        def finish(outcome):
            nonlocal recorded
            if not recorded:
                self.store.finish(trace_id, started_at, self.timestamp(), self.clock() - started, status, outcome)
                recorded = True

        async def observed_receive():
            nonlocal disconnected
            message = await receive()
            if message['type'] == 'http.disconnect':
                disconnected = True
            return message

        async def observed_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message.get('status')
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                finish('disconnected' if disconnected else 'error' if status and status >= 400 else 'completed')

        try:
            return await self.app(scope, observed_receive, observed_send)
        except BaseException as exc:
            finish('disconnected' if disconnected or isinstance(exc, (OSError, asyncio.CancelledError))
                   or type(exc).__name__ == 'ClientDisconnect' else 'error')
            raise
        finally:
            finish('disconnected' if disconnected else 'incomplete')
