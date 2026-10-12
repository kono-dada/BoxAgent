// Regression checks for the single React root using isolated IPC records.
const { app, BrowserWindow, ipcMain } = require('electron');
const assert = require('node:assert/strict');
const path = require('node:path');
app.whenReady().then(async () => {
  const window = new BrowserWindow({ width: 1200, height: 820, show: true, webPreferences: {
    partition: 'panel-lifecycle-smoke', preload: path.join(__dirname, '../preload.cjs'),
    contextIsolation: true, nodeIntegration: false, sandbox: true
  } });
  const sessions = ['one', 'two'].map((id) => ({ session_id: id, title: id, updated_at: 1 }));
  const state = { session_id: 'one', voice: 'off', task: 'idle' };
  const calls = [];
  let resolveMemory, resolveHistory;
  const timeline = (id) => Array.from({ length: 60 }, (_, index) => ({ seq: index + 1,
    type: 'assistant/message', time: 172800, data: { content: `${id} history ${index}` } }));
  ipcMain.handle('engine:errors', () => []);
  ipcMain.handle('engine:request', (_event, method, params = {}) => {
    calls.push({ method, params });
    switch (method) {
      case 'state': return state;
      case 'list_sessions': return sessions;
      case 'session_events': return [];
      case 'session_timeline': return params.session_id === 'two'
        ? new Promise((resolve) => { resolveHistory = resolve; }) : timeline('one');
      case 'memory_snapshot': return new Promise((resolve) => { resolveMemory = resolve; });
      case 'desktop_persona': return { name: 'Companion', content: 'Original persona' };
      case 'desktop_snapshot': return {};
      default: throw Error(method);
    }
  });
  const js = async (source) => { try { return await window.webContents.executeJavaScript(source); } catch (error) { console.error('Failed renderer script:', source); throw error; } };
  const wait = async (condition) => {
    for (let index = 0; index < 100; index++) {
      if (await js(condition)) return;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw Error(condition);
  };
  const ready = (view) => wait(`document.body.dataset.view === '${view}' && document.getElementById('content').getAttribute('aria-busy') === 'false'`);
  const nav = async (view) => { await js(`document.querySelector('[data-view="${view}"]').click()`); await ready(view); };
  const input = async (selector, value) => {
    await js(`(() => { const element = document.querySelector(${JSON.stringify(selector)});
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(element, ${JSON.stringify(value)});
      element.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await new Promise((resolve) => setTimeout(resolve, 80));
  };
  try {
    await window.loadFile(path.join(__dirname, '../dist/index.html'));
    await wait(`!document.getElementById('refresh').disabled`); await ready('conversation');
    await nav('timeline');
    await input('#search', 'one');
    await js(`document.getElementById('page').scrollTop = 330`);
    const scroll = await js(`document.getElementById('page').scrollTop`);
    assert(scroll > 0);
    await nav('settings');
    // The overview cards used to depend on the removed global click listener.
    await js(`document.querySelector('.settings-category[data-view="persona"]').click()`);
    await ready('persona');
    await input('[aria-label="角色名称"]', 'Unsaved draft');
    await js(`document.dispatchEvent(new CustomEvent('panel-preferences'))`);
    window.webContents.send('engine:state', { ...state, voice: 'ready' });
    await wait(`document.getElementById('runtime-status').textContent.includes('已开启')`);
    assert.equal(await js(`document.querySelector('[aria-label="角色名称"]').value`), 'Unsaved draft');
    assert.equal(calls.filter((call) => call.method === 'desktop_persona').length, 1);
    await js(`document.getElementById('settings-back').click()`); await ready('timeline');
    assert.equal(await js(`document.getElementById('search').value`), 'one');
    assert.equal(await js(`document.getElementById('page').scrollTop`), scroll);
    await js(`document.querySelector('.timeline-event').click()`);
    await wait(`document.getElementById('detail').open`);
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' });
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' });
    await wait(`!document.getElementById('detail').open && document.activeElement.classList.contains('timeline-event')`);
    // Discard a delayed page result after navigation, and discard a different session's result.
    await js(`document.querySelector('[data-view="memory"]').click()`);
    await wait(`document.body.dataset.view === 'memory' && document.getElementById('content').getAttribute('aria-busy') === 'true'`);
    assert(resolveMemory);
    await nav('timeline');
    resolveMemory({ nodes: [], edges: [] });
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert(await js(`Boolean(document.querySelector('.timeline-event')) && !document.querySelector('.memory-explorer')`));
    await js(`document.querySelector('[data-session-id="two"]').click()`);
    await wait(`document.getElementById('content').getAttribute('aria-busy') === 'true'`);
    assert(resolveHistory);
    await js(`document.querySelector('[data-session-id="one"]').click()`); await ready('timeline');
    resolveHistory(timeline('two'));
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert(await js(`document.querySelector('.timeline-event').textContent.includes('one history') && !document.getElementById('content').textContent.includes('two history')`));
    // Subscription cleanup must work across Electron's isolated preload bridge.
    await js(`window.fixtureEvents = 0; void (window.stopFixture = boxagent.onState(() => window.fixtureEvents++))`);
    window.webContents.send('engine:state', state);
    await wait(`window.fixtureEvents === 1`);
    await js(`window.stopFixture()`);
    window.webContents.send('engine:state', state);
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(await js(`window.fixtureEvents`), 1);
    console.log('React lifecycle: settings cards/back, search/scroll, draft/state preservation, detail Escape/focus, delayed page/session isolation and IPC unsubscribe passed');
    app.quit();
  } catch (error) { console.error(error); app.exit(1); }
});
