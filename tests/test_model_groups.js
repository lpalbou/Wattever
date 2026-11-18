// Similar models are boxed together: the same model in several variants, then one family in several sizes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const context = {WatteverPhaseMetrics: require('../web/phase-metrics.js'), Charts: {fmtNum: String, MARKERS: ['circle'], DASHES: [null]},
  document: {documentElement: {}, querySelector: () => null, querySelectorAll: () => []}, getComputedStyle: () => ({getPropertyValue: () => '#123456'})};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, ''), context);
const base = id => JSON.parse(vm.runInContext(`JSON.stringify(modelBase(${JSON.stringify(id)}))`, context));
const groups = names => JSON.parse(vm.runInContext(`JSON.stringify(modelGroups(${JSON.stringify(names)}))`, context));
const shape = names => { const out = groups(names); return {boxes: out.groups.map(group => [group.name, group.kind, group.models]), loose: out.loose}; };

// What an ID stands for once its publisher, quantization and packaging are set aside.
for (const [id, label, family] of [
  ['Jundot--Qwen3.8-27B-oQ4e-mtp', 'Qwen3.8-27B', 'Qwen3.8'],
  ['mlx-community--Qwen3.8-27B-4bit', 'Qwen3.8-27B', 'Qwen3.8'],
  ['qwen/qwen3.8-27b', 'qwen3.8-27b', 'qwen3.8'],
  ['qwen3.8-27b@q3_k_xl', 'qwen3.8-27b', 'qwen3.8'],
  ['qwen3.8-flash-next-unsloth-ud-iq4_xs', 'qwen3.8-flash-next', 'qwen3.8-flash-next'],
  ['qwen3.6-35b-a3b-ud-q4_k_xl', 'qwen3.6-35b-a3b', 'qwen3.6'],
  ['google/gemma-4-26b-a4b-qat', 'gemma-4-26b-a4b', 'gemma-4'],
  ['google/gemma-4-e2b', 'gemma-4-e2b', 'gemma-4'],
  ['lmstudio-community/Qwen3.8-27B-GGUF', 'Qwen3.8-27B', 'Qwen3.8'],
  ['Qwen3.8-27B-Q4_K_M.gguf', 'Qwen3.8-27B', 'Qwen3.8'],
  ['qwen3.8-27b.Q8_0.gguf', 'qwen3.8-27b', 'qwen3.8'],
  ['llama3.1:8b-instruct-q4_K_M', 'llama3.1-8b-instruct', 'llama3.1-instruct'],
  ['llama3.1:8b-instruct-fp16', 'llama3.1-8b-instruct', 'llama3.1-instruct'],
  ['model-8.0bpw-exl2', 'model', 'model'],
  // Nothing that could be part of a model's own name is removed.
  ['mistral-7b-v0.3', 'mistral-7b-v0.3', 'mistral-v0.3'],
  ['spark-x2.6-4b', 'spark-x2.6-4b', 'spark-x2.6'],
  ['glm-5.3-flash-maya-xs-iq1_s', 'glm-5.3-flash-maya-xs', 'glm-5.3-flash-maya-xs'],
  ['qwen3.8-27b-instruct', 'qwen3.8-27b-instruct', 'qwen3.8-instruct'],
  ['phi-4-mini', 'phi-4-mini', 'phi-4-mini'],
  ['gguf', 'gguf', 'gguf'],
  ['8b', '8b', '8b'],
]) assert.deepEqual(base(id), {label, family}, id);

