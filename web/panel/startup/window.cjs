const path = require('node:path');

function createSetupWindow({ app, BrowserWindow, ipcMain, panelRoot, registerWindow, coordinator, shell, dialog }) {
  let window;
  const show = () => {
    if (window && !window.isDestroyed()) { window.show(); window.focus(); return window; }
    window = new BrowserWindow({ width: 500, height: 570, useContentSize: true, resizable: false, maximizable: false, fullscreenable: false, title: 'BoxAgent',
      titleBarStyle: 'hiddenInset', backgroundColor: '#00000000',
      ...(process.platform === 'darwin' ? { vibrancy: 'hud', visualEffectState: 'active' } : {}),
      webPreferences: { preload: path.join(panelRoot, 'startup/preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
    registerWindow(window);
    window.loadFile(path.join(panelRoot, 'dist/setup.html'));
    window.on('closed', () => { window = null; if (!coordinator.state.completed) app.quit(); });
    window.on('focus', () => coordinator.emit());
    return window;
  };
  ipcMain.handle('setup:request', async (event, method, params) => {
    if (!window || window.isDestroyed() || event.sender !== window.webContents ||
        typeof method !== 'string' || (params !== undefined && (!params || typeof params !== 'object' || Array.isArray(params))))
      throw Error('untrusted_setup_request');
    if (method === 'choose-tool') {
      if (!dialog || !['codex', 'computer-use'].includes(params?.kind)) throw Error('invalid_setup_method');
      const selected = await dialog.showOpenDialog(window, { properties: ['openDirectory', 'treatPackageAsDirectory'] });
      if (selected.canceled) return coordinator.snapshot();
      const patch = await require('./desktop-tools.cjs').chooseDesktopTool(params.kind, selected.filePaths[0]);
      return coordinator.request('save', patch);
    }
    if (method === 'official-tools') {
      await shell.openExternal('https://github.com/openai/codex/releases');
      return coordinator.snapshot();
    }
    if (method === 'provider-help') {
      const urls = { dashscope: 'https://bailian.console.aliyun.com/', deepseek: 'https://platform.deepseek.com/api_keys' };
      if (!Object.hasOwn(urls, params?.kind)) throw Error('invalid_setup_method');
      await shell.openExternal(urls[params.kind]);
      return coordinator.snapshot();
    }
    const result = await coordinator.request(method, params);
    if (method === 'open-logs') shell.showItemInFolder(path.join(coordinator.paths.data, 'logs'));
    if (method === 'complete' && result.onboarding.completed && !result.error) window?.close();
    return result;
  });
  return { show, hide: () => window?.hide(), send: snapshot => { if (window && !window.isDestroyed()) window.webContents.send('setup:state', snapshot); } };
}
module.exports = { createSetupWindow };
