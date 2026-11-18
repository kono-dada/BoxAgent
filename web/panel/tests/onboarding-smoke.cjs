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
  let storageUnlocked = true;
  const encryption = { isEncryptionAvailable: () => storageUnlocked, encryptString: value => Buffer.from(value), decryptString: value => value.toString() };
  let healthy = true, shown = 0, granted = false, speechGranted = false;
  const ready = () => ({ state: 'ready', code: '' });
  const credentials = new CredentialStore(path.join(data, 'state/credentials.json'), encryption);
  const wechatChecks = { client: { state: 'missing', code: 'wechat_client_missing' },
    reading: { state: 'missing', code: 'wechat_keys_missing' }, listening: { state: 'missing', code: 'wechat_keys_missing' },
    sending: { state: 'missing', code: 'wechat_native_check_required' }, downloadAvailable: true };
  const wechat = {
    snapshot: () => ({ ...wechatChecks }), manifest: async () => {}, refresh: async () => {},
    provision: async () => { wechatChecks.client = ready(); },
    capture: async () => {
      wechatChecks.reading = ready(); wechatChecks.listening = ready();
      await credentials.save({ BOXAGENT_WECHAT_APP: '/fixture/WeChat.app', BOXAGENT_WECHAT_DB_ROOT: '/fixture/db_storage',
        BOXAGENT_WECHAT_KEY_MATERIAL: JSON.stringify({ keys: { 'message/message_0.db': 'a1'.repeat(32) } }) });
    },
    verify: async (_signal, native) => { if (native) wechatChecks.sending = ready(); },
  };
  let setup;
  coordinator = new StartupCoordinator({ paths: { data, root: data, packaged: true },
    credentials, wechat,
    detectTools: async () => ({ codex: '/fixture/codex', client: '/fixture/SkyComputerUseClient' }),
    permissions: { snapshot: () => ({ microphone: granted ? 'granted' : 'denied', speech: speechGranted ? 'granted' : 'not-determined', screen: 'denied', accessibility: false }),
      request: async permission => { if (permission === 'speech') speechGranted = true; else granted = true; } },
    runtime: { localStatus: async () => ({ core: ready(), memory: ready(), vision: ready() }), environment: value => value,
      install: async (_config, signal) => {
        coordinator.progress = { component: 'vision-model', phase: 'downloading', bytes: 64 * 1024 ** 2, total: 256 * 1024 ** 2 };
        coordinator.emit();
        await new Promise((resolve, reject) => { signal.addEventListener('abort', () => reject(Error('cancelled')), { once: true }); });
      } },
    ensureEngine: async () => { if (!healthy) throw Error('fixture engine failed'); }, stopEngine: async () => {},
    enterDesktop: async () => { shown++; }, broadcast: state => setup?.send(state),
    validate: async config => ({ qwen: config.DASHSCOPE_API_KEY === 'test-key' ? ready() : { state: 'invalid', code: 'qwen_auth_failed' },
      task: config.DEEPSEEK_API_KEY === 'test-ds-key' ? ready() : { state: 'invalid', code: 'deepseek_auth_failed' } }),
  });
  setup = createSetupWindow({ app, BrowserWindow, ipcMain, panelRoot: path.resolve(__dirname, '..'), registerWindow: () => {}, coordinator, shell: { showItemInFolder: () => {} } });
  const window = setup.show();
  window.webContents.setBackgroundThrottling(false);
  await new Promise(resolve => window.webContents.once('did-finish-load', resolve));
  await coordinator.start();
  assert.deepEqual(window.getContentSize(), [500, 570]);
  const evaluate = script => window.webContents.executeJavaScript(script);
  const capture = async name => {
    window.webContents.invalidate();
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    await new Promise(resolve => setTimeout(resolve, 350));
    await fs.writeFile(path.join(data, `screenshots/${name}.png`), (await window.webContents.capturePage()).toPNG());
  };
  const waitFor = async script => {
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) { if (await evaluate(script)) return; await new Promise(resolve => setTimeout(resolve, 50)); }
    throw Error(`UI condition timed out: ${script}`);
  };
  const setInput = async (index, value) => evaluate(`(() => { const input=document.querySelectorAll('input')[${index}]; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  const checkLayout = async () => {
    await new Promise(resolve => setTimeout(resolve, 250));
    assert.equal(await evaluate(`(() => {
      const button=document.querySelector('footer .setup-primary').getBoundingClientRect();
      return button.bottom <= innerHeight && button.top >= 48 && Math.abs((button.left+button.right)/2-innerWidth/2)<1 && document.documentElement.scrollWidth <= innerWidth;
    })()`), true, 'Primary action must remain visible and centered without horizontal overflow');
    assert.equal(await evaluate(`(() => {
      if (innerWidth < 500 || document.querySelector('.setup-shell').dataset.step !== 'permissions' || document.querySelector('.setup-details').open) return true;
      const refresh = document.querySelector('.setup-centered-action').getBoundingClientRect();
      const content = document.querySelector('.setup-content').getBoundingClientRect();
      return refresh.top >= content.top && refresh.bottom <= content.bottom;
    })()`), true, 'Permission refresh action must be fully visible at standard size');
  };
  await waitFor(`document.querySelectorAll('.setup-language button').length === 2`);
  await evaluate(`document.querySelector('.setup-language button').click()`);
  await waitFor(`document.querySelector('h1')?.textContent === '认识 BoxAgent'`);
  await fs.mkdir(path.join(data, 'screenshots'), { recursive: true });
  await checkLayout();
  await capture('welcome');
  window.webContents.debugger.attach('1.3');
  await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.setup-shell')).backgroundColor`), 'rgba(10, 11, 15, 0.9)');
  await capture('welcome-system-light');
  healthy = false;
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await waitFor(`document.querySelector('h1')?.textContent === '模型服务'`);
  assert.equal(await evaluate(`document.querySelector('.setup-provider-submit').disabled`), true);
  await checkLayout();
  await capture('providers-empty');
  assert.equal(await evaluate(`(() => {
    const note = document.querySelector('.setup-security').getBoundingClientRect();
    const content = document.querySelector('.setup-content').getBoundingClientRect();
    return note.top >= content.top && note.bottom <= content.bottom;
  })()`), true, 'First-time provider form must show the complete key protection note');
  for (let index = 0; index < 3; index++) {
    await evaluate(`document.querySelectorAll('input')[${index}].focus()`);
    await capture(`providers-focused-${index}`);
  }
  await evaluate(`document.activeElement.blur()`);
  await fs.writeFile(path.join(data, 'visual-metrics.json'), JSON.stringify(await evaluate(`(() => {
    const style = element => getComputedStyle(document.querySelector(element));
    return { window: [innerWidth, innerHeight], titlebarHeight: document.querySelector('.setup-titlebar').getBoundingClientRect().height,
      titleSize: style('h1').fontSize, tint: style('.setup-shell').backgroundColor,
      accent: style('.setup-primary').backgroundColor, primary: document.querySelector('.setup-primary').getBoundingClientRect().toJSON(),
      dotWidths: [...document.querySelectorAll('.setup-journey button')].map(button => button.getBoundingClientRect().width), font: style('h1').fontFamily };
  })()`), null, 2));
  await setInput(0, 'wrong-key');
  await setInput(1, 'test-workspace');
  assert.equal(await evaluate(`document.querySelector('.setup-provider-submit').disabled`), true, 'DeepSeek is required');
  await setInput(2, 'test-ds-key');
  await waitFor(`!document.querySelector('.setup-provider-submit').disabled`);
  storageUnlocked = false;
  await evaluate(`document.querySelector('.setup-provider-submit').click()`);
  await waitFor(`document.querySelector('[role=alert]')?.textContent.includes('系统密钥存储暂不可用')`);
  assert.equal(await evaluate(`document.querySelector('input').value`), 'wrong-key', 'Failed save must preserve unsaved keys');
  await capture('providers-storage-error');
  storageUnlocked = true;
  await evaluate(`document.querySelector('input').focus()`);
  window.focus();
  window.webContents.focus();
  window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Return' });
  window.webContents.sendInputEvent({ type: 'char', keyCode: '\r' });
  window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Return' });
  await waitFor(`document.querySelector('.setup-status')?.textContent.includes('千问鉴权失败')`);
  assert.equal(coordinator.capabilities.qwen.state, 'invalid');
  await capture('providers-auth-error');
  await setInput(0, 'test-key');
  await setInput(2, 'wrong-ds-key');
  await waitFor(`!document.querySelector('.setup-provider-submit').disabled`);
  await evaluate(`document.querySelector('.setup-provider-submit').click()`);
  await waitFor(`document.querySelector('.setup-status')?.textContent.includes('DeepSeek 鉴权失败')`);
  assert.equal(coordinator.capabilities.qwen.state, 'ready');
  assert.equal(coordinator.capabilities.task.state, 'invalid');
  assert.ok(await evaluate(`Boolean(document.querySelector('.setup-provider-submit'))`));
  await capture('providers-ds-auth-error');
  await setInput(2, 'test-ds-key');
  await waitFor(`!document.querySelector('.setup-provider-submit').disabled`);
  await evaluate(`document.querySelector('.setup-provider-submit').click()`);
  await waitFor(`document.querySelector('input[type=password]').value === '' && !document.querySelector('.setup-provider-submit')`);
  assert.equal(coordinator.summary.dashscope, true);
  assert.equal(coordinator.summary.deepseek, true);
  assert.equal(coordinator.capabilities.engine.state, 'missing');
  await capture('providers');
  await evaluate(`document.querySelectorAll('.setup-language button')[1].click()`);
  await waitFor(`document.querySelector('h1')?.textContent === 'Model services'`);
  await capture('providers-en');
  await setInput(2, 'updated-key');
  await waitFor(`Boolean(document.querySelector('.setup-provider-submit'))`);
  await evaluate(`document.querySelectorAll('.setup-language button')[0].click()`);
  assert.equal(await evaluate(`document.querySelectorAll('input')[2].value`), 'updated-key', 'Language switch must preserve unsaved keys');
  await setInput(2, '');
  await waitFor(`!document.querySelector('.setup-provider-submit')`);
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await waitFor(`document.querySelector('h1')?.textContent === '本地组件'`);
  await checkLayout();
  await capture('components');
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await waitFor(`Boolean(document.querySelector('progress'))`);
  assert.equal(await evaluate(`document.querySelector('progress').position`), .25);
  assert.equal(await evaluate(`document.querySelectorAll('.setup-feature-card').length`), 2);
  assert.equal(await evaluate(`document.querySelector('.setup-progress-percent').textContent`), '25%');
  assert.equal(await evaluate(`(() => {
    const button = document.querySelector('.setup-cancel'), style = getComputedStyle(button);
    return button.textContent.includes('取消下载') && parseFloat(style.borderTopWidth) >= 1 &&
      style.borderTopStyle !== 'none' && button.getBoundingClientRect().height >= 32;
  })()`), true, 'Cancel must look and behave like a button');
  await capture('components-downloading');
  await evaluate(`document.querySelector('.setup-progress button').click()`);
  await waitFor(`document.querySelector('[role=alert]')?.textContent.includes('已取消')`);
  await capture('components-cancelled');
  healthy = true;
  await coordinator.request('refresh');
  await waitFor(`document.querySelector('footer .setup-primary')?.textContent === '下一步'`);
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await waitFor(`document.querySelector('h1')?.textContent === '权限与体验'`);
  assert.equal(await evaluate(`document.querySelectorAll('.setup-permission').length`), 4);
  assert.equal(await evaluate(`document.querySelectorAll('.setup-permission h3')[1].textContent`), '语音识别');
  await capture('permissions');
  await checkLayout();
  assert.equal(await evaluate(`document.querySelector('.setup-desktop-details summary')?.textContent.includes('桌面操作已就绪')`), true);
  assert.equal(await evaluate(`document.querySelector('.setup-tools-repair').open`), false, 'Manual tool selection is repair-only');
  await evaluate(`document.querySelector('.setup-permission button').click()`);
  await waitFor(`document.querySelector('.setup-permission button')?.textContent === '已授权'`);
  await evaluate(`document.querySelectorAll('.setup-permission button')[1].click()`);
  await waitFor(`document.querySelectorAll('.setup-permission button')[1]?.textContent === '已授权'`);
  await capture('permissions-granted');
  await evaluate(`document.querySelector('.setup-details').open = true; document.querySelector('.setup-content').scrollTop = 1000;`);
  await checkLayout();
  await capture('permissions-tools');
  await evaluate(`document.querySelector('.setup-tools-repair').open = true; document.querySelector('.setup-content').scrollTop = 1000;`);
  await checkLayout();
  await capture('permissions-tools-repair');
  await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }, { name: 'prefers-contrast', value: 'more' }] });
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await waitFor(`document.querySelector('h1')?.textContent === '接入微信'`);
  await checkLayout();
  assert.equal(await evaluate(`document.querySelectorAll('.setup-wechat-steps > li').length`), 3);
  assert.equal(await evaluate(`document.querySelector('.setup-page').textContent.includes('同账号登录副本可能让主微信下线')`), true);
  await capture('wechat');
  await evaluate(`[...document.querySelectorAll('.setup-wechat-steps button')].find(button => button.textContent === '准备微信副本').click()`);
  await waitFor(`document.querySelectorAll('.setup-wechat-stage')[1].open`);
  await capture('wechat-login');
  await evaluate(`[...document.querySelectorAll('.setup-wechat-steps button')].find(button => button.textContent === '启动并扫码登录').click()`);
  await waitFor(`document.querySelectorAll('.setup-wechat-stage')[2].open`);
  await evaluate(`[...document.querySelectorAll('.setup-wechat-steps button')].find(button => button.textContent === '后台发送预检').click()`);
  await waitFor(`document.querySelectorAll('.setup-status .is-ready').length === 3`);
  assert.equal(coordinator.summary.wechatEnabled, false, 'Verification alone cannot enable message processing');
  await evaluate(`[...document.querySelectorAll('button')].find(button => button.textContent === '启用微信').click()`);
  await waitFor(`Boolean([...document.querySelectorAll('button')].find(button => button.textContent === '停用微信'))`);
  assert.ok(!JSON.stringify(coordinator.snapshot()).includes('a1'.repeat(32)), 'Credentials must never reach renderer snapshots');
  await capture('wechat-ready');
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await waitFor(`document.querySelector('h1')?.textContent === '准备好了'`);
  assert.equal(await evaluate(`document.querySelector('.setup-page').querySelectorAll(':scope > p').length`), 0, 'Ready page should keep technical copy inside details');
  assert.equal(await evaluate(`document.querySelector('.setup-main').getAnimations().length`), 0);
  await checkLayout();
  await capture('ready-high-contrast');
  await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [] });
  await capture('ready');
  await evaluate(`document.querySelectorAll('.setup-language button')[1].click()`);
  for (const step of ['welcome', 'providers', 'components', 'permissions', 'wechat', 'ready']) {
    await coordinator.request('step', { step });
    await waitFor(`document.querySelector('.setup-shell')?.dataset.step === '${step}'`);
    await checkLayout();
    await capture(`${step}-en`);
  }
  await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-transparency', value: 'reduce' }] });
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.setup-shell')).backgroundColor`), 'rgb(20, 21, 27)');
  await capture('ready-no-transparency');
  await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [] });
  window.webContents.setZoomFactor(2);
  for (const step of ['welcome', 'providers', 'components', 'permissions', 'wechat', 'ready']) {
    await coordinator.request('step', { step });
    await waitFor(`document.querySelector('.setup-shell')?.dataset.step === '${step}'`);
    await checkLayout();
    await capture(`${step}-zoom-200`);
  }
  window.webContents.setZoomFactor(1);
  healthy = false;
  const blocked = await coordinator.request('complete');
  assert.equal(blocked.onboarding.completed, false);
  assert.equal(shown, 0);
  healthy = true;
  await coordinator.request('refresh');
  delete coordinator.capabilities.qwen;
  delete coordinator.capabilities.task;
  coordinator.emit();
  await waitFor(`document.querySelector('footer .setup-primary')?.textContent === 'Check and start'`);
  await waitFor(`!document.querySelector('footer .setup-primary').disabled`);
  await evaluate(`document.querySelector('footer .setup-primary').click()`);
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(coordinator.state.completed, true);
  assert.equal(shown, 1);
  console.log('PASS: real Electron/React, six pages in both languages, WeChat isolation copy, compact layout, keyboard submit, save/auth errors, preserved keys, download cancellation, permissions, motion/contrast/transparency preferences, completion gate.');
  app.quit();
}).catch(error => { console.error(error); app.exit(1); });
app.on('before-quit', () => coordinator?.close());
