// Real iframe messaging and avatar controls, with a local preview/IPC fixture.
const { app, BrowserWindow, ipcMain, nativeTheme } = require('electron');
const assert = require('node:assert/strict');
const path = require('node:path');
const http = require('node:http');
app.whenReady().then(async () => {
  const server = http.createServer((_request, response) => {
    response.setHeader('Content-Type', 'text/html');
    response.end(`<script>
      document.documentElement.dataset.theme = new URL(location).searchParams.get('previewTheme');
      window.messages = 0;
      addEventListener('message', event => {
        if (event.data.type === 'boxagent-preview-theme') {
          document.documentElement.dataset.theme = event.data.theme; window.messages++;
        }
      });
    </script><p>Isolated preview</p>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const window = new BrowserWindow({ width: 1200, height: 820, show: true, webPreferences: {
    partition: 'avatar-bridge-smoke', preload: path.join(__dirname, '../preload.cjs'),
    contextIsolation: true, sandbox: true, nodeIntegration: false
  } });
  const snapshot = { appearance: 'first', appearance_ready: true, scale: 1 };
  const calls = [];
  const timeout = setTimeout(() => { console.error('Avatar bridge timed out:', calls.map(call => call.method)); server.closeAllConnections(); server.close(); app.exit(1); }, 15000);
  ipcMain.handle('engine:errors', () => []);
  ipcMain.handle('engine:request', (_event, method, params = {}) => {
    calls.push({ method, params });
    switch (method) {
      case 'state': return { voice: 'off', task: 'idle' };
      case 'list_sessions': return [];
      case 'desktop_snapshot': return snapshot;
      case 'desktop_avatars': return { ...snapshot, items: [{ id: 'first', name: 'First' }, { id: 'second', name: 'Second' }] };
      case 'desktop_preview_avatar': return { url: `http://127.0.0.1:${server.address().port}/preview?id=${params.id}` };
      case 'desktop_select_avatar': snapshot.appearance = params.id; return snapshot;
      default: throw Error(method);
    }
  });
  const js = source => window.webContents.executeJavaScript(source);
  const wait = async condition => {
    for (let index = 0; index < 100; index++) {
      if (await condition()) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw Error('Avatar bridge fixture timed out');
  };
  const nav = async view => {
    await js(`document.querySelector('[data-view="${view}"]').click()`);
    await wait(() => js(`document.body.dataset.view === '${view}' && document.getElementById('content').getAttribute('aria-busy') === 'false'`));
  };
  const frame = () => window.webContents.mainFrame.frames.find(frame => frame.url.startsWith('http://127.0.0.1'));
  try {
    nativeTheme.themeSource = 'light';
    await window.loadFile(path.join(__dirname, '../dist/index.html'));
    await wait(() => js(`!document.getElementById('refresh').disabled`));
    await nav('settings'); await nav('appearance');
    await wait(() => frame()?.executeJavaScript(`document.documentElement.dataset.theme === 'light'`));
    const address = frame().url;
    nativeTheme.themeSource = 'dark';
    await wait(() => frame()?.executeJavaScript(`document.documentElement.dataset.theme === 'dark' && window.messages > 0`));
    assert.equal(frame().url, address);
    await js(`void document.querySelectorAll('.avatar-choice')[1].click()`);
    await wait(() => js(`document.querySelector('.avatar-stage iframe')?.src.includes('id=second')`));
    await new Promise(resolve => setTimeout(resolve, 200));
    await wait(() => frame()?.executeJavaScript(`new URL(location).searchParams.get('id') === 'second' && document.documentElement.dataset.theme === 'dark'`));
    assert.equal(snapshot.appearance, 'first');
    await js(`document.querySelector('.avatar-layout .primary').click()`);
    await wait(() => js(`document.querySelector('.avatar-choice.active small')?.textContent === '正在使用'`));
    assert.equal(snapshot.appearance, 'second');
    assert(calls.some(call => call.method === 'desktop_select_avatar' && call.params.id === 'second'));
    console.log('Avatar bridge: preview selection, initial theme, system theme messages without reload and explicit avatar application passed');
    clearTimeout(timeout);
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve)); app.quit();
  } catch (error) { console.error(error); server.close(); app.exit(1); }
});
