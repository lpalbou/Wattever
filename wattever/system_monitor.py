"""Always-on machine observatory, independent of viewers and inference endpoints.

The helper samples the machine once per second for as long as it runs and
appends each reading to a rolling local log, so System can show what happened
while nobody was watching. While a benchmark measures with its own sampler the
monitor reads that sampler instead of running a second sensor stream beside it.
"""
import threading
import time

from . import power
from .telemetry_log import TelemetryLog, compact


class SystemMonitor:
    """Own one sampler, record it continuously and answer history queries."""

    def __init__(self, interval_ms=1000, borrow=None, clock=time.time, poll_s=0.25, stale_s=4.0):
        self.interval_ms = interval_ms
        self.borrow = borrow
        self.clock = clock
        self.poll_s = poll_s
        self.stale_s = stale_s
        self.log = TelemetryLog(clock=clock)
        self._lock = threading.RLock()
        self._sampler = None
        self._active = None
        self._origin = 'monitor'
        self._thread = None
        self._stop = threading.Event()
        self._started = False
        self._final = False
        self.error = None
        self._retry_at = 0.0
        self._retry_delay = 5.0
        self._last_t = None

    # -- lifecycle -----------------------------------------------------------
    def start(self, log_dir=None):
        """Begin continuous capture. Sensor failures are reported and retried, never raised."""
        with self._lock:
            if self._started or self._final:
                return
            self._started = True
            self._stop.clear()
            if log_dir is not None:
                self.log = TelemetryLog(log_dir, clock=self.clock)
            self.log.open()
            self._select()
            self._thread = threading.Thread(target=self._loop, name='wattever-system-monitor', daemon=True)
            self._thread.start()

    def stop(self, final=False):
        """Stop sensors and flush the log. A final stop at shutdown also refuses later lazy restarts."""
        with self._lock:
            self._final = self._final or final
            self._started = False
            self._stop.set()
            worker, self._thread = self._thread, None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=3.0)
        with self._lock:
            self._release()
            self._active = None
            self.log.close()

    def _release(self):
        sampler, self._sampler = self._sampler, None
        if sampler is not None:
            sampler.stop()

    def _ensure_sampler(self):
        if self._sampler is not None:
            if 'ended unexpectedly' not in str(self._sampler.error or ''):
                return
            self.error = self._sampler.error
            self._release()
            self._retry_at = time.monotonic() + self._retry_delay
        if time.monotonic() < self._retry_at:
            return
        sampler = power.PowerSampler(interval_ms=self.interval_ms)
        try:
            sampler.start()
        except Exception as exc:  # noqa: BLE001 - sensors must not stop the helper
            try:
                sampler.stop()
            except Exception:  # noqa: BLE001
                pass
            self.error = 'System sensors could not start: %s' % exc
            self._retry_at = time.monotonic() + self._retry_delay
            self._retry_delay = min(300.0, self._retry_delay * 2)
            return
        self._sampler, self.error, self._retry_delay = sampler, None, 5.0

    def _select(self):
        """Use a running benchmark sampler if there is one, otherwise our own."""
        external = None
        if self.borrow is not None:
            try:
                external = self.borrow()
            except Exception:  # noqa: BLE001
                external = None
        if external is not None:
            self._release()
            self._active, self._origin = external, 'benchmark'
        else:
            self._ensure_sampler()
            self._active, self._origin = self._sampler, 'monitor'
        return self._active

    # -- capture -------------------------------------------------------------
    def tick(self):
        """Record the newest fresh reading once; repeated or stale readings are skipped."""
        with self._lock:
            if not self._started:
                return False
            sampler = self._select()
        point = sampler.now() if sampler is not None else None
        if not point:
            return False
        age = point.get('sample_age_s')
        age = age if isinstance(age, (int, float)) and age >= 0 else 0.0
        if age > self.stale_s:
            return False
        t = self.clock() - age
        # Benchmarks can sample faster than 1 Hz; the log keeps about one reading per interval.
        if self._last_t is not None and t < self._last_t + 0.9 * self.interval_ms / 1000.0:
            return False
        self._last_t = t
        return self.log.append(compact(point, t))

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - keep recording after a bad reading
                self.error = 'System history recording error: %s' % exc
            self._stop.wait(self.poll_s)
        self.log.flush()

    # -- views ---------------------------------------------------------------
    def snapshot(self):
        if not self._started:
            self.start()
        if self._final:
            return {'timestamp': self.clock(), 'telemetry': None, 'scope': {}, 'source': 'none',
                    'error': 'Wattever is shutting down.', 'sampling_interval_ms': self.interval_ms,
                    'capture': self.capture()}
        with self._lock:
            sampler, origin, error = self._active, self._origin, self.error
        if sampler is None:
            return {'timestamp': self.clock(), 'telemetry': None, 'scope': {}, 'source': 'none',
                    'error': error or 'System sensors are starting.', 'sampling_interval_ms': self.interval_ms,
                    'capture': self.capture(origin)}
        telemetry = sampler.now()
        now = self.clock()
        if telemetry:
            age = telemetry.get('sample_age_s')
            telemetry['t'] = now - (age if isinstance(age, (int, float)) and age >= 0 else 0.0)
        return {'timestamp': now, 'telemetry': telemetry,
                'scope': sampler.scope_metadata(), 'source': sampler.source,
                'error': error or sampler.error,
                'sampling_interval_ms': sampler.interval_ms, 'capture': self.capture(origin)}

    def capture(self, origin=None):
        return {'continuous': True, 'sampler': origin or self._origin,
                'record_interval_ms': self.interval_ms, 'log': self.log.status()}

    def history(self, start=None, end=None, max_points=900):
        if not self._started:
            self.start()
        return dict(self.log.query(start, end, max_points), capture=self.capture())
