const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { EngineClient } = require('./engine-client.cjs');
const data = path.resolve(process.env.BOXAGENT_DATA_DIR || path.join(__dirname, '../../.runtime/pet'));
const methods = new Set(['state', 'list_sessions', 'context_snapshot', 'context_entry', 'session_timeline', 'session_events', 'memory_snapshot', 'delete_memory_node', 'create_session', 'activate_session', 'archive_session', 'toggle_voice', 'toggle_context', 'submit_text', 'cancel_task', 'answer_approval', 'list_skills', 'create_skill', 'update_skill', 'set_skill_enabled', 'delete_skill']);
const desktopMethods = new Set(['desktop_snapshot','desktop_toggle_bubble','desktop_avatars','desktop_preview_avatar','desktop_select_avatar','desktop_persona','desktop_save_persona','desktop_runs','desktop_open_run','desktop_open_logs','desktop_quit']);
let client, desktop, window;
if (!process.argv.includes('--smoke') && !app.requestSingleInstanceLock()) app.quit();
app.on('second-instance', () => { window?.show(); window?.focus(); });
app.whenReady().then(() => {
  window = new BrowserWindow({ width: 1200, height: 820, minWidth: 740, minHeight: 560,
    title: '伙伴 · 控制台', titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 16, y: 15 },
    backgroundColor: '#ffffff',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  client = new EngineClient(path.join(data, 'engine.sock'));
  desktop = new EngineClient(path.join(data, 'desktop.sock'));
  let desktopReconnect;
  desktop.on('diagnostic', () => {});
  desktop.on('offline', () => { if (!disposed) desktopReconnect = setTimeout(() => desktop.connect(), 2000); });
  let reconnect, disposed = false;
  client.on('state', state => window?.webContents.send('engine:state', state));
  client.on('online', () => window?.webContents.send('engine:diagnostic', 'Engine 已连接'));
  client.on('diagnostic', message => window?.webContents.send('engine:diagnostic', message));
  client.on('offline', () => {
    window?.webContents.send('engine:diagnostic', 'Engine 已断开');
    if (!disposed) reconnect = setTimeout(() => client.connect(), 2000);
  });
  ipcMain.handle('engine:request', (_event, method, params) => {
    if (desktopMethods.has(method)) return desktop.request(method, params);
    if (!methods.has(method)) throw new Error('面板未提供此操作');
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
  window.on('closed', () => { disposed = true; clearTimeout(reconnect); clearTimeout(desktopReconnect); client.removeAllListeners(); desktop.removeAllListeners(); client.close(); desktop.close(); window = null; });
  window.loadFile(path.join(__dirname, 'dist/index.html')); client.connect(); desktop.connect();
  if (process.argv.includes('--smoke')) {
    window.webContents.once('did-finish-load', async () => {
      try { await require('./tests/smoke.cjs').runSmoke(window); app.quit(); }
      catch (error) { console.error(error); app.exit(1); }
    });
  }
});
app.on('window-all-closed', () => app.quit());
