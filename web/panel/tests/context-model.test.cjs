const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const model = {};
const compiled = ts.transpileModule(fs.readFileSync(require.resolve('../src/context-model.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(compiled, { exports: model });
const framesOf = (state, timeline) => JSON.parse(JSON.stringify(model.buildFrames(state, timeline)));
const entry = (seq, category, tokens) => ({ seq, category, tokens, label: category, preview: 'content' });
const request = (seq, entries, added, removed) => ({ seq, time: 1000 + seq, entries, added, removed });

test('historical requests restore an expired reference without injecting current messages', () => {
  const current = [entry(1, 'instructions', 10), entry(6, 'user', 3), entry(9, 'reference', 5)];
  const frames = framesOf({ entries: current, provider_entries: current, totals: { tokens: 18 }, requests: [request(5, [1, 3], [1, 3], []), request(10, [1, 6, 9], [6, 9], [3])] }, [
    { seq: 3, type: 'context/item', time: 1000, data: { category: 'reference', tokens: 7, content: 'old query only' } },
    { seq: 4, type: 'assistant/message', time: 1000, data: { product_event: { content: 'formal reply is not context' } } }
  ]);
  assert.deepEqual(frames[0].entries.map(row => row.seq), [1, 3]);
  assert.equal(frames[0].tokens, 17); assert.equal(frames[1].tokens, 18);
  assert.equal(frames[1].removed[0], 3); assert.equal(frames[1].entries.some(row => row.seq === 3), false);
  assert.equal(frames.at(-1).key, 'live');
});
test('missing historical entries stay explicit rather than treated as known zero cost', () => {
  const frames = framesOf({ entries: [entry(7, 'user', 2)], provider_entries: [], totals: { tokens: 2 }, requests: [request(6, [2, 3], [2, 3], [])] }, []);
  assert.equal(frames[0].missing, 2); assert.equal(frames[0].entries[0].category, 'unknown');
  assert.equal(frames.at(-1).missing, 0);
});
test('category differences reflect reference removal while keeping formal history', () => {
  const frame = { composition: [{ key: 'user', count: 2, tokens: 6 }] };
  const previous = { composition: [{ key: 'user', count: 1, tokens: 3 }, { key: 'reference', count: 1, tokens: 50 }] };
  assert.deepEqual(JSON.parse(JSON.stringify(model.categoryDelta(frame, previous, 'reference'))), { items: -1, tokens: -50 });
  assert.deepEqual(JSON.parse(JSON.stringify(model.categoryDelta(frame, previous, 'user'))), { items: 1, tokens: 3 });
});
