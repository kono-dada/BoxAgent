const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');
const { targetDisplay, petAnchor, petPlacement, primaryBubbleFrame } = require('../desktop/displays.cjs');
const sourceFile = path.join(__dirname, '../desktop/host.cjs');
const localRequire = createRequire(sourceFile);

function display(id, x, y, width, height, scaleFactor = 1) {
  return { id, label: `Display ${id}`, scaleFactor, bounds: { x, y, width, height }, workArea: { x, y, width, height } };
}
function inside(frame, area) {
  assert(frame.x >= area.x && frame.y >= area.y && frame.x + frame.width <= area.x + area.width &&
    frame.y + frame.height <= area.y + area.height, `${JSON.stringify(frame)} outside ${JSON.stringify(area)}`);
}
async function fixture(t, { preference, position, initialScale = 1,
  displays = [display(1, 0, 0, 1440, 900, 2), display(2, -480, -200, 480, 1920)] } = {}) {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'boxagent-displays-'));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  if (preference !== undefined) fs.writeFileSync(path.join(data, 'display.json'), preference);
  if (position) fs.writeFileSync(path.join(data, 'position.json'), JSON.stringify(position));
  const windows = [], screen = new EventEmitter(), calls = [];
  screen.displays = displays;
  screen.getAllDisplays = () => screen.displays;
  screen.getPrimaryDisplay = () => screen.displays[0];
  screen.getCursorScreenPoint = () => ({ x: 600, y: 300 });
  screen.getDisplayNearestPoint = point => screen.displays.reduce((best, item) => {
    const distance = ({ bounds: b }) => Math.hypot(Math.max(b.x - point.x, 0, point.x - b.x - b.width), Math.max(b.y - point.y, 0, point.y - b.y - b.height));
    return distance(item) < distance(best) ? item : best;
  });
  screen.getDisplayMatching = frame => screen.getDisplayNearestPoint({ x: frame.x + frame.width / 2, y: frame.y + frame.height / 2 });
  class FakeWindow extends EventEmitter {
    constructor(options) {
      super();
      this.bounds = { x: 600, y: 300, ...options };
      this.visible = false;
      this.webContents = new EventEmitter();
      this.webContents.id = windows.length + 1;
      this.webContents.send = () => {};
      windows.push(this);
    }
    loadFile() { return Promise.resolve(); }
    getBounds() { const { x, y, width, height } = this.bounds; return { x, y, width, height }; }
    setBounds(bounds) { Object.assign(this.bounds, bounds); }
    setPosition(x, y) { Object.assign(this.bounds, { x, y }); }
    setVisibleOnAllWorkspaces() {}
    setAlwaysOnTop() {}
    showInactive() { this.visible = true; }
    hide() { this.visible = false; }
    isVisible() { return this.visible; }
    isDestroyed() { return Boolean(this.destroyed); }
    destroy() { this.destroyed = true; this.emit('closed'); }
    static getFocusedWindow() { return null; }
  }
  class Tray extends EventEmitter { setToolTip() {} setContextMenu(menu) { this.menu = menu; } destroy() {} }
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(sourceFile, 'utf8'), { module, __dirname: path.dirname(sourceFile), console,
    process: { platform: 'darwin' },
    setTimeout, clearTimeout, setInterval, clearInterval, setImmediate,
    require(name) { return name === 'electron' ? { BrowserWindow: FakeWindow, screen, Tray,
      nativeImage: { createFromPath: () => ({ setTemplateImage() {} }) },
      Menu: { buildFromTemplate: items => ({ items, popup() {} }) },
      globalShortcut: { register: () => true, unregisterAll() {} }, Notification: { isSupported: () => false },
    } : localRequire(name); },
  });
  let scale = initialScale;
  const service = { request: async (method, params) => {
    calls.push(method);
    if (method === 'desktop_snapshot') return { scale, appearance: 'fixture', name: 'Fixture' };
    if (method === 'desktop_prepare_avatar') return { token: 'fixture', url: 'http://localhost/avatar' };
    if (method === 'desktop_set_pet_scale') { scale = params.scale; return { scale }; }
    return {};
  } };
  const host = new module.exports.DesktopHost({ app: {}, data, service, client: { request: async () => [] },
    broadcast() {}, registerWindow() {}, showPanel() {}, showCamera() {} });
  t.after(() => host.close());
  const starting = host.start();
  await new Promise(resolve => setImmediate(resolve));
  await host.report(host.pending.window.webContents, { type: 'ready' });
  await starting;
  host.showBubble();
  return { host, screen, data, calls };
}

