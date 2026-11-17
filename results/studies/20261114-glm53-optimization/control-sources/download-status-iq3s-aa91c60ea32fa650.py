"""Report durable download chunks, excluding sparse/unwritten file space."""
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
FOLDER = ROOT / 'models/models/IQ3_S'
SIZES = (54_817_524_224, 28_800_138_432)

def main():
    total = sum(SIZES)
    completed = 0
    starts = []
    verified = 0
    for index, expected in enumerate(SIZES, 1):
        path = FOLDER / f'Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-{index:05d}-of-00002.gguf'
        if path.with_name(path.name + '.done').exists():
            completed += expected
            verified += 1
            continue
        side = path.with_name(path.name + '.part.chunks')
        if side.exists():
            data = json.loads(side.read_text())
            completed += sum(min(data['chunk'], data['total'] - i * data['chunk'])
                             for first, end in data['done'] for i in range(first, end))
        source = path.with_name(path.name + '.part.src')
        if source.exists():
            starts.append(source.stat().st_birthtime)
    sample = ROOT / 'download-iq3s-status.json'
    now = time.time()
    rate = None
    try:
        previous = json.loads(sample.read_text())
        elapsed = now - previous['time']
        if elapsed >= 15 and completed > previous['completed']:
            rate = (completed - previous['completed']) / elapsed
    except (OSError, ValueError, KeyError):
        pass
    if rate is None and starts and now > min(starts):
        rate = completed / (now - min(starts))
    sample.write_text(json.dumps({'time': now, 'completed': completed}))
    print(f'IQ3_S: {completed / 1e9:.2f} / {total / 1e9:.2f} GB ({completed / total:.1%}); verified shards {verified}/2')
    if verified == 2:
        print('Model download verified; see download-iq3s.log for preparation status.')
    elif rate:
        minutes = (total - completed) / rate / 60
        print(f'Estimated rate {rate / 1e6:.1f} MB/s; download ETA approximately {minutes / 60:.1f} hours ({minutes:.0f} minutes).')
    else:
        print('ETA unavailable until download progress is recorded.')

if __name__ == '__main__':
    main()
