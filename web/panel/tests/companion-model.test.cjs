const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const exportsForTest = {};
const compiled = ts.transpileModule(fs.readFileSync(require.resolve('../src/companion/model.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(compiled, { exports: exportsForTest });

test('new notifications cannot reappend a stale committed assistant reply', () => {
  const history = [
    { type: 'message.final', event_id: 'reply', role: 'assistant', content: '行，我盯着点。', interaction_id: 'turn' },
    { type: 'message.final', event_id: 'notice', role: 'assistant', content: '朋友发来了新消息。' },
  ];
  const rows = exportsForTest.transcript(history, { assistant_text: '行，我盯着点。' }, '');
  assert.equal(rows.length, 2);
  assert.equal(rows.at(-1).id, 'notice');
});
test('uncommitted streaming reply remains visible and disappears after commit', () => {
  const state = { assistant_text: '正在查询微信。' };
  assert.equal(exportsForTest.transcript([], state, '')[0].id, 'live-assistant');
  const history = [{ type: 'message.final', event_id: 'committed', role: 'assistant', content: state.assistant_text }];
  assert.equal(exportsForTest.transcript(history, state, '').length, 1);
});