test('select secondary keeps chat, task panels and notifications on primary', async t => {
  const { host, screen, calls } = await fixture(t);
  const chat = host.bubble.getBounds();
  const result = await host.request('desktop_set_pet_display', { id: '2' });
  assert.equal(result.pet_display_id, '2');
  assert.equal(result.separate_windows, true);
  assert.deepEqual(host.bubble.getBounds(), chat);
  const pet = host.current.window.getBounds();
  inside(pet, screen.displays[1].workArea);
  host.resizeBubble(host.bubble.webContents, 570);
  host.showTasks();
  host.hideBubble();
  host.stateChanged({ task: 'succeeded' }, { type: 'task.succeeded' });
  host.collapseTasks();
  host.showBubble();
  assert.deepEqual(host.current.window.getBounds(), pet);
  inside(host.bubble.getBounds(), screen.displays[0].workArea);
  assert(!calls.includes('desktop_set_pet_display'), 'window choice never goes to Python or voice');
});

function drag(host, window, dx, dy) {
  const frame = window.getBounds(), point = { x: frame.x + 20, y: frame.y + 20 };
  host.dragWindow(window.webContents, { phase: 'start', buttons: 1, point });
  host.dragWindow(window.webContents, { phase: 'move', buttons: 1, point: { x: point.x + dx, y: point.y + dy } });
  host.dragWindow(window.webContents, { phase: 'end', buttons: 0 });
}

test('split dragging is independent and constrained to the assigned screen', async t => {
  const { host, screen } = await fixture(t);
  host.setPetDisplay('2');
  host.showTasks();
  const chat = host.bubble.getBounds(), tasks = host.tasks.getBounds();
  drag(host, host.current.window, 5000, -400);
  assert.deepEqual(host.bubble.getBounds(), chat);
  assert.deepEqual(host.tasks.getBounds(), tasks);
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
  const pet = host.current.window.getBounds();
  drag(host, host.bubble, -5000, -200);
  assert.deepEqual(host.current.window.getBounds(), pet);
  inside(host.bubble.getBounds(), screen.displays[0].workArea);
  inside(host.tasks.getBounds(), screen.displays[0].workArea);
  drag(host, host.tasks, 80, 30);
  assert.deepEqual(host.current.window.getBounds(), pet);
});

test('unplug falls back to primary; reconnect restores target and updates menus', async t => {
  const { host, screen } = await fixture(t);
  const secondary = screen.displays[1];
  host.setPetDisplay('2');
  screen.displays = [screen.displays[0]];
  screen.emit('display-removed');
  assert.equal(host.sendDesktop().pet_display_id, '1');
  inside(host.current.window.getBounds(), screen.displays[0].workArea);
  assert(host.displayMenuItems().some(item => item.label === '未检测到扩展副屏' && item.enabled === false));
  screen.displays.push(secondary);
  screen.emit('display-added');
  assert.equal(host.sendDesktop().pet_display_id, '2');
  inside(host.current.window.getBounds(), secondary.workArea);
  inside(host.bubble.getBounds(), screen.displays[0].workArea);
});

test('portrait rotation, negative coordinates and scale changes preserve full viewport', async t => {
  const { host, screen } = await fixture(t);
  host.setPetDisplay('2');
  const chat = host.bubble.getBounds();
  await host.request('desktop_set_pet_scale', { scale: 6 });
  inside({ ...host.current.origin, ...host.current.viewport }, screen.displays[1].workArea);
  assert.deepEqual(host.bubble.getBounds(), chat);
  screen.displays[1] = display(2, 1440, 200, 1920, 480);
  screen.emit('display-metrics-changed');
  inside({ ...host.current.origin, ...host.current.viewport }, screen.displays[1].workArea);
  assert.deepEqual(host.bubble.getBounds(), chat);
});

test('display preference survives startup and avatar replacement', async t => {
  const { host, screen, data } = await fixture(t, { preference: JSON.stringify({ version: 1, target: '2' }) });
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
  const chat = host.bubble.getBounds();
  const loading = host.loadAvatar('replacement');
  await new Promise(resolve => setImmediate(resolve));
  await assert.rejects(host.request('desktop_set_pet_display', { id: '1' }), /加载/);
  await host.report(host.pending.window.webContents, { type: 'ready' });
  await loading;
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
  assert.deepEqual(host.bubble.getBounds(), chat);
  host.setPetDisplay('1');
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(data, 'display.json'), 'utf8')), { version: 1, target: 'primary' });
  assert.equal(host.sendDesktop().pet_display_id, '1');
});

