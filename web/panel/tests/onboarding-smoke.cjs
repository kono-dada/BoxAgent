// Run with Electron, not node:test. All provider and Python boundaries are fixtures.
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const { StartupCoordinator } = require('../startup/coordinator.cjs');
const { CredentialStore } = require('../startup/credentials.cjs');
const { createSetupWindow } = require('../startup/window.cjs');
const data = path.resolve(__dirname, '../../../.runtime/onboarding-smoke');
app.setPath('userData', path.join(data, 'electron'));
app.on('window-all-closed', () => {});
let coordinator;
app.whenReady().then(async () => {
  await fs.rm(path.join(data, 'state'), { recursive: true, force: true });
  const encryption = { isEncryptionAvailable: () => true, encryptString: value => Buffer.from(value), decryptString: value => value.toString() };
  let healthy = false, shown = 0;
  const ready = () => ({ state: 'ready', code: '' });
  let setup;
  coordinator = new StartupCoordinator({ paths: { data, root: data, packaged: true },
    credentials: new CredentialStore(path.join(data, 'state/credentials.json'), encryption),
    permissions: { snapshot: () => ({ microphone: 'denied', screen: 'denied', accessibility: false }), request: async () => {} },
    runtime: { localStatus: async () => ({ core: ready(), memory: ready(), vision: ready() }), environment: value => value },
    ensureEngine: async () => { if (!healthy) throw Error('fixture engine failed'); }, stopEngine: async () => {},
    enterDesktop: async () => { shown++; }, broadcast: state => setup?.send(state),
    validate: async config => ({ qwen: config.DASHSCOPE_API_KEY === 'test-key' ? ready() : { state: 'invalid', code: 'qwen_auth_failed' }, task: { state: 'missing', code: 'task_configuration_missing' } }),
  });
  setup = createSetupWindow({ app, BrowserWindow, ipcMain, panelRoot: path.resolve(__dirname, '..'), registerWindow: () => {}, coordinator, shell: { showItemInFolder: () => {} } });
  const window = setup.show();
  await new Promise(resolve => window.webContents.once('did-finish-load', resolve));
  await coordinator.start();
  const evaluate = script => window.webContents.executeJavaScript(script);
  const capture = async name => {
    await new Promise(resolve => setTimeout(resolve, 350));
    await fs.writeFile(path.join(data, `screenshots/${name}.png`), (await window.webContents.capturePage()).toPNG());
  };
  const waitFor = async script => {
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) { if (await evaluate(script)) return; await new Promise(resolve => setTimeout(resolve, 50)); }
    throw Error(`UI condition timed out: ${script}`);
  };
  await waitFor(`document.querySelectorAll('.setup-language button').length === 2`);
  await evaluate(`document.querySelector('.setup-language button').click()`);
  await waitFor(`document.querySelector('h1')?.textContent === '认识 BoxAgent'`);
  await fs.mkdir(path.join(data, 'screenshots'), { recursive: true });
  await capture('welcome');
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await waitFor(`document.querySelector('h1')?.textContent === '模型服务'`);
  await evaluate(`(() => { const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; for(const [input,value] of [[document.querySelectorAll('input')[0],'test-key'],[document.querySelectorAll('input')[1],'test-workspace']]) { set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true})); } })()`);
  await waitFor(`!document.querySelector('form button[type=submit]').disabled`);
  await evaluate(`document.querySelector('form button[type=submit]').click()`);
  await waitFor(`document.querySelector('input[type=password]').value === '' && document.querySelector('.setup-status .is-ready')`);
  assert.equal(coordinator.summary.dashscope, true);
  assert.equal(coordinator.capabilities.engine.state, 'missing');
  await capture('providers');
  await evaluate(`document.querySelectorAll('.setup-language button')[1].click()`);
  await waitFor(`document.querySelector('h1')?.textContent === 'Model services'`);
  await capture('providers-en');
  const blocked = await coordinator.request('complete');
  assert.equal(blocked.onboarding.completed, false);
  assert.equal(shown, 0);
  healthy = true;
  await coordinator.request('step', { step: 'ready' });
  await coordinator.request('refresh');
  await waitFor(`!document.querySelector('footer .setup-primary').disabled`);
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(coordinator.state.completed, true);
  assert.equal(shown, 1);
  console.log('PASS: real Electron/React setup, key form, language, Engine failure, completion gate.');
  app.quit();
}).catch(error => { console.error(error); app.exit(1); });
app.on('before-quit', () => coordinator?.close());
