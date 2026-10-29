const { BrowserWindow, session, net, systemPreferences } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const origin = 'boxagent-camera://app';

function assetPath(url, root) {
  const parsed = new URL(url);
  if (parsed.protocol !== 'boxagent-camera:' || parsed.host !== 'app') return null;
  const pathname = decodeURIComponent(parsed.pathname);
  if (pathname === '/camera.html') return path.join(root, 'web/panel/dist/camera.html');
  const parts = pathname.split('/');
  if (parts.length !== 3) return null;
  const [, folder, name] = parts;
  if (folder === 'assets' && /^[a-zA-Z0-9_-]+\.(?:js|css)$/.test(name)) return path.join(root, 'web/panel/dist/assets', name);
  return null;
}

class CameraWindow {
  constructor({ root, client, registerWindow, onState = () => {} }) {
    Object.assign(this, { root, client, registerWindow, onState });
    this.window = null;
    this.token = '';
    this.allowed = false;
    this.generation = 0;
    this.requested = false;
    this.pendingStart = false;
    this.rendererReady = false;
    this.error = '';
    this.session = session.fromPartition('boxagent-camera');
    this.session.protocol.handle('boxagent-camera', async (request) => {
      try {
        const file = assetPath(request.url, root);
        if (request.method !== 'GET' || !file || !fs.existsSync(file)) return new Response('Not found', { status: 404 });
        return net.fetch(pathToFileURL(file).toString());
      } catch { return new Response('Invalid asset', { status: 400 }); }
    });
    const trusted = (contents) => this.allowed && contents === this.window?.webContents
      && contents.getURL() === `${origin}/camera.html`;
    this.session.setPermissionCheckHandler((contents, permission, _origin, details) =>
      trusted(contents) && permission === 'media' && details.mediaType === 'video');
    this.session.setPermissionRequestHandler((contents, permission, callback, details) =>
      callback(trusted(contents) && permission === 'media' && details.mediaTypes?.length === 1
        && details.mediaTypes[0] === 'video'));
  }
  snapshot() {
    return { status: this.token ? 'on' : this.requested ? 'starting' : 'off', error: this.error };
  }
  async toggle() {
    if (this.requested || this.token) {
      const window = this.window;
      const stopped = this.stop();
      this.window = null;
      this.rendererReady = false;
      // Destroy the renderer immediately, even while an AOQ start/stop is pending.
      if (window && !window.isDestroyed()) window.close();
      await stopped;
    } else {
      this.requested = true;
      this.pendingStart = true;
      this.error = '';
      this.onState(this.snapshot());
      try {
        this.show();
        if (this.rendererReady) {
          this.pendingStart = false;
          this.window.webContents.send('camera:start');
        }
      } catch (error) {
        await this.stop(String(error.message || error));
        throw error;
      }
    }
    return this.snapshot();
  }
  show() {
    if (this.window && !this.window.isDestroyed()) { this.window.show(); this.window.focus(); return; }
    const window = new BrowserWindow({
      title: 'BoxAgent · 摄像头', width: 400, height: 480, minWidth: 320, minHeight: 360,
      ...(process.platform === 'darwin' ? {
        titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 14, y: 16 },
      } : {}),
      backgroundColor: '#242424', webPreferences: {
        preload: path.join(__dirname, 'preload.cjs'), session: this.session,
        sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false,
      },
    });
    this.window = window;
    this.rendererReady = false;
    this.registerWindow(window);
    window.on('closed', () => {
      if (this.window !== window) return;
      this.window = null;
      this.rendererReady = false;
      void this.stop();
    });
    window.webContents.on('render-process-gone', () => { if (this.window === window) void this.stop(); });
    window.webContents.on('will-navigate', () => { if (this.window === window) void this.stop(); });
    let initialLoad = true;
    window.webContents.on('did-start-loading', () => {
      if (this.window !== window) return;
      this.rendererReady = false;
      if (initialLoad) { initialLoad = false; return; }
      void this.stop();
    });
    void window.loadURL(`${origin}/camera.html`).catch((error) => {
      if (this.window === window) void this.stop(String(error.message || error));
    });
  }
  async request(event, method, payload = {}) {
    if (event.sender !== this.window?.webContents || event.senderFrame !== event.sender.mainFrame)
      throw Error('Camera request from untrusted window');
    if (method === 'ready') {
      this.rendererReady = true;
      const start = this.pendingStart;
      this.pendingStart = false;
      return { start, pinned: this.window.isAlwaysOnTop() };
    }
    if (method === 'pin') {
      if (typeof payload.enabled !== 'boolean') throw Error('Invalid pin state');
      this.window.setAlwaysOnTop(payload.enabled);
      return { pinned: this.window.isAlwaysOnTop() };
    }
    if (method === 'authorize') {
      const generation = ++this.generation;
      this.requested = true;
      this.error = '';
      this.onState(this.snapshot());
      if (process.platform === 'darwin' && !(await systemPreferences.askForMediaAccess('camera')))
        throw Error('摄像头权限未开启，请在 macOS 系统设置 → 隐私与安全性 → 摄像头中允许 BoxAgent（开发模式为 Electron）');
      if (generation !== this.generation || !this.window) throw Error('Camera request cancelled');
      if (process.platform === 'darwin' && !(await systemPreferences.askForMediaAccess('microphone')))
        throw Error('麦克风权限未开启，请在 macOS 隐私与安全性设置中允许 BoxAgent');
      if (generation !== this.generation || !this.window) throw Error('Camera request cancelled');
      this.allowed = true;
      return { authorized: true };
    }
    if (method === 'start') {
      if (!this.allowed || this.token) throw Error('Camera not authorized or already active');
      const generation = this.generation;
      const result = await this.client.request('camera_start');
      if (generation !== this.generation || !this.window) {
        await this.client.request('camera_stop', { token: result.token });
        throw Error('Camera start cancelled');
      }
      this.token = result.token;
      this.onState(this.snapshot());
      return { started: true, revision: result.revision };
    }
    if (method === 'stop') {
      await this.stop(typeof payload.error === 'string' ? payload.error.slice(0, 500) : '');
      return { stopped: true };
    }
    if (method === 'frame') {
      if (!this.allowed || !this.token) throw Error('Camera is stopped');
      if (typeof payload.image !== 'string' || payload.image.length > 256000)
        throw Error('摄像头画面过大，已停止上传');
      // Camera-only IPC. API credentials and generic engine methods stay out of this renderer.
      return this.client.request('camera_frame', {
        token: this.token, sequence: payload.sequence, captured_at: payload.captured_at,
        image: payload.image,
      });
    }
    throw Error('Unknown camera command');
  }
  async stop(error = '') {
    this.allowed = false;
    this.requested = false;
    this.pendingStart = false;
    this.error = error;
    ++this.generation;
    const token = this.token;
    this.token = '';
    this.onState(this.snapshot());
    if (token) await this.client.request('camera_stop', { token }).catch(() => {});
  }
  disconnected() {
    void this.stop();
    this.window?.webContents.send('camera:disconnected');
  }
}
module.exports = { CameraWindow, assetPath };