test('selection saves the display name and startup upgrades a valid legacy preference', async t => {
  const { host, data } = await fixture(t, { preference: JSON.stringify({ version: 1, target: '2' }) });
  const saved = () => JSON.parse(fs.readFileSync(path.join(data, 'display.json'), 'utf8'));
  assert.deepEqual(saved(), { version: 1, target: '2', label: 'Display 2' });
  host.setPetDisplay(null);
  assert.deepEqual(saved(), { version: 1, target: null });
  assert.equal(host.displayLabel, '');
  host.setPetDisplay('2');
  assert.deepEqual(saved(), { version: 1, target: '2', label: 'Display 2' });
});

test('startup restores the named secondary when its ID changed', async t => {
  const secondary = { ...display(3, 1440, 0, 720, 1920), label: '12.3FHD' };
  const { host, screen } = await fixture(t, {
    preference: JSON.stringify({ version: 1, target: '2', label: '12.3FHD' }),
    displays: [display(1, 0, 0, 1440, 900, 2), secondary],
  });
  assert.equal(host.sendDesktop().pet_display_id, '3');
  inside(host.current.window.getBounds(), secondary.workArea);
  inside(host.bubble.getBounds(), screen.displays[0].workArea);
  await reportCrop(host);
  await host.request('desktop_set_pet_scale', { scale: 6 });
  inside(host.current.window.getBounds(), secondary.workArea);
  assert(host.displayMenuItems().some(item => item.checked && item.label.includes('12.3FHD')));
});

test('hotplug restores the named display after renumbering, including a pending avatar', async t => {
  const { host, screen } = await fixture(t);
  host.setPetDisplay('2');
  const loading = host.loadAvatar('replacement');
  await new Promise(resolve => setImmediate(resolve));
  screen.displays = [screen.displays[0]];
  screen.emit('display-removed');
  assert.equal(host.sendDesktop().pet_display_id, '1');
  const secondary = { ...display(7, 1440, 0, 720, 1920), label: 'Display 2' };
  screen.displays.push(secondary);
  screen.emit('display-added');
  assert.equal(host.sendDesktop().pet_display_id, '7');
  inside(host.current.window.getBounds(), secondary.workArea);
  inside(host.pending.window.getBounds(), secondary.workArea);
  await host.report(host.pending.window.webContents, { type: 'ready' });
  await loading;
  inside(host.current.window.getBounds(), secondary.workArea);
  inside(host.bubble.getBounds(), screen.displays[0].workArea);
});

test('a reused display ID cannot override the saved name; ambiguous or missing names fall back', () => {
  const primary = display(1, 0, 0, 1440, 900);
  const different = display(2, -1920, 0, 1920, 1080);
  const wanted = { ...display(3, 1440, 0, 720, 1920), label: '12.3FHD' };
  const duplicate = { ...display(4, 2160, 0, 720, 1920), label: '12.3FHD' };
  assert.equal(targetDisplay([primary, different, wanted], primary, '2', '12.3FHD'), wanted);
  assert.equal(targetDisplay([primary, different], primary, '2', '12.3FHD'), primary);
  assert.equal(targetDisplay([primary, different, wanted, duplicate], primary, '2', '12.3FHD'), primary);
  assert.equal(targetDisplay([primary, wanted, duplicate], primary, '3', '12.3FHD'), wanted);
  assert.equal(targetDisplay([primary, wanted], primary, 'primary', '12.3FHD'), primary);
  assert.equal(targetDisplay([primary, different], primary, '2'), different);
});

test('topology changes during avatar loading also move pending renderer', async t => {
  const { host, screen } = await fixture(t);
  host.setPetDisplay('2');
  const loading = host.loadAvatar('replacement');
  await new Promise(resolve => setImmediate(resolve));
  screen.displays = [screen.displays[0]];
  screen.emit('display-removed');
  inside(host.pending.window.getBounds(), screen.displays[0].workArea);
  await host.report(host.pending.window.webContents, { type: 'ready' });
  await loading;
  inside(host.current.window.getBounds(), screen.displays[0].workArea);
});

test('primary reassignment moves chat but retains selected physical pet display', async t => {
  const { host, screen } = await fixture(t);
  host.setPetDisplay('2');
  screen.displays = [display(3, 0, -900, 1440, 900), ...screen.displays];
  screen.emit('display-metrics-changed');
  inside(host.bubble.getBounds(), screen.displays[0].workArea);
  assert.equal(host.sendDesktop().pet_display_id, '2');
});

