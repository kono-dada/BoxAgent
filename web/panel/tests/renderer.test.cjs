const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Independent recorded response shapes: unified events and legacy product events
// expose different type/content fields. Exercise decoding without duplicating UI.
function renderer() {
  const handlers = {};
  const node = { addEventListener() {}, dataset: {}, style: {} };
  const context = vm.createContext({
    document: { title:'', getElementById: () => node, querySelectorAll: () => [], addEventListener() {} },
    boxagent: { onState() {}, onDiagnostic() {}, request: async () => new Promise(() => {}) },
    console, Intl, Date, setTimeout, t: value => value, locale: () => 'zh-CN', startPreferences() {}
  });
  vm.runInContext(fs.readFileSync(require.resolve('../renderer.js'), 'utf8').replace(/^import .*;\n/gm, ''), context);
  handlers.event = row => JSON.parse(vm.runInContext(`JSON.stringify(normalizeEvent(${JSON.stringify(row)}))`, context));
  return handlers;
}

test('unified final messages retain their readable role and content', () => {
  const result = renderer().event({ seq: 23, type: 'assistant/message', time: 172800, data: { product_event: {
    sequence: 10, type: 'message.final', role: 'assistant', content: '记住了，下次陪你喝红茶。', occurred_at: 172799, interaction_id: 'int_tea'
  } } });
  assert.equal(result.label, '伙伴回复'); assert.equal(result.preview, '记住了，下次陪你喝红茶。');
  assert.equal(result.seq, 23); assert.equal(result.query, 'int_tea');
});
test('legacy product messages keep top-level content even with nested metadata', () => {
  const result = renderer().event({ sequence: 4, type: 'message.final', role: 'user', content: '今天有些难受。', occurred_at: 172800, data: { channel: 'text' } });
  assert.equal(result.label, '你说'); assert.equal(result.preview, '今天有些难受。');
});
test('model-visible messages remain context items rather than duplicate formal replies', () => {
  const result = renderer().event({ seq: 19, type: 'context/item', time: 172800, data: { ledger_type: 'assistant/message', category: 'assistant', content: '  我在这里。\n' } });
  assert.equal(result.type, 'context/item'); assert.equal(result.label, '助手回复'); assert.equal(result.preview, '我在这里。');
});
test('query-scoped reference and task records retain association and summary', () => {
  const decode = renderer().event;
  const reference = decode({ seq: 16, type: 'context/item', time: 172800, data: { category: 'reference', content: '当前 Query 的记忆参考', query_id: 'qry_current' } });
  assert.equal(reference.label, '本轮参考包'); assert.equal(reference.query, 'qry_current');
  const task = decode({ seq: 20, type: 'task.completed', time: 172800, data: { product_event: { type: 'task.completed', data: { task_text: '文档已保存' } } } });
  assert.equal(task.label, '任务完成'); assert.equal(task.preview, '文档已保存');
});
