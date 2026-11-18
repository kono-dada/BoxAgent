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
    window.on('focus', () => {
      if (coordinator.busy) coordinator.emit();
      else void coordinator.request('refresh-permissions').catch(() => {});
    });
    return window;
  };
  ipcMain.handle('setup:request', async (event, method, params) => {
    if (!window || window.isDestroyed() || event.sender !== window.webContents ||
        typeof method !== 'string' || (params !== undefined && (!params || typeof params !== 'object' || Array.isArray(params))))
      throw Error('untrusted_setup_request');
    if (method === 'restart-for-permissions') {
      if (coordinator.busy) throw Error('setup_busy');
      setImmediate(() => { app.relaunch(); app.quit(); });
      return coordinator.snapshot();
    }
    if (method === 'choose-tool') {
      if (!dialog || !['codex', 'computer-use'].includes(params?.kind)) throw Error('invalid_setup_method');
      const selected = await dialog.showOpenDialog(window, { properties: ['openDirectory', 'treatPackageAsDirectory'] });
      if (selected.canceled) return coordinator.snapshot();
      const patch = await require('./desktop-tools.cjs').chooseDesktopTool(params.kind, selected.filePaths[0]);
      return coordinator.request('save', patch);
    }
    if (method === 'wechat-choose-client' || method === 'wechat-choose-account') {
      if (!dialog || coordinator.busy) throw Error('setup_busy');
      const account = method === 'wechat-choose-account';
      const selected = await dialog.showOpenDialog(window, account ? {
        title: '选择微信副本的 db_storage 文件夹',
        defaultPath: path.join(app.getPath('home'), 'Library/Containers/com.tencent.xinWeChatBoxAgent/Data/Documents/xwechat_files'),
        properties: ['openDirectory'],
      } : { title: '选择微信 4.1.15（build 270115）', defaultPath: '/Applications/WeChat.app',
        properties: ['openFile'], filters: [{ name: '微信应用或安装包', extensions: ['app', 'dmg'] }] });
      if (selected.canceled || selected.filePaths.length !== 1) return coordinator.snapshot();
      return coordinator.request(account ? 'wechat-account' : 'wechat-prepare',
        account ? { root: selected.filePaths[0] } : { source: selected.filePaths[0] });
    }
    if (method === 'wechat-developer-tools') {
      await require('./process.cjs').run('/usr/bin/xcode-select', ['--install'], { timeout: 20000 });
      return coordinator.snapshot();
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
    // File paths come from the native picker above, never renderer-provided paths.
    if (['wechat-prepare', 'wechat-account'].includes(method)) throw Error('invalid_setup_method');
    const result = await coordinator.request(method, params);
    if (method === 'open-logs') shell.showItemInFolder(path.join(coordinator.paths.data, 'logs'));
    if (method === 'complete' && result.onboarding.completed && !result.error) window?.close();
    if (method === 'finish-permission-review' && !result.error) window?.close();
    return result;
  });
  return { show, hide: () => window?.hide(), send: snapshot => { if (window && !window.isDestroyed()) window.webContents.send('setup:state', snapshot); } };
}
module.exports = { createSetupWindow };
