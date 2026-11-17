"""Bounded startup-fatal observation for one Control-owned Maya load."""
import os
from pathlib import Path
import re


class StartupFatalCursor:
    """Read only complete lines appended after this load captured the old EOF."""
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.floor = 0
        self.identity = None
        self.pending = b''
        self.floor_is_line_start = True
        self.failure = None
        try:
            with self.path.open('rb') as stream:
                stat = os.fstat(stream.fileno())
                if stat.st_size:
                    stream.seek(stat.st_size - 1)
                    self.floor_is_line_start = stream.read(1) == b'\n'
        except FileNotFoundError:
            return
        self.offset = stat.st_size
        self.floor = self.offset
        self.identity = (stat.st_dev, stat.st_ino)

    def poll(self):
        if self.failure is not None:
            return self.failure
        try:
            with self.path.open('rb') as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.identity is not None and identity != self.identity:
                    raise RuntimeError('Owned startup log was replaced; startup state is unknown.')
                self.identity = identity
                if stat.st_size < self.offset:
                    raise RuntimeError('Owned startup log was truncated; startup state is unknown.')
                # Native/library writers can update bytes already observed near EOF.
                # Re-read a bounded overlap rather than treating changed bytes as fatal.
                # Never cross the EOF captured before this particular owned startup.
                start = max(self.floor, self.offset - 8192)
                line_start = self.floor_is_line_start
                if start > self.floor:
                    stream.seek(start - 1)
                    line_start = stream.read(1) == b'\n'
                stream.seek(start)
                chunk = stream.read(65536)
                self.offset = max(self.offset, start + len(chunk))
        except FileNotFoundError:
            if self.identity is not None:
                raise RuntimeError('Owned startup log disappeared; startup state is unknown.') from None
            return None
        complete = chunk.split(b'\n')
        complete.pop()  # incomplete trailing line is reconsidered in the next overlap
        if not line_start and complete:
            complete.pop(0)  # a window beginning inside a line cannot create a protocol prefix
        for raw in complete:
            line = raw.rstrip(b'\r').decode('utf-8', errors='replace')
            prefix = 'maya generate: glm fast: '
            if not line.startswith(prefix):
                continue
            message = line[len(prefix):]
            if (message.startswith('WDDM cap ') or
                message.startswith('the warmed expert tables did not upload (') or
                    re.match(r'CUDA[0-9]+ warm (?:entry has prior CUDA error|.* failed \()', message)):
                self.failure = line
                return self.failure
        return None


def cleanup_failed_start(controller, previous_process, previous_job, failure):
    """Only a job/process created by this load can trigger its verified owned cleanup."""
    if controller.job is previous_job and controller.model_process is previous_process:
        return
    try:
        controller._unload_maya()
    except Exception as cleanup_error:
        raise RuntimeError(f'{failure}\nOwned startup cleanup failed: {cleanup_error}. Shutdown remains unknown.') from failure
