const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { EngineClient } = require('./engine-client.cjs');
const data = path.resolve(process.env.BOXAGENT_DATA_DIR || path.join(__dirname, '../../.runtime/pet'));
const methods = new Set(['state', 'list_sessions', 'context_snapshot', 'context_entry', 'session_timeline', 'memory_snapshot']);
let client, window;
app.whenReady().then(() => {
  window = new BrowserWindow({ width: 1200, height: 820, minWidth: 740, minHeight: 560,
    title: '伙伴 · 会话管理', titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 16, y: 15 },
    backgroundColor: '#ffffff',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  client = new EngineClient(path.join(data, 'engine.sock'));
  let reconnect, disposed = false;
  client.on('state', state => window?.webContents.send('engine:state', state));
  client.on('online', () => window?.webContents.send('engine:diagnostic', 'Engine 已连接'));
  client.on('diagnostic', message => window?.webContents.send('engine:diagnostic', message));
  client.on('offline', () => {
    window?.webContents.send('engine:diagnostic', 'Engine 已断开');
    if (!disposed) reconnect = setTimeout(() => client.connect(), 2000);
  });
  ipcMain.handle('engine:request', (_event, method, params) => {
    if (!methods.has(method)) throw new Error('面板只允许读取');
    return client.request(method, params);
  });
  ipcMain.handle('engine:errors', async () => {
    const file = path.join(data, 'logs/errors.jsonl');
    try {
      const handle = await fs.promises.open(file, 'r');
      try { const size = (await handle.stat()).size; const buffer = Buffer.alloc(Math.min(size, 128000));
        await handle.read(buffer, 0, buffer.length, Math.max(0, size - buffer.length));
        return buffer.toString().split('\n').flatMap(line => { try { return [JSON.parse(line)]; } catch { return []; } }).slice(-100);
      } finally { await handle.close(); }
    } catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  });
  window.on('closed', () => { disposed = true; clearTimeout(reconnect); client.removeAllListeners(); client.close(); window = null; });
  window.loadFile(path.join(__dirname, 'index.html')); client.connect();
  if (process.argv.includes('--smoke')) {
    window.webContents.once('did-finish-load', async () => {
      try { await require('./tests/smoke.cjs').runSmoke(window); app.quit(); }
      catch (error) { console.error(error); app.exit(1); }
    });
  }
});
app.on('window-all-closed', () => app.quit());
