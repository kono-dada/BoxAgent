const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { createHash } = require('node:crypto');
const { PackagedRuntime } = require('../startup/runtime.cjs');
const { verifyModel, parseManifest } = require('../startup/download.cjs');

test('tampered package resource fails before any installer starts', async t => {
  const resources = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-package-'));
  t.after(() => fs.rm(resources, { recursive: true, force: true }));
  await fs.writeFile(path.join(resources, 'wheel.whl'), 'altered');
  await fs.writeFile(path.join(resources, 'runtime-manifest.json'), JSON.stringify({ version: 1, pythonVersion: '3.12.9', groups: { core: 'c', jev: 'j', vision: 'v' },
    files: [{ path: 'wheel.whl', sha256: createHash('sha256').update('original').digest('hex') }] }));
  const runtime = new PackagedRuntime({ resources }, { execute: async () => assert.fail('Installer started with invalid inputs') });
  await assert.rejects(runtime.install({}, new AbortController().signal), /packaged_inputs_invalid/);
});
test('manifest traversal and model file damage are rejected', async t => {
  assert.throws(() => parseManifest(`../escape\t${'a'.repeat(64)}`), /invalid_model_manifest/);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-model-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const manifest = `model.safetensors\t${createHash('sha256').update('correct').digest('hex')}`;
  await fs.writeFile(path.join(directory, 'model.safetensors'), 'wrong');
  assert.equal(await verifyModel(manifest, directory), false);
  await fs.writeFile(path.join(directory, 'model.safetensors'), 'correct');
  assert.equal(await verifyModel(manifest, directory), true);
});

test('failed dependency installation revokes success without deleting product data', async t => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-repair-'));
  t.after(() => fs.rm(data, { recursive: true, force: true }));
  const runtimeDir = path.join(data, 'runtimes'), resources = path.join(data, 'resources');
  await fs.mkdir(path.join(runtimeDir, 'core'), { recursive: true });
  await fs.mkdir(path.join(data, 'sessions'), { recursive: true });
  await fs.writeFile(path.join(data, 'sessions/keep.jsonl'), 'user conversation');
  const marker = path.join(runtimeDir, 'core/boxagent-runtime.json');
  await fs.writeFile(marker, JSON.stringify({ id: 'old', base: 'old-python' }));
  const runtime = new PackagedRuntime({ data, runtime: runtimeDir, resources }, { execute: async (_executable, args) => {
    if (args.includes('sync')) throw Error('dependency_install_failed');
  } });
  runtime.inputs = async () => ({ groups: { core: 'new', jev: 'j', vision: 'v' } });
  await assert.rejects(runtime.install({}, new AbortController().signal), /dependency_install_failed/);
  await assert.rejects(fs.readFile(marker), error => error.code === 'ENOENT');
  assert.equal(await fs.readFile(path.join(data, 'sessions/keep.jsonl'), 'utf8'), 'user conversation');
});

test('healthy existing component environments are reused without installation', async t => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-reuse-'));
  t.after(() => fs.rm(data, { recursive: true, force: true }));
  const runtimeDir = path.join(data, 'runtimes'), resources = path.join(data, 'resources');
  const groups = { core: 'c', jev: 'j', vision: 'v' };
  for (const [group, id] of Object.entries(groups)) {
    await fs.mkdir(path.join(runtimeDir, group), { recursive: true });
    await fs.writeFile(path.join(runtimeDir, group, 'boxagent-runtime.json'), JSON.stringify({ version: 1, id, base: path.join(resources, 'python/bin/python3') }));
  }
  const probes = [];
  const runtime = new PackagedRuntime({ data, runtime: runtimeDir, resources }, { execute: async () => assert.fail('Reinstalled a healthy environment') });
  runtime.inputs = async () => ({ groups });
  runtime.probe = async (_python, component) => { probes.push(component); };
  await runtime.install({}, new AbortController().signal);
  assert.deepEqual(probes, ['core', 'memory', 'vision']);
});

test('moving the app rebuilds only offline environments and reuses verified models', async t => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-upgrade-'));
  t.after(() => fs.rm(data, { recursive: true, force: true }));
  const runtimeDir = path.join(data, 'runtimes'), resources = path.join(data, 'new-app/Resources');
  const root = path.join(resources, 'boxagent'), model = path.join(data, 'models/vision');
  const groups = { core: 'c', jev: 'j', vision: 'v' };
  for (const [group, id] of Object.entries(groups)) {
    await fs.mkdir(path.join(runtimeDir, group), { recursive: true });
    await fs.writeFile(path.join(runtimeDir, group, 'boxagent-runtime.json'), JSON.stringify({ version: 1, id, base: '/Volumes/old-app/python3' }));
  }
  await fs.mkdir(path.join(root, 'scripts/model-manifests'), { recursive: true });
  await fs.mkdir(model, { recursive: true });
  await fs.writeFile(path.join(model, 'weights'), 'cached-weights');
  await fs.writeFile(path.join(root, 'scripts/model-manifests/qwen3.5-0.8b-mlx.manifest.tsv'),
    `weights\t${createHash('sha256').update('cached-weights').digest('hex')}`);
  const commands = [], probes = [];
  const runtime = new PackagedRuntime({ data, runtime: runtimeDir, resources, root, model }, { execute: async (_program, args) => commands.push(args) });
  runtime.inputs = async () => ({ groups });
  runtime.probe = async (_python, component) => { probes.push(component); };
  t.mock.method(globalThis, 'fetch', async () => assert.fail('Downloaded a cached model'));
  await runtime.install({}, new AbortController().signal);
  assert.equal(commands.length, 6);
  for (const args of commands) assert.ok(args.includes('--no-python-downloads') || args.includes('--no-index'));
  assert.deepEqual(probes, ['core', 'memory', 'vision']);
  assert.equal(JSON.parse(await fs.readFile(path.join(runtimeDir, 'core/boxagent-runtime.json'))).base, path.join(resources, 'python/bin/python3'));
  assert.equal(await fs.readFile(path.join(model, 'weights'), 'utf8'), 'cached-weights');
});

test('a runtime linked to an old app is rejected even while the old location still works', async t => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-relocation-'));
  t.after(() => fs.rm(data, { recursive: true, force: true }));
  const runtimeDir = path.join(data, 'runtimes');
  await fs.mkdir(path.join(runtimeDir, 'core'), { recursive: true });
  await fs.writeFile(path.join(runtimeDir, 'core/boxagent-runtime.json'), JSON.stringify({ base: '/Volumes/BoxAgent/python3' }));
  const runtime = new PackagedRuntime({ packaged: true, data, runtime: runtimeDir, resources: path.join(data, 'new-resources') });
  runtime.probe = async () => assert.fail('Must not keep using an old mounted runtime');
  const result = await runtime.localStatus({}, new AbortController().signal, ['core']);
  assert.equal(result.core.code, 'component_runtime_relocated');
  assert.equal(result.core.state, 'unavailable');
});
