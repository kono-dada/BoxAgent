const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { resolvePaths } = require('../startup/paths.cjs');
const { CredentialStore } = require('../startup/credentials.cjs');
const { readState, atomicWrite } = require('../startup/store.cjs');
const { StartupCoordinator } = require('../startup/coordinator.cjs');
const { validateProviders } = require('../startup/providers.cjs');

const encryption = { isEncryptionAvailable: () => true, encryptString: value => Buffer.from(value.split('').reverse().join('')),
  decryptString: value => value.toString().split('').reverse().join('') };
async function fixture(t) {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-setup-'));
  t.after(() => fs.rm(data, { recursive: true, force: true }));
  return { data, credentials: new CredentialStore(path.join(data, 'credentials.json'), encryption) };
}
test('published paths do not depend on source or inherit .runtime user storage', () => {
  const paths = resolvePaths({ packaged: true, resources: '/Applications/Box Agent.app/Contents/Resources', appData: '/Users/example/Library/Application Support', env: {} });
  assert.equal(paths.data, '/Users/example/Library/Application Support/BoxAgent');
  assert.equal(paths.python, '/Users/example/Library/Application Support/BoxAgent/runtimes/core/bin/python3');
  assert.equal(paths.root, '/Applications/Box Agent.app/Contents/Resources/boxagent');
});
test('keys are encrypted, never exposed by summary, replaceable and deletable', async t => {
  const { credentials } = await fixture(t);
  await credentials.save({ DASHSCOPE_API_KEY: 'sk-original-secret', BOXAGENT_DASHSCOPE_WORKSPACE_ID: 'test-workspace' });
  const raw = await fs.readFile(credentials.file, 'utf8');
  assert.ok(!raw.includes('sk-original-secret'));
  assert.ok(!JSON.stringify(await credentials.summary()).includes('sk-original-secret'));
  assert.equal((await credentials.load()).DASHSCOPE_API_KEY, 'sk-original-secret');
  await credentials.save({ DASHSCOPE_API_KEY: 'sk-replacement' });
  assert.equal((await credentials.load()).DASHSCOPE_API_KEY, 'sk-replacement');
  await credentials.save({ DASHSCOPE_API_KEY: '' });
  assert.equal((await credentials.summary()).dashscope, false);
});
test('locked storage and corrupt state preserve existing files', async t => {
  const { data, credentials } = await fixture(t);
  await credentials.save({ DASHSCOPE_API_KEY: 'sk-key' });
  const before = await fs.readFile(credentials.file, 'utf8');
  const locked = new CredentialStore(credentials.file, { ...encryption, isEncryptionAvailable: () => false });
  await assert.rejects(locked.save({ DASHSCOPE_API_KEY: '' }), /credential_store_locked/);
  assert.equal(await fs.readFile(credentials.file, 'utf8'), before);
  const state = path.join(data, 'onboarding.json');
  await fs.writeFile(state, '{broken');
  await assert.rejects(readState(state), /configuration_unreadable/);
  assert.equal(await fs.readFile(state, 'utf8'), '{broken');
});
test('concurrent key updates are serialized and invalid workspace is rejected', async t => {
  const { credentials } = await fixture(t);
  await Promise.all([credentials.save({ DASHSCOPE_API_KEY: 'qwen' }), credentials.save({ DEEPSEEK_API_KEY: 'deepseek' })]);
  assert.equal((await credentials.load()).DASHSCOPE_API_KEY, 'qwen');
  await assert.rejects(credentials.save({ BOXAGENT_DASHSCOPE_WORKSPACE_ID: 'wrong/path' }), /invalid_workspace/);
  assert.equal((await credentials.load()).DEEPSEEK_API_KEY, 'deepseek');
});
test('real provider response structure is required; errors never leak keys or body', async () => {
  const config = { DASHSCOPE_API_KEY: 'sk-secret', BOXAGENT_DASHSCOPE_WORKSPACE_ID: 'test', DEEPSEEK_API_KEY: 'sk-task' };
  const result = await validateProviders(config, { fetcher: async () => ({ ok: false, status: 401, json: async () => ({ message: 'sk-secret' }) }) });
  assert.equal(result.qwen.state, 'invalid');
  assert.ok(!JSON.stringify(result).includes('sk-secret'));
  const malformed = await validateProviders(config, { fetcher: async () => ({ ok: true, json: async () => ({}) }) });
  assert.notEqual(malformed.qwen.state, 'ready');
  const valid = await validateProviders(config, { fetcher: async url => ({ ok: true, json: async () => url.includes('webrtc') ? { aoqTokenForClient: 'temporary', sid: 'session' } : { data: [] } }) });
  assert.equal(valid.qwen.state, 'ready');
  assert.ok(!JSON.stringify(valid).includes('temporary'));
});
test('Engine failure leaves configuration operations available and blocks completion', async t => {
  const { data, credentials } = await fixture(t);
  await credentials.save({ DASHSCOPE_API_KEY: 'key', BOXAGENT_DASHSCOPE_WORKSPACE_ID: 'test' });
  const ready = () => ({ state: 'ready', code: '' });
  const coordinator = new StartupCoordinator({ paths: { data, root: data, packaged: true }, credentials,
    permissions: { snapshot: () => ({ microphone: 'denied', screen: 'denied', accessibility: false }) },
    runtime: { localStatus: async () => ({ core: ready(), memory: ready(), vision: ready() }), environment: value => value },
    ensureEngine: async () => { throw Error('broken Engine'); }, stopEngine: async () => {},
    enterDesktop: async () => assert.fail('Desktop must not open'), broadcast: () => {},
    validate: async () => ({ qwen: ready() }) });
  await coordinator.start();
  assert.equal(coordinator.snapshot().mode, 'needs_setup');
  assert.equal(coordinator.snapshot().capabilities.engine.state, 'unavailable');
  const result = await coordinator.request('complete');
  assert.equal(result.onboarding.completed, false);
  assert.equal(result.error, 'capabilities_not_ready');
  assert.equal((await coordinator.request('save', { DASHSCOPE_API_KEY: 'new-key' })).credentials.dashscope, true);
});
test('completion rechecks readiness and returning-user repair preserves completed', async t => {
  const { data, credentials } = await fixture(t);
  await credentials.save({ DASHSCOPE_API_KEY: 'key', BOXAGENT_DASHSCOPE_WORKSPACE_ID: 'test' });
  let healthy = true, opened = 0;
  const coordinator = new StartupCoordinator({ paths: { data, root: data, packaged: true }, credentials,
    permissions: { snapshot: () => ({}) }, runtime: {
      localStatus: async () => Object.fromEntries(['core', 'memory', 'vision'].map(name => [name, { state: healthy ? 'ready' : 'missing', code: '' }])),
      environment: value => value }, ensureEngine: async () => {}, stopEngine: async () => {},
    enterDesktop: async () => { opened++; }, broadcast: () => {}, validate: async () => ({ qwen: { state: 'ready', code: '' } }) });
  await coordinator.start();
  const complete = await coordinator.request('complete');
  assert.equal(complete.onboarding.completed, true);
  assert.equal(opened, 1);
  healthy = false;
  const repair = await coordinator.request('repair');
  assert.equal(repair.onboarding.completed, true);
  assert.equal((await coordinator.request('complete')).error, 'capabilities_not_ready');
  assert.equal((await readState(coordinator.stateFile)).completed, true);
});
test('flow state is validated and persisted without capabilities or credentials', async t => {
  const { data } = await fixture(t);
  const file = path.join(data, 'state.json');
  await atomicWrite(file, { version: 1, completed: false, step: 'providers', completedAt: null });
  assert.equal((await readState(file)).step, 'providers');
  assert.deepEqual(Object.keys(await readState(file)).sort(), ['version', 'completed', 'step', 'completedAt'].sort());
});
test('cancelled subprocess is reaped even when it handles termination with a zero exit', async () => {
  const { run } = require('../startup/process.cjs');
  const controller = new AbortController();
  const operation = run(process.execPath, ['-e', "process.on('SIGTERM', () => process.exit(0)); console.error('ready'); setInterval(() => {}, 1000)"],
    { signal: controller.signal, progress: () => controller.abort() });
  await assert.rejects(operation, /cancelled/);
});