test('invalid choices are rejected; returning to together restores legacy linked drag', async t => {
  const { host, screen } = await fixture(t);
  for (const id of ['missing', 2, {}, undefined]) await assert.rejects(host.request('desktop_set_pet_display', { id }), /显示器/);
  host.setPetDisplay('2');
  host.setPetDisplay(null);
  assert.equal(host.sendDesktop().separate_windows, false);
  inside(host.current.window.getBounds(), screen.displays[0].workArea);
  const pet = host.current.window.getBounds(), chat = host.bubble.getBounds();
  drag(host, host.bubble, -80, -40);
  const delta = host.current.window.getBounds().x - pet.x;
  assert.notEqual(delta, 0);
  assert.equal(host.bubble.getBounds().x - chat.x, delta);
});

test('corrupt or null saved preferences safely keep existing same-screen behavior', async t => {
  for (const preference of ['{', 'null', JSON.stringify({ version: 1, target: {} })]) {
    const { host } = await fixture(t, { preference });
    assert.equal(host.displayTarget, null);
  }
});

test('shutdown removes display listeners', async t => {
  const { host, screen } = await fixture(t);
  assert.equal(screen.listenerCount('display-added'), 1);
  host.close();
  assert.equal(screen.listenerCount('display-added'), 0);
  assert.equal(screen.listenerCount('display-removed'), 0);
  assert.equal(screen.listenerCount('display-metrics-changed'), 0);
});

test('placement uses logical work area, not Retina pixels; chat fits resized primary', () => {
  const area = { x: -480, y: 24, width: 480, height: 1896 };
  const result = petPlacement({ origin: { x: 0, y: 0 } }, 6, area, true);
  inside({ ...result.origin, ...result.viewport }, area);
  assert.equal(result.viewport.width, 464);
  inside(primaryBubbleFrame({ x: 600, y: 300 }, { x: 0, y: 0, width: 400, height: 500 }, 600), { x: 0, y: 0, width: 400, height: 500 });
});

async function reportCrop(host, pet = host.current, width = .36, height = .74) {
  const viewport = pet.viewport;
  await host.report(pet.window.webContents, { type: 'bounds', viewport: [viewport.width, viewport.height], bounds: {
    x: Math.floor(viewport.width * (1 - width) / 2), y: Math.floor(viewport.height * .08),
    width: Math.floor(viewport.width * width), height: Math.floor(viewport.height * height),
  } });
}

test('measured portrait pet uses visible bounds, not transparent canvas, at maximum scale', async t => {
  const { host, screen } = await fixture(t);
  await reportCrop(host);
  host.setPetDisplay('2');
  const chat = host.bubble.getBounds();
  const small = host.current.window.getBounds();
  await host.request('desktop_set_pet_scale', { scale: 6 });
  await reportCrop(host);
  const large = host.current.window.getBounds();
  assert(large.height > small.height * 3, 'maximum scale actually enlarges the visible body');
  assert(large.width > 440, 'uses nearly all the 480px portrait width');
  assert(host.current.viewport.width > screen.displays[1].workArea.width, 'transparent viewport may extend beyond display');
  inside(large, screen.displays[1].workArea);
  assert.deepEqual(host.bubble.getBounds(), chat);
  drag(host, host.current.window, 5000, -5000);
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
  assert.deepEqual(host.bubble.getBounds(), chat);
  screen.displays[1] = display(2, 1440, 0, 1920, 480);
  screen.emit('display-metrics-changed');
  await reportCrop(host);
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
  await host.request('desktop_set_pet_scale', { scale: 2 });
  host.setPetDisplay(null);
  inside({ ...host.current.origin, ...host.current.viewport }, screen.displays[0].workArea);
});

test('first measured bounds calibrate saved maximum scale; all later animation reports leave the frame fixed', async t => {
  const { host, screen } = await fixture(t, { preference: JSON.stringify({ version: 1, target: '2' }) });
  await host.request('desktop_set_pet_scale', { scale: 6 });
  const before = { ...host.current.viewport };
  await reportCrop(host);
  assert(host.current.viewport.height > before.height * 2);
  const calibrated = { ...host.current.viewport };
  const fixedFrame = host.current.window.getBounds();
  await host.report(host.current.window.webContents, { type: 'bounds', viewport: [before.width, before.height],
    bounds: { x: 0, y: 0, ...before } });
  assert.deepEqual(host.current.viewport, calibrated);
  await reportCrop(host, host.current, .3, .7);
  assert.deepEqual(host.current.viewport, calibrated, 'smaller animation bounds do not zoom back in');
  await reportCrop(host, host.current, .65, .78);
  assert.deepEqual(host.current.viewport, calibrated, 'wider gestures cannot change first-frame sizing');
  assert.deepEqual(host.current.window.getBounds(), fixedFrame);
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
  const reduced = { ...host.current.viewport };
  await reportCrop(host, host.current, .3, .7);
  assert.deepEqual(host.current.viewport, reduced);
  const secondary = screen.displays.pop();
  screen.emit('display-removed');
  inside(host.current.window.getBounds(), screen.displays[0].workArea);
  screen.displays.push(secondary);
  screen.emit('display-added');
  inside(host.current.window.getBounds(), secondary.workArea);
});

