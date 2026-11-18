// Maker-side check. The relocated application and its children receive only a system PATH.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import { spawn } from 'node:child_process';

const source = path.resolve(process.argv[2] || '.runtime/package-build/mac-arm64/BoxAgent.app');
const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-artifact-'));
const bundle = path.join(directory, 'Moved App/BoxAgent.app');
const data = path.join(directory, 'User Data');
console.log(`Evidence: ${directory}`);
await fs.mkdir(path.dirname(bundle), { recursive: true });
await fs.mkdir(data, { recursive: true });
const cacheFlag = process.argv.indexOf('--model-cache');
const modelCache = cacheFlag === -1 ? null : path.resolve(process.argv[cacheFlag + 1]);
const executable = path.join(bundle, 'Contents/MacOS/BoxAgent');
const resources = path.join(bundle, 'Contents/Resources');
const environment = { HOME: os.homedir(), USER: os.userInfo().username, PATH: '/usr/bin:/bin:/usr/sbin:/sbin',
  TMPDIR: os.tmpdir(), BOXAGENT_DATA_DIR: data, BOXAGENT_ELECTRON_USER_DATA: path.join(data, 'electron') };
async function run(command, args, env = environment, timeout = 120000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { env, cwd: directory, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    child.stdout.on('data', chunk => {
      output += chunk;
      if (env.ELECTRON_RUN_AS_NODE === '1') process.stdout.write(chunk);
    });
    child.stderr.on('data', chunk => { process.stderr.write(chunk); });
    const timer = setTimeout(() => child.kill('SIGKILL'), timeout);
    child.on('error', reject);
    child.on('close', code => { clearTimeout(timer); code === 0 ? resolve(output) : reject(Error(`${path.basename(command)} exited ${code}`)); });
  });
}
await run('/usr/bin/ditto', [source, bundle]);
if (modelCache) await run('/usr/bin/ditto', [modelCache, path.join(data, 'models')]);
await run('/bin/chmod', ['-R', 'a-w', bundle]);
await run('/usr/bin/codesign', ['--verify', '--deep', '--strict', bundle]);
async function checkUi(expectedEngine = false) {
const server = net.createServer();
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
await new Promise(resolve => server.close(resolve));
const log = await fs.open(path.join(directory, expectedEngine ? 'engine-app.log' : 'app.log'), 'w');
const application = spawn(executable, [`--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1'],
  { env: environment, cwd: directory, stdio: ['ignore', log.fd, log.fd] });
const exited = new Promise(resolve => application.once('exit', resolve));
let websocket;
try {
  let target;
  for (let i = 0; i < 200 && !target; i++) {
    const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(response => response.json()).catch(() => []);
    target = targets.find(item => item.url.endsWith('/setup.html'));
    if (!target) await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(target, 'Relocated application did not open onboarding');
  websocket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { websocket.onopen = resolve; websocket.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  websocket.onmessage = event => {
    const message = JSON.parse(event.data);
    const item = pending.get(message.id);
    if (item) { pending.delete(message.id); message.error ? item.reject(Error(message.error.message)) : item.resolve(message.result); }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    websocket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw Error('Renderer evaluation failed');
    return result.result.value;
  };
  let state;
  for (let i = 0; i < 200; i++) {
    try { state = await evaluate(`window.boxagentSetup?.request('snapshot')`); }
    catch (error) {
      // CDP may discover setup.html while its first document is still loading.
      if (error.message !== 'Execution context was destroyed.') throw error;
    }
    if (state && !state.busy) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(state && !state.busy, 'Onboarding snapshot did not become ready');
  assert.equal(state.onboarding.completed, false);
  assert.equal(state.credentials.dashscope, false);
  assert.equal(state.canComplete, false);
  if (expectedEngine) {
    assert.equal(state.capabilities.core.state, 'ready');
    assert.equal(state.capabilities.engine.state, 'ready', 'Real packaged Engine failed to start');
  }
  if (!await evaluate(`document.querySelectorAll('input[type=password]').length`)) {
    let rendered = false;
    for (let i = 0; i < 100; i++) {
      rendered = await evaluate(`Boolean(document.querySelector('footer .setup-primary') && !document.querySelector('footer .setup-primary').disabled)`);
      if (rendered) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(rendered, 'Onboarding next button did not render');
    await evaluate(`document.querySelector('footer .setup-primary').click()`);
  }
  for (let i = 0; i < 100; i++) {
    if (await evaluate(`document.querySelectorAll('input[type=password]').length === 2`)) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(await evaluate(`document.querySelectorAll('input[type=password]').length`), 2);
  assert.equal(await evaluate(`document.querySelector('.setup-provider-submit').disabled`), true);
  await evaluate(`(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    for (const [index, value] of [[0, 'fixture-required-key'], [1, 'fixture-workspace']]) {
      const input = document.querySelectorAll('input')[index]; setter.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  })()`);
  assert.equal(await evaluate(`document.querySelector('.setup-provider-submit').disabled`), true, 'DeepSeek must also be required in the actual app');
  await evaluate(`(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    for (const input of document.querySelectorAll('input')) { setter.call(input, ''); input.dispatchEvent(new Event('input', { bubbles: true })); }
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  })()`);
  await evaluate(`Promise.all(document.getAnimations().filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})))`);
  const layout = await evaluate(`({ width: innerWidth, height: innerHeight, action: document.querySelector('.setup-provider-submit').getBoundingClientRect().toJSON(), scrollWidth: document.documentElement.scrollWidth })`);
  await fs.writeFile(path.join(directory, 'visual-metrics.json'), JSON.stringify(layout, null, 2));
  assert.equal(await evaluate(`(() => {
    const button = document.querySelector('.setup-provider-submit').getBoundingClientRect();
    return innerWidth === 500 && innerHeight === 570 && button.top >= 48 && button.bottom <= innerHeight &&
      Math.abs((button.left + button.right) / 2 - innerWidth / 2) < 1 && document.documentElement.scrollWidth <= innerWidth;
  })()`), true, 'Save/verify action must be visible and centered in the compact window');
  await new Promise(resolve => setTimeout(resolve, 350));
  const screenshot = await send('Page.captureScreenshot', { format: 'png' });
  await fs.writeFile(path.join(directory, 'providers.png'), Buffer.from(screenshot.data, 'base64'));
  console.log('PASS: relocated signed app, system PATH, first launch, required BYOK form.');
  if (expectedEngine) {
    console.log('PASS: actual packaged Python desktop service and Engine socket health.');
    await evaluate(`window.boxagentSetup.request('step', { step: 'wechat' })`);
    const supplied = await evaluate(`window.boxagentSetup.request('snapshot')`);
    assert.equal(supplied.wechat.downloadAvailable, true, 'Installer must provide the exact verified client');
    const began = Date.now();
    const prepared = await evaluate(`window.boxagentSetup.request('wechat-provision')`);
    assert.equal(prepared.error, '', 'Automatic replica provisioning failed in the packaged app');
    assert.equal(prepared.wechat.client.state, 'ready');
    assert.equal(prepared.credentials.wechatEnabled, false);
    assert.equal(prepared.credentials.wechatConfigured, false);
    const image = await send('Page.captureScreenshot', { format: 'png' });
    await fs.writeFile(path.join(directory, 'wechat-provisioned.png'), Buffer.from(image.data, 'base64'));
    console.log('PASS: actual packaged fixed-build replica provisioning, no login, ' + (Date.now() - began) + ' ms');
    if (process.argv.includes('--wechat-login')) {
      console.log('ACTION: complete QR sign-in in the new WeChat copy; no messages will be sent.');
      const captured = await evaluate(`window.boxagentSetup.request('wechat-capture')`);
      assert.equal(captured.error, '', 'Real sign-in and encrypted credential acquisition failed');
      assert.equal(captured.credentials.wechatConfigured, true);
      assert.equal(captured.credentials.wechatEnabled, false, 'Capture alone must not enable model message processing');
      assert.equal(captured.wechat.reading.state, 'ready');
      assert.equal(captured.wechat.listening.state, 'ready');
      console.log('PASS: real sign-in, account-authenticated read/listener checks, encrypted credential storage');
    }
  }
} finally {
  websocket?.close();
  application.kill('SIGTERM');
  const forced = setTimeout(() => application.kill('SIGKILL'), 5000);
  await exited;
  clearTimeout(forced);
  await log.close();
}
}
await checkUi();
if (process.argv.includes('--ui-only')) process.exit(0);

// Use the application-owned Electron/Node and its actual asar modules for installation.
const runner = path.join(directory, 'runtime-check.cjs');
await fs.writeFile(runner, `
const path = require('node:path');
const fs = require('node:fs/promises');
const resources = process.argv[2], data = process.argv[3];
const { resolvePaths } = require(path.join(resources, 'app.asar/startup/paths.cjs'));
const { PackagedRuntime } = require(path.join(resources, 'app.asar/startup/runtime.cjs'));
const { run } = require(path.join(resources, 'app.asar/startup/process.cjs'));
(async () => {
  const paths = resolvePaths({ packaged: true, resources, appData: data });
  const runtime = new PackagedRuntime(paths, { progress: value => console.log(JSON.stringify(value)) });
  await runtime.inputs();
  await fs.mkdir(paths.runtime, { recursive: true });
  const env = runtime.environment();
  for (const group of ['core', 'jev', 'vision']) {
    const python = path.join(paths.runtime, group, 'bin/python3');
    await run(path.join(resources, 'uv'), ['venv', '--no-config', '--no-python-downloads', '--python', path.join(resources, 'python/bin/python3'), path.dirname(path.dirname(python))], { env });
    await run(path.join(resources, 'uv'), ['pip', 'sync', '--no-config', '--no-cache', '--no-build', '--python', python, '--no-index', '--find-links', path.join(resources, 'python-dependencies', group, 'wheels'), '--require-hashes', path.join(resources, 'python-dependencies', group, 'requirements.lock')], { env, timeout: 900000 });
    console.log('PASS: offline ' + group + ' installation');
  }
  await runtime.probe(paths.python, 'core');
  // No provider calls: this synthetic configuration is used only to initialize local Jev.
  if (process.argv[4] === '--models') {
    const localConfig = { DASHSCOPE_API_KEY: 'artifact-local-initialization', BOXAGENT_DASHSCOPE_WORKSPACE_ID: 'artifact-local' };
    await runtime.install(localConfig, new AbortController().signal);
    const began = Date.now();
    const status = await runtime.localStatus(localConfig);
    if (Object.values(status).some(value => value.state !== 'ready')) throw Error(JSON.stringify(status));
    console.log('PASS: actual core/Jev/MLX and pinned model initialization (no cloud inference)');
    console.log('Warm component checks: ' + (Date.now() - began) + ' ms');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
`);
await run(executable, [runner, resources, data, ...(process.argv.includes('--models') ? ['--models'] : [])],
  { ...environment, ELECTRON_RUN_AS_NODE: '1' }, 1800000);
await checkUi(true);
console.log(`Evidence: ${directory}`);
