"""Rolling, local-only 24-hour record of System telemetry.

One JSON object per line, one file per UTC hour. The open hour is appended in
batches; a finished hour is compressed once and never rewritten, so pruning
deletes whole files. A torn last line after a crash is skipped on read and
terminated before appending. Only one helper process writes; others read.
"""
import collections
import gzip
import json
import math
import os
import re
import threading
import time

SEGMENT_S = 3600
RETENTION_S = 86400
BUCKETS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
FIELDS = ('cpu_util_percent', 'gpu_util', 'system_memory_percent', 'vram_percent', 'gpu_w', 'system_w_est',
          'temp_c', 'cpu_temp_c', 'system_memory_used_bytes', 'system_memory_total_bytes', 'vram_mb',
          'vram_total_mb', 'cpu_w', 'clock_mhz')
GPU_FIELDS = ('gpu_util', 'vram_percent', 'vram_mb', 'vram_total_mb', 'gpu_w', 'temp_c', 'clock_mhz')
NAME = re.compile(r'^system-(\d{8}T\d{2})0000Z\.jsonl(\.gz)?$')
LOCK_NAME = 'writer.lock'


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(float(value), 2)


def compact(point, t):
    """Keep plotted fields only. Missing readings stay missing."""
    record = {'t': round(float(t), 3)}
    for field in FIELDS:
        value = _number(point.get(field))
        if value is not None:
            record[field] = value
    gpus = []
    for gpu in point.get('gpus') or []:
        if not isinstance(gpu, dict) or type(gpu.get('index')) is not int:
            continue
        row = {'index': gpu['index']}
        for field in GPU_FIELDS:
            value = _number(gpu.get(field))
            if value is not None:
                row[field] = value
        gpus.append(row)
    if gpus:
        record['gpus'] = gpus
    return record


def segment_name(start):
    return 'system-%s0000Z.jsonl' % time.strftime('%Y%m%dT%H', time.gmtime(start))


def segment_start(name):
    match = NAME.match(name)
    if not match:
        return None
    import calendar
    return calendar.timegm(time.strptime(match.group(1), '%Y%m%dT%H'))


def parse_lines(data):
    """Valid records only: a torn or corrupt line never hides the others."""
    records = []
    for line in data.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and _number(record.get('t')) is not None:
            records.append(record)
    return records


def choose_bucket(span, max_points):
    for bucket in BUCKETS_S:
        if span / bucket <= max_points:
            return bucket
    return BUCKETS_S[-1]


def downsample(records, bucket):
    """Average each aligned bucket and keep its peaks. Empty buckets stay absent."""
    if bucket <= 1:
        return [dict(record) for record in records]
    groups = collections.OrderedDict()
    for record in records:
        groups.setdefault(math.floor(record['t'] / bucket), []).append(record)
    result = []
    for items in groups.values():
        point = {'t': round(sum(item['t'] for item in items) / len(items), 3), 'n': len(items), 'max': {}}
        for field in FIELDS:
            values = [item[field] for item in items if field in item]
            if values:
                point[field] = round(sum(values) / len(values), 2)
                point['max'][field] = max(values)
        cards = collections.OrderedDict()
        for item in items:
            for gpu in item.get('gpus') or []:
                cards.setdefault(gpu.get('index'), []).append(gpu)
        if cards:
            point['gpus'] = []
            for index, rows in cards.items():
                row = {'index': index}
                for field in GPU_FIELDS:
                    values = [gpu[field] for gpu in rows if field in gpu]
                    if values:
                        row[field] = round(sum(values) / len(values), 2)
                point['gpus'].append(row)
        result.append(point)
    return result