test('split scaling and repeated screen changes retain the original crop without rounding drift', async t => {
  const { host, screen } = await fixture(t);
  await reportCrop(host);
  host.setPetDisplay('2');
  await host.request('desktop_set_pet_scale', { scale: 6 });
  const crop = { ...host.current.crop }, viewport = { ...host.current.viewport };
  for (let index = 0; index < 20; index++) {
    host.setPetDisplay('1');
    await host.request('desktop_set_pet_scale', { scale: 1.37 });
    host.setPetDisplay('2');
    await host.request('desktop_set_pet_scale', { scale: 6 });
  }
  assert.deepEqual(host.current.crop, crop);
  assert.deepEqual(host.current.viewport, viewport);
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
});

test('split resize and drag share geometry tracking for avatar, chat and task windows', async t => {
  const { host, screen } = await fixture(t);
  await reportCrop(host);
  host.setPetDisplay('2');
  await host.request('desktop_set_pet_scale', { scale: 6 });
  host.resizeBubble(host.bubble.webContents, 570);
  host.showTasks();
  for (const window of [host.current.window, host.bubble, host.tasks]) {
    const before = window.getBounds();
    assert.deepEqual(host.geometry.getBounds(window), before, 'resizes update requested DIP sizes');
    for (let index = 0; index < 10; index++) drag(host, window, -5, -3);
    assert.equal(window.getBounds().width, before.width);
    assert.equal(window.getBounds().height, before.height);
  }
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
  inside(host.bubble.getBounds(), screen.displays[0].workArea);
});

test('avatar actions and music survive camera state updates while the avatar is on secondary', async t => {
  const { host } = await fixture(t);
  host.setPetDisplay('2');
  const frame = host.current.window.getBounds();
  let sent;
  host.current.window.webContents.send = (channel, payload) => { if (channel === 'desktop:pet') sent = payload; };
  host.musicActive = true;
  host.stateChanged({ avatar_action: 'dance', avatar_action_id: 'action-1', speaking: true });
  host.stateChanged({ camera_status: 'active', voice: 'ready' });
  assert.equal(sent.action, 'dance');
  assert.equal(sent.actionId, 'action-1');
  assert.equal(sent.music, true);
  assert.equal(sent.state, 'speaking');
  host.stateChanged({ user_speaking: true });
  assert.equal(sent.state, 'idle');
  assert.deepEqual(host.current.window.getBounds(), frame);
});

test('visible scaling preserves the feet and horizontal center when there is room', () => {
  const pet = { hasBounds: true, viewport: { width: 252, height: 182 },
    origin: { x: 500, y: 500 }, crop: { x: 80, y: 30, width: 90, height: 136 } };
  const result = petPlacement(pet, 2, { x: 0, y: 0, width: 1440, height: 900 });
  assert.equal(result.origin.y + result.crop.y + result.crop.height, pet.origin.y + pet.crop.y + pet.crop.height);
  assert.equal(result.origin.x + result.crop.x + result.crop.width / 2, pet.origin.x + pet.crop.x + pet.crop.width / 2);
});

test('restart and avatar replacement preserve the visible anchor of an enlarged pet', async t => {
  const preference = JSON.stringify({ version: 1, target: '2' });
  const first = await fixture(t, { preference, initialScale: 6 });
  await reportCrop(first.host);
  drag(first.host, first.host.current.window, 0, -400);
  const position = JSON.parse(fs.readFileSync(path.join(first.data, 'position.json'), 'utf8'));
  assert.deepEqual(position.anchor, petAnchor(first.host.current));
  const { host, data } = await fixture(t, { preference, initialScale: 6, position });
  await reportCrop(host);
  const restored = petAnchor(host.current);
  assert(Math.abs(restored.x - position.anchor.x) <= 1);
  assert(Math.abs(restored.y - position.anchor.y) <= 1);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(data, 'position.json'), 'utf8')).anchor, restored);
  const loading = host.loadAvatar('replacement');
  await new Promise(resolve => setImmediate(resolve));
  await reportCrop(host, host.pending, .4, .68);
  await host.report(host.pending.window.webContents, { type: 'ready' });
  await loading;
  const replaced = petAnchor(host.current);
  assert(Math.abs(replaced.x - restored.x) <= 1);
  assert(Math.abs(replaced.y - restored.y) <= 1);
});
