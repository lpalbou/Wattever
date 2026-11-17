import json
from pathlib import Path
folder=Path('C:/tests/flash-next/benchmark-clean/20261030T100949Z')
from datetime import datetime
from collections import defaultdict
begin=datetime.fromisoformat('2026-10-30T10:28:30+00:00').timestamp()
end=datetime.fromisoformat('2026-10-30T10:30:38+00:00').timestamp()
samples=[json.loads(line) for line in (folder/'system-samples.jsonl').read_text().splitlines()]
rows=[s for s in samples if begin<=s['time']<=end]
print('samples',len(rows),'cpu mean',sum(s['external_cpu_percent'] for s in rows)/max(1,len(rows)), 'io mean',sum(s['external_io_mb_s'] for s in rows)/max(1,len(rows)))
names=defaultdict(float)
for sample in rows:
    for proc in sample['top_external']:names[proc['name']]+=proc['cpu_percent']/max(1,len(rows))
print('contributors',dict(sorted(names.items(),key=lambda x:-x[1])))
for row in rows[-4:]:print(json.dumps(row))
import psutil
try:
    proc=psutil.Process(3492)
    print('Downloader executable:',proc.exe(),'parent:',proc.parent().name())
except psutil.Error as exc:print(type(exc).__name__)
