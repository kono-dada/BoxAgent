const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { CredentialStore } = require('../startup/credentials.cjs');
const { WeChatSetup, validateArchive } = require('../startup/wechat.cjs');
const { StartupCoordinator, canComplete } = require('../startup/coordinator.cjs');
const { PackagedRuntime } = require('../startup/runtime.cjs');
const ready = () => ({ state: 'ready', code: '' });
const missing = () => ({ state: 'missing', code: 'wechat_keys_missing' });
async function fixture(t) {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-wechat-setup-'));
  t.after(() => fs.rm(data, { recursive: true, force: true }));
  const encryption = { isEncryptionAvailable: () => true, encryptString: value => Buffer.from(value.split('').reverse().join('')),
    decryptString: value => value.toString().split('').reverse().join('') };
  const credentials = new CredentialStore(path.join(data, 'credentials.json'), encryption);
  const paths = { root: data, data, runtime: data, resources: data, python: '/fixture/python', packaged: true };
  const material = { 'session/session.db': 'a1'.repeat(32), 'contact/contact.db': 'b2'.repeat(32), 'message/message_0.db': 'c3'.repeat(32) };
  return { paths, credentials, material };
}
test('captured keys travel only over stdin/stdout and encrypted storage, never the UI summary', async t => {
  const { paths, credentials, material } = await fixture(t);
  const calls = [];
  const setup = new WeChatSetup(paths, credentials, { execute: async (python, args, options) => {
    calls.push({ python, args, options });
    return JSON.stringify({ ok: true, result: { root: '/fixture/wxid_self_abcd/db_storage', material,
      reading: ready(), listening: ready() } });
  } });
  await setup.capture(new AbortController().signal);
  const secret = material['session/session.db'];
  assert.ok(!JSON.stringify(calls[0].args).includes(secret));
  assert.ok(!JSON.stringify(setup.snapshot()).includes(secret));
  assert.ok(!JSON.stringify(await credentials.summary()).includes(secret));
  assert.ok(!(await fs.readFile(credentials.file, 'utf8')).includes(secret));
  assert.equal((await credentials.load()).BOXAGENT_WECHAT_ENABLED, undefined, 'Capture does not consent to cloud message processing');
  await setup.verify(new AbortController().signal);
  assert.equal(JSON.parse(calls[1].options.input).material['session/session.db'], secret);
});
test('cancelled capture preserves previous account and credentials', async t => {
  const { paths, credentials, material } = await fixture(t);
  await credentials.save({ BOXAGENT_WECHAT_DB_ROOT: '/old/account', BOXAGENT_WECHAT_KEY_MATERIAL: JSON.stringify({ keys: material }) });
  const before = await fs.readFile(credentials.file, 'utf8');
  const controller = new AbortController();
  const setup = new WeChatSetup(paths, credentials, { execute: async () => {
    controller.abort(); return JSON.stringify({ ok: true, result: { root: '/new/account', material } });
  } });
  await assert.rejects(setup.capture(controller.signal));
  assert.equal(await fs.readFile(credentials.file, 'utf8'), before);
});
test('untrusted subprocess error text is never exposed to renderer', async t => {
  const { paths, credentials } = await fixture(t);
  const setup = new WeChatSetup(paths, credentials, { execute: async () => JSON.stringify({ ok: false, code: 'secret chat or key' }) });
  await assert.rejects(setup.capture(new AbortController().signal), /^Error: wechat_setup_failed$/);
  assert.ok(!JSON.stringify(setup.snapshot()).includes('secret'));
});
test('unpublished, mutable, and wrong-build archives cannot bypass the download gate', () => {
  const base = { version: 1, clientVersion: '4.1.15', build: '270115', url: null, sha256: null, size: null };
  assert.equal(validateArchive(base), false);
  assert.throws(() => validateArchive({ ...base, url: 'https://example.com/latest.dmg' }));
  assert.throws(() => validateArchive({ ...base, build: '270135' }));
  assert.equal(validateArchive({ ...base, url: 'https://example.com/pinned.dmg', sha256: 'ab'.repeat(32), size: 2048 }), true);
  assert.equal(validateArchive({ ...base, bundledPath: 'wechat/4.1.15.270115.dmg', sha256: 'ab'.repeat(32), size: 2048 }), true);
  assert.throws(() => validateArchive({ ...base, bundledPath: '../../other.dmg', url: 'https://example.com/file', sha256: 'ab'.repeat(32), size: 2048 }));
});
test('an enabled connector blocks completion until real read and listen checks pass', () => {
  const snapshot = { credentials: { dashscope: true, deepseek: true, workspace: 'test', provider: 'deepseek', wechatEnabled: true },
    capabilities: Object.fromEntries(['core', 'memory', 'vision', 'engine', 'qwen', 'task'].map(name => [name, ready()])) };
  assert.equal(canComplete(snapshot), false);
  assert.equal(canComplete({ ...snapshot, wechat: { reading: ready(), listening: missing() } }), false);
  assert.equal(canComplete({ ...snapshot, wechat: { reading: ready(), listening: ready() } }), true);
});
test('renderer cannot write WeChat credential fields through general provider settings', async t => {
  const { paths, credentials } = await fixture(t);
  const coordinator = new StartupCoordinator({ paths, credentials, permissions: { snapshot: () => ({}) }, broadcast: () => {},
    runtime: {}, stopEngine: async () => assert.fail('Must reject before stopping') });
  const result = await coordinator.request('save', { BOXAGENT_WECHAT_KEY_MATERIAL: 'injected' });
  assert.equal(result.error, 'invalid_configuration');
  assert.equal((await credentials.load()).BOXAGENT_WECHAT_KEY_MATERIAL, undefined);
});
test('packaged environment does not inherit developer WeChat keys or auto-enable it', async t => {
  const { paths } = await fixture(t);
  const previous = process.env.BOXAGENT_WECHAT_KEY_MATERIAL;
  process.env.BOXAGENT_WECHAT_KEY_MATERIAL = 'developer-only';
  try {
    const environment = new PackagedRuntime(paths).environment({});
    assert.equal(environment.BOXAGENT_WECHAT_KEY_MATERIAL, undefined);
    assert.equal(environment.BOXAGENT_WECHAT_ENABLED, '0');
  } finally {
    if (previous === undefined) delete process.env.BOXAGENT_WECHAT_KEY_MATERIAL;
    else process.env.BOXAGENT_WECHAT_KEY_MATERIAL = previous;
  }
});
test('missing archive is explicit and never starts a download', async t => {
  const { paths, credentials } = await fixture(t);
  await fs.mkdir(path.join(paths.root, 'scripts/wechat-clients'), { recursive: true });
  await fs.writeFile(path.join(paths.root, 'scripts/wechat-clients/4.1.15.270115.json'), JSON.stringify({
    version: 1, clientVersion: '4.1.15', build: '270115', url: null, sha256: null, size: null }));
  const setup = new WeChatSetup(paths, credentials, { fetcher: async () => assert.fail('No unverified download') });
  await assert.rejects(setup.download(new AbortController().signal), /wechat_archive_unavailable/);
});
test('a failed or cancelled optional WeChat setup restores the core service without enabling the connector', async t => {
  const { paths, credentials } = await fixture(t);
  let started = 0, stopped = 0;
  const coordinator = new StartupCoordinator({ paths, credentials, permissions: { snapshot: () => ({}) }, broadcast: () => {},
    runtime: { localStatus: async () => ({ core: ready() }), environment: value => value },
    ensureEngine: async () => { started++; }, stopEngine: async () => { stopped++; },
    wechat: { snapshot: () => ({ reading: missing(), listening: missing() }),
      capture: async () => { throw Error('wechat_capture_timeout'); } } });
  const result = await coordinator.request('wechat-capture');
  assert.equal(result.error, 'wechat_capture_timeout');
  assert.equal(result.capabilities.engine.state, 'ready');
  assert.equal(started, 1); assert.equal(stopped, 1);
  assert.equal((await credentials.summary()).wechatEnabled, false);
});
test('verification failure does not enable message processing', async t => {
  const { paths, credentials } = await fixture(t);
  const coordinator = new StartupCoordinator({ paths, credentials, permissions: { snapshot: () => ({}) }, broadcast: () => {},
    runtime: { localStatus: async () => ({ core: ready() }), environment: value => value },
    ensureEngine: async () => {}, stopEngine: async () => {},
    wechat: { snapshot: () => ({ reading: ready(), listening: missing() }), verify: async () => {} } });
  const result = await coordinator.request('wechat-enable');
  assert.equal(result.error, 'wechat_validation_failed');
  assert.equal((await credentials.summary()).wechatEnabled, false);
});
test('tampered bundled client is rejected before mounting or running any program', async t => {
  const { paths, credentials } = await fixture(t);
  await fs.mkdir(path.join(paths.resources, 'wechat'), { recursive: true });
  await fs.writeFile(path.join(paths.resources, 'wechat/4.1.15.270115.dmg'), Buffer.alloc(2048));
  await fs.writeFile(path.join(paths.resources, 'wechat/manifest.json'), JSON.stringify({
    version: 1, clientVersion: '4.1.15', build: '270115', bundledPath: 'wechat/4.1.15.270115.dmg',
    url: null, sha256: 'ab'.repeat(32), size: 2048 }));
  const setup = new WeChatSetup(paths, credentials, { execute: async () => assert.fail('Invalid archive must not be mounted') });
  await assert.rejects(setup.provision(new AbortController().signal), /wechat_download_invalid/);
});

test('WeChat setup rechecks the runtime and reports repair before any client operation', async t => {
  const { paths, credentials } = await fixture(t);
  const coordinator = new StartupCoordinator({ paths, credentials, permissions: { snapshot: () => ({}) }, broadcast: () => {},
    runtime: { localStatus: async (_config, _signal, components) => {
      assert.deepEqual(components, ['core']);
      return { core: { state: 'unavailable', code: 'component_runtime_relocated' } };
    } }, stopEngine: async () => assert.fail('Do not stop services for an unavailable setup runtime'),
    wechat: { snapshot: () => ({}), provision: async () => assert.fail('Must not prepare WeChat with a broken runtime') } });
  coordinator.capabilities.core = ready();
  const result = await coordinator.request('wechat-provision');
  assert.equal(result.error, 'wechat_runtime_unavailable');
  assert.equal(result.capabilities.core.code, 'component_runtime_relocated');
});