// The model list from a real library of 21 models.
const library = ['Jundot--Qwen3.8-27B-oQ4e-mtp', 'Qwen3.8-Flash-Next-oQ3e-mtp', 'glm-5.3-flash-maya-xs-iq1_s', 'google/gemma-4-12b-qat',
  'google/gemma-4-26b-a4b-qat', 'google/gemma-4-e2b', 'google/gemma-4-e4b', 'lfm2.5-24b-a2b', 'lfm3-omni-2b', 'mlx-community--Qwen3.6-35B-A3B-4bit',
  'mlx-community--Qwen3.8-27B-4bit', 'openai/gpt-oss-20b', 'prism-ml/bonsai-27b', 'qwen/qwen3.5-9b', 'qwen/qwen3.6-35b-a3b', 'qwen/qwen3.8-27b',
  'qwen3.6-35b-a3b-ud-q4_k_xl', 'qwen3.8-27b@q3_k_xl', 'qwen3.8-flash-next-iq3_s', 'qwen3.8-flash-next-unsloth-ud-iq4_xs', 'spark-x2.6-4b'];
assert.deepEqual(shape(library), {
  boxes: [
    ['Qwen3.8-27B', 'variants', ['Jundot--Qwen3.8-27B-oQ4e-mtp', 'mlx-community--Qwen3.8-27B-4bit', 'qwen/qwen3.8-27b', 'qwen3.8-27b@q3_k_xl']],
    ['Qwen3.8-Flash-Next', 'variants', ['Qwen3.8-Flash-Next-oQ3e-mtp', 'qwen3.8-flash-next-iq3_s', 'qwen3.8-flash-next-unsloth-ud-iq4_xs']],
    ['gemma-4', 'sizes', ['google/gemma-4-12b-qat', 'google/gemma-4-26b-a4b-qat', 'google/gemma-4-e2b', 'google/gemma-4-e4b']],
    ['Qwen3.6-35B-A3B', 'variants', ['mlx-community--Qwen3.6-35B-A3B-4bit', 'qwen/qwen3.6-35b-a3b', 'qwen3.6-35b-a3b-ud-q4_k_xl']],
  ],
  loose: ['glm-5.3-flash-maya-xs-iq1_s', 'lfm2.5-24b-a2b', 'lfm3-omni-2b', 'openai/gpt-oss-20b', 'prism-ml/bonsai-27b', 'qwen/qwen3.5-9b', 'spark-x2.6-4b'],
}, 'boxes come in the order their first model is listed; a model with no sibling stays loose');
const everyone = groups(library);
assert.equal(everyone.groups.flatMap(group => group.models).length + everyone.loose.length, library.length, 'every model appears exactly once');

// Unrelated models are left exactly as they were.
assert.deepEqual(shape(['model-a', 'model-b', 'model-c']), {boxes: [], loose: ['model-a', 'model-b', 'model-c']});
assert.deepEqual(shape([]), {boxes: [], loose: []});
assert.deepEqual(shape(['only-one-7b']), {boxes: [], loose: ['only-one-7b']});

// A different fine-tune is a different model, not a variant.
assert.deepEqual(shape(['qwen3.8-27b', 'qwen3.8-27b-instruct']), {boxes: [], loose: ['qwen3.8-27b', 'qwen3.8-27b-instruct']});
// Sizes of one family group only among models that have no variant of their own.
assert.deepEqual(shape(['llama-3.1-70b', 'llama-3.1-8b', 'llama-3.1-8b-q4_k_m', 'mistral-7b']), {
  boxes: [['llama-3.1-8b', 'variants', ['llama-3.1-8b', 'llama-3.1-8b-q4_k_m']]],
  loose: ['llama-3.1-70b', 'mistral-7b']});
assert.deepEqual(shape(['llama-3.1-70b', 'llama-3.1-8b', 'mistral-7b']), {
  boxes: [['llama-3.1', 'sizes', ['llama-3.1-70b', 'llama-3.1-8b']]], loose: ['mistral-7b']});
// Matching ignores case; the box takes the spelling with the most capitals.
assert.deepEqual(shape(['abc/phi-4-q8_0', 'Phi-4-4bit']).boxes, [['Phi-4', 'variants', ['abc/phi-4-q8_0', 'Phi-4-4bit']]]);

console.log('model groups: ok');