test('returning users start after core/Engine checks without reloading large models', async t => {
  const { data, credentials } = await fixture(t);
  const checked = [];
  let opened = 0;
  const coordinator = new StartupCoordinator({ paths: { data, root: data, packaged: true }, credentials,
    permissions: { snapshot: () => ({}) }, runtime: {
      localStatus: async (_config, _signal, components) => {
        checked.push(...components);
        return { core: { state: 'ready', code: '' } };
      }, environment: value => value }, ensureEngine: async () => {}, stopEngine: async () => {},
    enterDesktop: async () => { opened++; }, broadcast: () => {} });
  await atomicWrite(coordinator.stateFile, { version: 1, completed: true, step: 'ready', completedAt: new Date().toISOString() });
  const snapshot = await coordinator.start();
  assert.deepEqual(checked, ['core']);
  assert.equal(opened, 1);
  assert.equal(snapshot.capabilities.engine.state, 'ready');
  assert.equal(snapshot.canComplete, false, 'Must not fabricate model readiness');
});

test('subprocess output limit waits for forced termination before rejecting', async () => {
  const { run } = require('../startup/process.cjs');
  const started = Date.now();
  await assert.rejects(run(process.execPath, ['-e', "process.on('SIGTERM', () => {}); process.stdout.write('x'.repeat(1100000)); setInterval(() => {}, 1000)"]), /process_output_limit/);
  assert.ok(Date.now() - started >= 1900, 'Resolved before reaping the child that ignored SIGTERM');
});
