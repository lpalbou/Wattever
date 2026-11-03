"""Read GGUF metadata/tensor shapes without loading weights or creating CUDA contexts.

python tools/inspect_gguf.py model.gguf --output inventory.json
The public output contains the file basename, not the private directory.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import struct

SCALARS={0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'B',10:'Q',11:'q',12:'d'}

class Reader:
    def __init__(self,stream):self.stream=stream
    def number(self,kind):
        fmt='<'+SCALARS[kind];size=struct.calcsize(fmt);raw=self.stream.read(size)
        if len(raw)!=size:raise ValueError('Truncated GGUF metadata')
        return struct.unpack(fmt,raw)[0]
    def string(self,keep=True):
        size=self.number(10)
        if size>1024**3:raise ValueError('Unbounded GGUF string')
        if not keep:self.stream.seek(size,1);return None
        raw=self.stream.read(size)
        if len(raw)!=size:raise ValueError('Truncated GGUF string')
        return raw.decode('utf-8',errors='replace')
    def value(self,kind,keep=True):
        if kind in SCALARS:return self.number(kind)
        if kind==8:return self.string(keep)
        if kind==9:
            element=self.number(4);count=self.number(10)
            if count>10**7:raise ValueError('Unbounded GGUF array')
            if not keep and element in SCALARS:
                self.stream.seek(count*struct.calcsize('<'+SCALARS[element]),1)
                return {'count':count,'element_type':element,'values_omitted':True}
            if not keep:
                for _ in range(count):self.value(element,False)
                return {'count':count,'element_type':element,'values_omitted':True}
            if count>2048:raise ValueError('Selected metadata array too large')
            return [self.value(element) for _ in range(count)]
        raise ValueError('Unsupported GGUF metadata type '+str(kind))

def inspect(path):
    path=Path(path);metadata={};tensors=[]
    with path.open('rb') as stream:
        reader=Reader(stream)
        if stream.read(4)!=b'GGUF':raise ValueError('Not a GGUF file')
        version=reader.number(4)
        if version not in (2,3):raise ValueError('Unsupported GGUF version')
        tensor_count,metadata_count=reader.number(10),reader.number(10)
        if tensor_count>10**6 or metadata_count>10**6:raise ValueError('Unbounded GGUF header')
        for _ in range(metadata_count):
            key=reader.string();kind=reader.number(4)
            keep=(not key.startswith('tokenizer.') and kind!=9) or (
                key.startswith(('qwen','gemma','lfm','llama')) and not key.endswith('chat_template'))
            value=reader.value(kind,keep)
            if keep or kind==9:metadata[key]=value
        for _ in range(tensor_count):
            name=reader.string();dimensions=reader.number(4)
            if not 1<=dimensions<=8:raise ValueError('Invalid tensor dimensions')
            shape=[reader.number(10) for _ in range(dimensions)]
            tensors.append({'name':name,'shape':shape,'parameter_count':math.prod(shape),
                            'ggml_type_id':reader.number(4),'data_offset':reader.number(10)})
        header_bytes=stream.tell();stream.seek(0)
        header_sha=hashlib.sha256(stream.read(header_bytes)).hexdigest()
    groups=collections.defaultdict(lambda:{'tensor_count':0,'parameter_count':0})
    for tensor in tensors:
        group=groups[str(tensor['ggml_type_id'])]
        group['tensor_count']+=1;group['parameter_count']+=tensor['parameter_count']
    return {'schema_version':1,'scope':'GGUF metadata and tensor directory only; weights not read',
            'file_name':path.name,'file_bytes':path.stat().st_size,'gguf_version':version,
            'header_bytes':header_bytes,'header_sha256':header_sha,'full_file_sha256':None,
            'metadata':metadata,'tensor_count':tensor_count,
            'parameter_count_from_tensor_shapes':sum(t['parameter_count'] for t in tensors),
            'parameter_count_scope':'All tensor elements, including non-trainable arrays where present; not active parameters per token',
            'ggml_type_groups':dict(groups),'tensors':tensors}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model');parser.add_argument('--output')
    arguments=parser.parse_args();result=inspect(arguments.model)
    if arguments.output:Path(arguments.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    else:print(json.dumps(result,indent=2))
