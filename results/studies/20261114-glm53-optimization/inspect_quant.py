"""Read GGUF headers only; no tensor payloads, allocations or engines."""
from pathlib import Path
import collections, json, sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'experimental-maya/source-1.1.59/tools'))
from gguf_reader import GGUFFile


def inspect(paths, output):
    tensors=[];metadata={};seen=set()
    for path in paths:
        reader=GGUFFile(Path(path))
        if not metadata:metadata=reader.metadata
        for tensor in reader.tensors:
            if tensor.name in seen:raise ValueError('Duplicate tensor '+tensor.name)
            seen.add(tensor.name);size=tensor.expected_bytes()
            if size is None:raise ValueError('Unknown tensor byte geometry '+tensor.type_name)
            if reader.data_start+tensor.offset+size>Path(path).stat().st_size:
                raise ValueError('Tensor exceeds its complete shard: '+tensor.name)
            tensors.append({'name':tensor.name,'bytes':size,'type':tensor.type_name,'elements':tensor.elements})
    arch=metadata['general.architecture']
    if arch not in ('glm5-next','glm5next') or metadata.get(arch+'.block_count')!=46 or metadata.get(arch+'.expert_count')!=288:
        raise ValueError('Candidate is not the expected GLM-5.3-Flash architecture.')
    bytype=collections.Counter()
    for tensor in tensors:bytype[tensor['type']]+=tensor['bytes']
    total=sum(x['bytes'] for x in tensors)
    expert=sum(x['bytes'] for x in tensors if '_exps.' in x['name'])
    result={'header_only':True,'files':[str(p) for p in paths],
        'metadata':{k:v for k,v in metadata.items() if not isinstance(v,(list,dict))},
        'tensors':tensors,'bytes_by_dtype':dict(bytype),'expert_bytes':expert,
        'total_tensor_bytes':total,'non_expert_bytes':total-expert,
        'parameters':sum(x['elements'] for x in tensors)}
    Path(output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--files',nargs='+',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    d=inspect(a.files,a.output)
    print(json.dumps({k:d[k] for k in ('parameters','total_tensor_bytes','expert_bytes','bytes_by_dtype')}))
