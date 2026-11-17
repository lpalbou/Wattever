import json
from pathlib import Path
p=Path('C:/tests/flash-next/benchmark-clean/20261030T100949Z')
for line in (p/'system-samples.jsonl').read_text().splitlines()[-3:]:
    row=json.loads(line)
    print({k:row[k] for k in ('time','external_cpu_percent','external_io_mb_s','top_external')})
