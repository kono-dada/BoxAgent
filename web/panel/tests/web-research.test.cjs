const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const { publicSourceUrl } = require('../desktop/web-links.cjs');

function model() {
  const exports = {};
  const source = fs.readFileSync(require.resolve('../src/web-research/model.ts'), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  vm.runInNewContext(compiled, { exports, URL, Map });
  return exports;
}

const view = (patch = {}) => ({ session_id: 'session', interaction_id: 'turn', stage: 'ready',
  query: '鸣潮 周本 无冠者', requested_version: '3.7', message: '资料已找到', retrieved_at: '2026-11-17T11:00:00Z',
  sources: [{ title: '公开攻略', url: 'https://wiki.example.com/guide', published_at: '2025-01-01',
    site_name: null, read_status: 'read' }], ...patch });

test('source links reject executable, local, credential and malformed targets in both renderer and main', () => {
  const { sourceUrl } = model();
  for (const value of ['javascript:alert(1)', 'file:///private/key', 'https://u:p@example.com', 'http://localhost',
    'http://10.0.0.1', 'http://2130706433', 'http://[::1]', 'https://name.internal', 'https://example.com\n']) {
    assert.equal(sourceUrl(value), null);
    assert.throws(() => publicSourceUrl(value));
  }
  assert.equal(publicSourceUrl('https://wiki.example.com/guide#section'), 'https://wiki.example.com/guide');
});

test('reference data stays with its interaction and rejects mismatched records and stale live sessions', () => {
  const { researchByInteraction } = model();
  const events = [
    { type: 'web.research.completed', session_id: 'session', interaction_id: 'turn', data: { web_research: view() } },
    { type: 'web.research.completed', interaction_id: 'other', data: { web_research: view() } },
  ];
  const result = researchByInteraction(events, { session_id: 'new-session', web_research: view({ interaction_id: 'stale' }) });
  assert.deepEqual(Object.keys(result), ['turn']);
  assert.equal(result.turn.requested_version, '3.7');
  assert.equal(result.turn.sources[0].published_at, '2025-01-01');
});

test('explicit rereading preserves article dates and source URL provenance without treating it as a spoken message', () => {
  const { researchByInteraction, lastAssistantIds } = model();
  const records = [
    { type: 'web.research.completed', interaction_id: 'turn', data: { web_research: view() } },
    { type: 'message.final', interaction_id: 'turn', role: 'assistant', event_id: 'wait', content: '我查一下。' },
    { type: 'web.research.completed', interaction_id: 'turn', data: { web_research: view({ sources: [
      { title: '原文', url: 'https://wiki.example.com/guide', published_at: null, site_name: null, read_status: 'read' },
      { title: '危险链接', url: 'javascript:alert(1)', read_status: 'read' },
    ] }) } },
    { type: 'message.final', interaction_id: 'turn', role: 'assistant', event_id: 'answer', content: '先稳住，再找时机。' },
  ];
  const result = researchByInteraction(records);
  assert.equal(result.turn.sources.length, 1);
  assert.equal(result.turn.sources[0].published_at, '2025-01-01');
  assert.equal(lastAssistantIds(records).turn, 'answer');
});
