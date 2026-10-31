const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const M=require('../web/model-metadata.js');
for(const [id,quant] of [
 ['qwen3.8-flash-next-unsloth-ud-iq4_xs','UD-IQ4_XS'],
 ['qwen3.8-flash-next-iq3_s','IQ3_S'],
 ['qwen3.6-35b-a3b-ud-q4_k_xl','UD-Q4_K_XL'],
 ['org/model-Q4_K_M.gguf','Q4_K_M'],
 ['org/model-Q4_K_M-00001-of-00003.gguf','Q4_K_M'],
 ['qwen3.8-27b@iq4_xs','IQ4_XS'],
 ['org/model-MXFP4','MXFP4'],['org/model-bf16','BF16'],
]){
 const model=M.normalize({id});assert.equal(model.quantization,quant,id);assert.equal(model.quantization_source,'model_identifier');
}
const explicit=M.normalize({id:'org/model-Q4_K_M',quantization:'Q8_0',max_context_length:131072,loaded_context_length:32768,arch:'qwen3next'});
assert.equal(explicit.quantization,'Q8_0');assert.equal(explicit.quantization_source,'endpoint_reported');assert.equal(explicit.loaded_context_length,32768);assert.equal(explicit.max_context_length,131072);
const strata=M.normalize({id:'qwen3.8-flash-next-unsloth-ud-iq4_xs',meta:{n_ctx:32768},architecture:{input_modalities:['text']}});
assert.equal(strata.max_context_length,32768);assert.equal(strata.loaded_context_length,32768);assert.equal(strata.arch,null);
for(const id of ['org/model','org/Q4_K_M-experiment','org/model-80B','org/model-q4_k_m_backup'])assert.equal(M.normalize({id}).quantization,null);
for(const context of [0,-1,true,'32768',NaN,Infinity,1e20])assert.equal(M.normalize({id:'m',max_context_length:context}).max_context_length,null);
assert.equal(M.normalize({id:'m',quantization:'<img src=x>',arch:'C:\\private\\weights'}).quantization,null);
assert.equal(M.normalize({id:'m',meta:{quantization:'NVFP4'}}).quantization,'NVFP4');
assert.equal(M.normalize({id:'m',details:{quantization:'Q4_K_M'}}).quantization_source,'endpoint_reported');
assert.equal(M.normalize({id:'gemma-4-12b-qat',quantization:{name:'Q4_0'}}).quantization_method,'QAT');
assert.equal(M.normalize({id:'alias',display_name:'LFM2.5 QAD',quantization:'Q4_0'}).quantization_method,'QAD');
assert.equal(M.normalize({id:'alias',display_name:'Qwen UD',quantization:'Q3_K_XL'}).quantization_method,'UD');
assert.equal(M.normalize({id:'alias',quantization:'Q4_0'}).quantization_method,null);
assert.equal(M.normalize(M.normalize({id:'gemma-qat'})).quantization_method_source,'model_identifier');
assert.equal(M.normalize(M.normalize({id:'m-UD-IQ4_XS'})).quantization_source,'model_identifier');
const window={},context={window};vm.createContext(context);vm.runInContext(fs.readFileSync('web/model-metadata.js','utf8'),context);
assert.equal(window.WatteverModelMetadata.normalize({id:'m-IQ3_S'}).quantization,'IQ3_S');
console.log('Model metadata quantization provenance, canonical suffixes, context and safety tests passed');
