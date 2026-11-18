const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

function parser() {
  const exports = {};
  const source = fs.readFileSync(require.resolve('../src/MemorySourcePreview.tsx'), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(compiled, { exports, require: () => ({}) });
  return exports;
}
const fixture = { status: 'succeeded', image: 'data:image/png;base64,aGVsbG8=',
  user_request: '记住这套配队', caption: '画面显示配队', captured_at: 1234 };

test('memory source accepts only bounded local PNG data and valid timestamps', () => {
  const parse = parser().parseMemorySource;
  assert.equal(parse(fixture).caption, fixture.caption);
  for (const image of ['file:///private/key', 'https://example.com/image', 'data:image/svg+xml,<svg/>',
    'javascript:alert(1)', 'data:image/png;base64,' + 'a'.repeat(6 * 1024 * 1024)]) {
    assert.equal(parse({ ...fixture, image }), null);
  }
  for (const patch of [{ status: 'not_found' }, { captured_at: NaN }, { captured_at: '1234' },
    { caption: {} }, { user_request: null }]) assert.equal(parse({ ...fixture, ...patch }), null);
});

test('deleting an episode removes all IDs acknowledged by the engine', () => {
  const ids = parser().deletedMemoryIds({ deleted_ids: ['cell', 'atom', null, 42] }, 'cell');
  assert.deepEqual([...ids], ['cell', 'atom']);
  assert.deepEqual([...parser().deletedMemoryIds({}, 'legacy')], ['legacy']);
});
