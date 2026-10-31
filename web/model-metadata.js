/* Model catalog labels are endpoint reports or identifier hints, not weight verification. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(typeof window!=='undefined')window.WatteverModelMetadata=api;else root.WatteverModelMetadata=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const pattern='(?:UD[-_])?(?:'+'IQ(?:1_[SM]|2_(?:XXS|XS|S|M)|3_(?:XXS|XS|S|M)|4_(?:XS|NL))|Q[1-8]_(?:[01]|K(?:_(?:S|M|L|XL))?)|MXFP4|NVFP4|BF16|F16|F32|FP16|FP32|FP8(?:_(?:E4M3(?:FN)?|E5M2))?'.replace(/_/g,'[-_]')+')';
  const quantPattern=new RegExp('^'+pattern+'$','i'), suffixPattern=new RegExp('(?:^|[-_./@])('+pattern+')$','i');
  function text(value){return typeof value==='string'&&value.length<=80&&/^[A-Za-z0-9][A-Za-z0-9_ .+()-]*$/.test(value.trim())?value.trim():null;}
  function quant(value){const clean=text(value&&typeof value==='object'?value.name:value);if(!clean||/^(unknown|none|null|n\/a)$/i.test(clean))return null;if(!quantPattern.test(clean))return clean;const normalized=clean.toUpperCase().replace(/-/g,'_');return normalized.startsWith('UD_')?'UD-'+normalized.slice(3):normalized;}
  function fromId(id){const match=String(id||'').replace(/\.gguf$/i,'').replace(/-\d{5}-of-\d{5}$/,'').match(suffixPattern);return match?quant(match[1]):null;}
  function context(...values){return values.find(value=>typeof value==='number'&&Number.isInteger(value)&&value>0&&value<=100000000)??null;}
  function normalize(model){
    const meta=model.meta&&typeof model.meta==='object'?model.meta:{},details=model.details&&typeof model.details==='object'?model.details:{};
    let value=quant(model.quantization)||quant(meta.quantization)||quant(details.quantization),source=value?(model.quantization_source==='model_identifier'?'model_identifier':'endpoint_reported'):'unknown';
    if(!value){value=fromId(model.id);if(value)source='model_identifier';}
    let method=text(model.quantization_method)||text(meta.quantization_method)||text(details.quantization_method)||text(model.quantization?.method),methodSource=method?(model.quantization_method_source||'endpoint_reported'):'unknown';
    if(!method){
      for(const key of ['display_name','displayName','filename','id']){
        const match=String(model[key]||'').match(/(?:^|[\s_./@()-])(QAT|QAD|UD|PTQ|GPTQ|AWQ)(?=$|[\s_./@()-])/i);
        if(match){method=match[1];methodSource=['id','filename'].includes(key)?'model_identifier':'endpoint_reported';break;}
      }
    }
    const loaded=context(model.loaded_context_length,meta.n_ctx,model.n_ctx),maximum=context(model.max_context_length,model.max_model_len,model.context_length,model.context_window,meta.max_context_length,meta.context_length,meta.n_ctx_train,details.context_length,details.context_window);
    const arch=text(model.arch)||text(model.architecture);
    return{...model,id:model.id,type:model.type||'llm',quantization:value,quantization_source:source,quantization_method:method?method.toUpperCase():null,quantization_method_source:methodSource,max_context_length:maximum||loaded,loaded_context_length:loaded,arch};
  }
  return{normalize,fromId};
});