def _lock(path):
    handle = open(path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class TelemetryLog:
    """Owner writes the log; reader serves another helper's log; memory keeps no file."""

    def __init__(self, directory=None, retention_s=RETENTION_S, segment_s=SEGMENT_S, flush_s=10.0,
                 clock=time.time, memory_points=3600):
        self.directory = directory
        self.retention_s = retention_s
        self.segment_s = segment_s
        self.flush_s = flush_s
        self.clock = clock
        self.mode = 'memory'
        self.reason = 'No telemetry log directory configured; history is kept in memory only.'
        self._lock = threading.RLock()
        self._memory = collections.deque(maxlen=memory_points)
        self._current = []
        self._current_start = None
        self._handle = None
        self._pending = []
        self._last_flush = 0.0
        self._last_t = None
        self._lock_handle = None
        self._last_claim = 0.0
        self._cache = collections.OrderedDict()
        self._cache_lock = threading.Lock()
        self._first = {}

    # -- ownership -----------------------------------------------------------
    def open(self):
        with self._lock:
            if self.directory is None:
                return self.mode
            try:
                os.makedirs(self.directory, exist_ok=True)
            except OSError as exc:
                self.mode, self.reason = 'memory', 'Telemetry log unavailable (%s); history is kept in memory only.' % exc.__class__.__name__
                return self.mode
            self._claim()
            return self.mode

    def _claim(self):
        self._last_claim = self.clock()
        try:
            handle = _lock(os.path.join(self.directory, LOCK_NAME))
        except OSError as exc:
            self.mode, self.reason = 'memory', 'Telemetry log unavailable (%s); history is kept in memory only.' % exc.__class__.__name__
            return
        if handle is None:
            self.mode, self.reason = 'reader', 'Another Wattever helper on this computer is recording; its log is shown.'
            return
        self._lock_handle = handle
        self.mode, self.reason = 'owner', None
        self.maintain()

    def status(self):
        return {'mode': self.mode, 'persistent': self.mode in ('owner', 'reader'),
                'retention_s': self.retention_s if self.mode != 'memory' else None, 'reason': self.reason}

    # -- writing -------------------------------------------------------------
    def append(self, record):
        with self._lock:
            t = record['t']
            if self._last_t is not None and t <= self._last_t:
                return False
            self._last_t = t
            if self.mode == 'memory':
                self._memory.append(record)
                return True
            if self.mode != 'owner':
                if self.clock() - self._last_claim >= 30:
                    self._claim()
                if self.mode != 'owner':
                    return False
            start = math.floor(t / self.segment_s) * self.segment_s
            if start != self._current_start:
                self._roll(start)
            self._current.append(record)
            self._pending.append((json.dumps(record, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8'))
            if self.clock() - self._last_flush >= self.flush_s:
                self.flush()
            return True

    def _roll(self, start):
        self.flush()
        self._close_handle()
        self._current, self._current_start = [], start
        path = os.path.join(self.directory, segment_name(start))
        try:
            if os.path.exists(path + '.gz'):
                # Clock moved back into a finished hour: keep its compressed file intact.
                self._current = self._read_segment(start)
                path = None
            else:
                self._current = self._read_file(path)
                handle = open(path, 'ab')
                if handle.tell():
                    with open(path, 'rb') as existing:
                        existing.seek(-1, os.SEEK_END)
                        if existing.read(1) != b'\n':
                            handle.write(b'\n')
                self._handle = handle
        except OSError:
            self._handle = None
        self.maintain()

    def flush(self):
        with self._lock:
            self._last_flush = self.clock()
            if not self._pending:
                return
            data, self._pending = b''.join(self._pending), []
            if self._handle is None:
                return
            try:
                self._handle.write(data)
                self._handle.flush()
            except OSError as exc:
                # A full or removed disk loses this hour's later writes; the next hour retries.
                self.reason = 'Telemetry log write failed (%s); recent readings may be missing after a restart.' % exc.__class__.__name__
                self._close_handle()

    def _close_handle(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None

    def close(self):
        with self._lock:
            self.flush()
            self._close_handle()
            if self._lock_handle is not None:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        self._lock_handle.seek(0)
                        msvcrt.locking(self._lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                self._lock_handle.close()
                self._lock_handle = None
            if self.mode == 'owner':
                self.mode = 'closed'

    # -- housekeeping --------------------------------------------------------
    def _segments(self):
        found = {}
        try:
            names = os.listdir(self.directory)
        except OSError:
            return found
        for name in names:
            start = segment_start(name)
            if start is not None:
                found.setdefault(start, set()).add(name)
        return found

    def maintain(self):
        """Compress finished hours and delete hours older than the retention."""
        if self.mode != 'owner' or self.directory is None:
            return
        now = self.clock()
        cutoff = now - self.retention_s
        for start, names in sorted(self._segments().items()):
            plain = segment_name(start)
            base = os.path.join(self.directory, plain)
            try:
                if start + self.segment_s <= cutoff:
                    for name in names:
                        os.remove(os.path.join(self.directory, name))
                    continue
                if start == self._current_start or start + self.segment_s > now or plain not in names:
                    continue
                if plain + '.gz' not in names:
                    with open(base, 'rb') as source:
                        data = source.read()
                    with gzip.open(base + '.gz.tmp', 'wb') as target:
                        target.write(data)
                    os.replace(base + '.gz.tmp', base + '.gz')
                os.remove(base)
            except OSError:
                continue  # Another reader may hold the file on Windows; retry at the next hour.

    # -- reading -------------------------------------------------------------
    @staticmethod
    def _read_file(path):
        try:
            with open(path, 'rb') as handle:
                return parse_lines(handle.read().decode('utf-8', 'replace'))
        except OSError:
            return []

    def _read_segment(self, start):
        base = os.path.join(self.directory, segment_name(start))
        try:
            with gzip.open(base + '.gz', 'rb') as handle:
                return parse_lines(handle.read().decode('utf-8', 'replace'))
        except (OSError, EOFError):
            return self._read_file(base)

    def _bucketed(self, start, bucket, current=None):
        """Finished hours are immutable, so their bucketed form is cached."""
        key = (start, bucket)
        if current is None:
            with self._cache_lock:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
        records = sorted(current if current is not None else self._read_segment(start), key=lambda record: record['t'])
        points = downsample(records, bucket)
        if current is None:
            with self._cache_lock:
                self._cache[key] = points
                self._first[start] = records[0]['t'] if records else None
                while len(self._cache) > 48:
                    self._cache.popitem(last=False)
        elif records:
            self._first.setdefault(start, records[0]['t'])
        return points

    def query(self, start=None, end=None, max_points=900):
        """Points in [start, end], averaged into buckets that keep at most max_points."""
        now = self.clock()
        end = min(now, now if end is None else float(end))
        start = end - 900 if start is None else float(start)
        start = max(start, now - self.retention_s)
        max_points = max(10, min(4000, int(max_points)))
        bucket = choose_bucket(max(0.0, end - start), max_points)
        with self._lock:
            mode, status = self.mode, self.status()
            memory = [r for r in self._memory if start <= r['t'] <= end] if mode == 'memory' else None
            oldest = self._memory[0]['t'] if mode == 'memory' and self._memory else None
            current_start = self._current_start if mode == 'owner' else None
            current = list(self._current) if current_start is not None else None
        # Disk reads happen outside the write lock so recording never waits for a long query.
        if mode == 'memory':
            points = downsample(memory, bucket)
        else:
            points = []
            segments = set(self._segments())
            if current_start is not None:
                segments.add(current_start)
            for first in sorted(segments):
                if first + self.segment_s <= now - self.retention_s:
                    continue
                live = current if first == current_start else None
                if live is None and first + self.segment_s > now:
                    live = self._read_segment(first)  # another helper's open hour
                if oldest is None:
                    if first not in self._first:
                        self._bucketed(first, BUCKETS_S[-1], live)
                    oldest = self._first.get(first)
                if first + self.segment_s <= start or first > end:
                    continue
                points.extend(p for p in self._bucketed(first, bucket, live) if start <= p['t'] <= end)
        if oldest is not None:
            oldest = max(oldest, now - self.retention_s)
        return {'start': start, 'end': end, 'bucket_s': bucket, 'gap_s': max(4.0, 2.5 * bucket),
                'retention_s': self.retention_s if mode != 'memory' else None, 'oldest': oldest,
                'log': status, 'points': points}
