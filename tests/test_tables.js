const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/tables.js', 'utf8') + '\nglobalThis.tableHelper = Tables;', context);
const rows = [
  {cells: [{textContent: 'Model'}, {textContent: 'Speed\n(tk/s)'}]},
  {cells: [{innerText: 'qwen/model'}, {innerText: '100.5'}]},
  {cells: [{textContent: 'gemma/model'}, {textContent: '  load   failed  '}]},
];
const table = {querySelectorAll(selector) {
  assert.equal(selector, 'thead tr, tbody tr');
  return rows;
}};
assert.equal(context.tableHelper.toTSV(table), 'Model\tSpeed (tk/s)\nqwen/model\t100.5\ngemma/model\tload failed');
assert.equal(context.tableHelper.toTSV({querySelectorAll: () => []}), '');
console.log('Shared table clipboard tests passed');
