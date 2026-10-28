const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const source = fs.readFileSync(path.join(__dirname, '../camera/window.cjs'), 'utf8');
function fixture({ askPermission = async () => true, response = async () => ({ token: 'capture-one' }) } = {}) {
  const handlers = {}, requests = [], states = [], windows = [];
  class FakeWindow extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.pinned = false;
      this.destroyed = false;
      this.webContents = new EventEmitter();
      this.webContents.getURL = () => 'boxagent-camera://app/camera.html';
      this.webContents.mainFrame = {};
      this.sent = [];
      this.webContents.send = (...args) => this.sent.push(args);
      windows.push(this);
    }
    loadURL() { this.webContents.emit('did-start-loading'); return Promise.resolve(); }
    show() {}
    focus() {}
    isAlwaysOnTop() { return this.pinned; }
    setAlwaysOnTop(enabled) { this.pinned = enabled; }
    isDestroyed() { return this.destroyed; }
    close() { this.destroyed = true; this.emit('closed'); }
  }
  const session = { protocol: { handle() {} },
    setPermissionCheckHandler(fn) { handlers.check = fn; },
    setPermissionRequestHandler(fn) { handlers.request = fn; } };
  const module = { exports: {} };
  vm.runInNewContext(source, { module, URL, Response, __dirname: path.join(__dirname, '../camera'), process,
    require(name) { if (name === 'electron') return { BrowserWindow: FakeWindow, session: { fromPartition: () => session }, systemPreferences: { askForMediaAccess: askPermission } }; return require(name); } });
  const camera = new module.exports.CameraWindow({ root: '/repo', registerWindow() {}, onState: state => states.push(state), client: { request: async (method, params) => { requests.push({ method, params }); return response(method); } } });
  const sender = { getURL: () => 'boxagent-camera://app/camera.html', mainFrame: {} };
  camera.window = { webContents: sender, isAlwaysOnTop: () => false };
  return { camera, handlers, requests, states, windows, sender, event: { sender, senderFrame: sender.mainFrame }, assetPath: module.exports.assetPath };
}
test('camera assets cannot escape the bundle or expose arbitrary local files', () => {
  const { assetPath } = fixture();
  assert.equal(assetPath('boxagent-camera://app/camera.html', '/repo'), '/repo/web/panel/dist/camera.html');
  assert.equal(assetPath('boxagent-camera://app/models/gesture_recognizer.task', '/repo'), null);
  for (const url of ['file:///etc/passwd', 'boxagent-camera://evil/camera.html', 'boxagent-camera://app/models/../../.env.local',
    'boxagent-camera://app/assets/%2e%2e%2fsecret.js', 'boxagent-camera://app/assets/foo.json']) assert.equal(assetPath(url, '/repo'), null);
});

test('chat toggle starts once after renderer readiness; ordinary open and reload never auto-start', async () => {
  const { camera, windows } = fixture();
  camera.window = null;
  assert.equal((await camera.toggle()).status, 'starting');
  const sender = windows[0].webContents;
  const event = { sender, senderFrame: sender.mainFrame };
  assert.equal((await camera.request(event, 'ready')).start, true);
  assert.equal((await camera.request(event, 'ready')).start, false);
  sender.emit('did-start-loading');
  assert.equal(camera.snapshot().status, 'off');
  assert.equal((await camera.request(event, 'ready')).start, false);
  await camera.toggle();
  assert.deepEqual(windows[0].sent, [['camera:start']], 'ready window receives one explicit start');
  await camera.toggle();
  assert.equal(windows[0].destroyed, true);
  camera.show();
  const opened = windows[1].webContents;
  assert.equal((await camera.request({ sender: opened, senderFrame: opened.mainFrame }, 'ready')).start, false);
});

test('switching off while permissions are pending cancels start and never starts audio', { skip: process.platform !== 'darwin' }, async () => {
  let grant;
  const permission = new Promise(resolve => { grant = resolve; });
  const { camera, windows, requests } = fixture({ askPermission: () => permission });
  camera.window = null;
  await camera.toggle();
  const sender = windows[0].webContents, event = { sender, senderFrame: sender.mainFrame };
  await camera.request(event, 'ready');
  const authorization = camera.request(event, 'authorize');
  const rejected = assert.rejects(authorization, /cancelled/);
  await camera.toggle();
  grant(true);
  await rejected;
  assert.equal(camera.allowed, false);
  assert.equal(camera.snapshot().status, 'off');
  assert.equal(requests.length, 0);
});

