"""On-demand machine observatory, independent of inference endpoint state."""
import threading
import time

from . import power


class SystemMonitor:
    """Share one sampler across viewers and release sensors after inactivity."""

    def __init__(self, idle_s=15):
        self.idle_s = idle_s
        self._lock = threading.Lock()
        self._sampler = None
        self._timer = None
        self._last_view = 0
        self._generation = 0

    def snapshot(self):
        with self._lock:
            self._last_view = time.monotonic()
            if self._sampler is None:
                sampler = power.PowerSampler(interval_ms=1000)
                try:
                    sampler.start()
                except Exception:
                    sampler.stop()
                    raise
                self._sampler = sampler
                self._generation += 1
                self._schedule_expiry()
            sampler = self._sampler
            return {"timestamp": time.time(), "telemetry": sampler.now(),
                    "scope": sampler.scope_metadata(), "source": sampler.source,
                    "error": sampler.error,
                    "sampling_interval_ms": sampler.interval_ms}

    def _schedule_expiry(self, delay=None):
        generation = self._generation
        self._timer = threading.Timer(self.idle_s if delay is None else delay,
                                      lambda: self._expire(generation))
        self._timer.daemon = True
        self._timer.start()

    def _expire(self, generation=None):
        with self._lock:
            if self._sampler is None or (generation is not None and generation != self._generation):
                return
            remaining = self.idle_s - (time.monotonic() - self._last_view)
            if remaining > 0:
                self._schedule_expiry(remaining)
                return
            self._stop_locked()

    def _stop_locked(self):
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._sampler is not None:
            self._sampler.stop()
            self._sampler = None

    def stop(self):
        with self._lock:
            self._stop_locked()
