const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const table = {innerHTML:''};
const context = {Charts:{fmtNum:String},document:{querySelector:s=>s==='#live-table' ? table : null}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, ''), context);
vm.runInContext("state.liveResults=[{model:'m',status:'ok',concurrency:1,prompt_tokens:100,completion_tokens:10,wall_s:2}];renderLiveTable()", context);
const header = table.innerHTML.match(/<thead>[\s\S]*?<\/thead>/)[0];
const body = table.innerHTML.match(/<tbody>[\s\S]*?<\/tbody>/)[0];
assert.equal((header.match(/<th(?:\s|>)/g)||[]).length,(body.match(/<td(?:\s|>)/g)||[]).length,
  'Every live result value must have the corresponding header');
console.log('Live table columns and headings align');