test('late audio connection after switch-off is stopped using its own token', async () => {
  let finish;
  const started = new Promise(resolve => { finish = resolve; });
  const { camera, windows, requests } = fixture({ response: method => method === 'camera_start' ? started : Promise.resolve({}) });
  camera.window = null;
  await camera.toggle();
  const sender = windows[0].webContents, event = { sender, senderFrame: sender.mainFrame };
  await camera.request(event, 'ready');
  await camera.request(event, 'authorize');
  const starting = camera.request(event, 'start');
  const rejected = assert.rejects(starting, /cancelled/);
  await camera.toggle();
  finish({ token: 'late-token' });
  await rejected;
  assert.equal(requests.at(-1).method, 'camera_stop');
  assert.equal(requests.at(-1).params.token, 'late-token');
  assert.equal(camera.snapshot().status, 'off');
  assert.equal(camera.token, '');
});

test('error resets the switch and old renderer cannot start again after close', async () => {
  const { camera, windows, states } = fixture();
  camera.window = null;
  await camera.toggle();
  const sender = windows[0].webContents, event = { sender, senderFrame: sender.mainFrame };
  await camera.request(event, 'stop', { error: 'permission denied' });
  assert.equal(states.at(-1).status, 'off');
  assert.equal(states.at(-1).error, 'permission denied');
  await camera.request(event, 'ready');
  await camera.toggle();
  await camera.toggle();
  await assert.rejects(() => camera.request(event, 'authorize'), /untrusted/);
});
test('media permission is video-only and scoped to the explicitly enabled camera window', () => {
  const { camera, handlers, sender } = fixture();
  assert.equal(handlers.check(sender, 'media', '', { mediaType: 'video' }), false);
  camera.allowed = true;
  assert.equal(handlers.check(sender, 'media', '', { mediaType: 'video' }), true);
  assert.equal(handlers.check(sender, 'media', '', { mediaType: 'audio' }), false);
  assert.equal(handlers.check(null, 'media', '', { mediaType: 'video' }), false);
  for (const mediaTypes of [['audio'], ['video', 'audio'], []])
    handlers.request(sender, 'media', (allowed) => assert.equal(allowed, false), { mediaTypes });
});
test('IPC allows only bounded frames, rejects other windows; stop revokes the token', async () => {
  const { camera, event, requests } = fixture();
  await assert.rejects(() => camera.request({ sender: {}, senderFrame: {} }, 'start'));
  camera.allowed = true;
  await camera.request(event, 'start');
  await camera.request(event, 'frame', { sequence: 1, captured_at: 10, image: 'jpeg', unexpected: 'private' });
  assert(!('unexpected' in requests.at(-1).params));
  assert.equal(requests.at(-1).params.image, 'jpeg');
  await assert.rejects(() => camera.request(event, 'frame', { image: 'x'.repeat(256001) }));
  await camera.stop();
  assert.equal(camera.allowed, false);
  assert.equal(requests.at(-1).method, 'camera_stop');
  await assert.rejects(() => camera.request(event, 'frame'));
});

test('start returns the event revision without exposing the capture token to the renderer', async () => {
  const { camera, event } = fixture({ response: async () => ({ token: 'private-token', revision: 42 }) });
  camera.allowed = true;
  const result = await camera.request(event, 'start');
  assert.equal(result.started, true);
  assert.equal(result.revision, 42);
  assert(!('token' in result));
});

test('compact window and pin control do not authorize camera or touch voice', async () => {
  const { camera, windows, requests } = fixture();
  camera.window = null;
  camera.show();
  const window = windows[0], sender = window.webContents;
  const event = { sender, senderFrame: sender.mainFrame };
  assert.equal(window.options.width, 400);
  assert.equal(window.options.height, 480);
  assert.equal(window.options.minWidth, 320);
  if (process.platform === 'darwin') {
    assert.equal(window.options.titleBarStyle, 'hiddenInset');
    assert.equal(window.options.trafficLightPosition.x, 14);
    assert.equal(window.options.trafficLightPosition.y, 16);
  }
  assert.equal((await camera.request(event, 'ready')).pinned, false);
  assert.equal((await camera.request(event, 'pin', { enabled: true })).pinned, true);
  assert.equal((await camera.request(event, 'ready')).pinned, true);
  assert.equal((await camera.request(event, 'pin', { enabled: false })).pinned, false);
  await assert.rejects(() => camera.request(event, 'pin', { enabled: 'true' }), /Invalid/);
  await assert.rejects(() => camera.request({ sender: {}, senderFrame: {} }, 'pin', { enabled: true }), /untrusted/);
  assert.equal(camera.allowed, false);
  assert.equal(requests.length, 0);
});
