const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');
const { targetDisplay, petAnchor, petPlacement, displayBubbleFrame } = require('../desktop/displays.cjs');
const { bubbleFrame } = require('../desktop/layout.cjs');
const sourceFile = path.join(__dirname, '../desktop/host.cjs');
const localRequire = createRequire(sourceFile);

function display(id, x, y, width, height, scaleFactor = 1) {
  return { id, label: `Display ${id}`, scaleFactor, bounds: { x, y, width, height }, workArea: { x, y, width, height } };
}
function inside(frame, area) {
  assert(frame.x >= area.x && frame.y >= area.y && frame.x + frame.width <= area.x + area.width &&
    frame.y + frame.height <= area.y + area.height, `${JSON.stringify(frame)} outside ${JSON.stringify(area)}`);
}

test('desktop chat follows the shoulder and adapts near the screen edge', () => {
  const area = { x: 0, y: 30, width: 1470, height: 890 };
  const avatar = { x: 815, y: 130, width: 451, height: 540 };
  const shoulder = { left: 1036, right: 1176, y: 290 };
  const chat = bubbleFrame(avatar, area, null, 228, shoulder);
  assert.deepEqual(chat, { x: 618, y: 262, width: 414, height: 262 });
  assert.equal(bubbleFrame(avatar, area, null, 300, shoulder).y, chat.y);
  const nearLeft = { left: 106, right: 226, y: 290 };
  assert.equal(bubbleFrame({ ...avatar, x: 25 }, area, null, 300, nearLeft).x, 230);
  const narrow = { ...area, width: 480 };
  const inMiddle = { x: 160, y: 400, width: 160, height: 300 };
  const stacked = bubbleFrame(inMiddle, narrow, null, 300, { left: 180, right: 300, y: 470 });
  assert(stacked.y + stacked.height < inMiddle.y);
  inside(stacked, narrow);
});
async function fixture(t, { preference, position, initialScale = 1, engineOnline = true,
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
    getBounds() {
      if (this.destroyed) throw new TypeError('Object has been destroyed');
      const { x, y, width, height } = this.bounds;
      return { x, y, width, height };
    }
    setBounds(bounds) { Object.assign(this.bounds, bounds); }
    setPosition(x, y) { Object.assign(this.bounds, { x, y }); }
    setVisibleOnAllWorkspaces() {}
    setAlwaysOnTop(active, level, offset) { this.topLevel = [active, level, offset]; }
    setSimpleFullScreen(value) { this.simpleFullScreen = value; }
    isSimpleFullScreen() { return Boolean(this.simpleFullScreen); }
    setIgnoreMouseEvents(ignore) { this.ignoreMouseEvents = ignore; }
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
  const host = new module.exports.DesktopHost({ app: {}, data, service,
    client: { online: engineOnline, request: async method => {
      assert(engineOnline, 'offline startup waits for Engine events instead of requesting state');
      return method === 'state' ? { voice: 'off', task: 'idle' } : [];
    } },
    wakeAllowed: () => false,
    broadcast() {}, registerWindow() {}, showPanel() {}, showCamera() {} });
  t.after(() => host.close());
  const starting = host.start();
  await new Promise(resolve => setImmediate(resolve));
  await host.report(host.pending.window.webContents, { type: 'ready' });
  await starting;
  host.showBubble();
  return { host, screen, data, calls };
}

test('desktop startup restores Engine state after the ready event was already emitted', async t => {
  const { host } = await fixture(t);
  assert.equal(host.state.voice, 'off');
  assert.equal(host.engineReady, true);
});

test('only the game dock rises above the active LoL fullscreen layer', async t => {
  const { host } = await fixture(t);
  const request = host.service.request;
  let observed = { game: 'lol', bounds: { x: 0, y: 0, width: 1440, height: 900 }, shield_offset: 2147482629 };
  host.service.request = (method, params) => method === 'desktop_foreground_game'
    ? Promise.resolve(observed) : request(method, params);
  host.stateChanged({ lol_companion_enabled: true }, { type: 'lol.companion' });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(host.gameDock.topLevel, [true, 'screen-saver', 2147482629]);
  assert.deepEqual(host.bubble.topLevel, [true, 'floating', 2]);
  assert(host.gameDock.isVisible());
  observed = null;
  await host.refreshGameDisplay();
  assert(!host.gameDock.isVisible());
  assert.deepEqual(host.gameDock.topLevel, [true, 'floating', 2]);
});

test('stationary pointer and audio levels do not repeatedly resend avatar state', async t => {
  const { host } = await fixture(t);
  clearInterval(host.pointerTimer);
  let updates = 0;
  host.sendPet = () => { updates++; };
  host.pointer();
  host.pointer();
  host.pointer();
  assert.equal(updates, 1);
  host.stateChanged({ voice_input_level: .2 }, { type: 'voice.input_level' });
  host.stateChanged({ voice_input_level: .5 }, { type: 'voice.input_level' });
  assert.equal(updates, 1);
  host.stateChanged({ speaking: true }, { type: 'voice.playback' });
  assert.equal(updates, 2);
});

test('voice closes independently of the chat reading timer and the background task', async t => {
  const { host } = await fixture(t);
  for (const type of ['voice.idle_closed', 'voice.farewell_closed']) {
    host.showBubble();
    host.stateChanged({ voice: 'off', task: 'running' }, { type });
    assert.equal(host.bubbleOpen, true);
    assert.ok(host.chatCloseTimer);
    assert.equal(host.state.task, 'running');
    assert.equal(host.current.ready, true);
  }
});

test('chat waits for speech, composition, history and approval on both display profiles', async t => {
  const { host } = await fixture(t);
  assert.equal(host.chatCloseTimer._idleTimeout, 20000);
  host.holdChat(host.bubble.webContents, true);
  assert.equal(host.chatCloseTimer, undefined);
  host.holdChat(host.bubble.webContents, false);
  assert.ok(host.chatCloseTimer);
  host.stateChanged({ speaking: true }, { type: 'voice.playback' });
  assert.equal(host.chatCloseTimer, undefined);
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  host.stateChanged({ speaking: false }, { type: 'voice.playback' });
  assert.equal(host.chatCloseTimer._idleTimeout, 12000);
  host.stateChanged({ approval: '确认一下' }, { type: 'task.approval' });
  assert.equal(host.chatCloseTimer, undefined);
  host.stateChanged({ approval: '' }, { type: 'task.running' });
  host.stateChanged({ chat_window_action: 'pin' }, { type: 'chat.window_command' });
  assert.equal(host.chatCloseTimer, undefined);
  host.stateChanged({ chat_window_action: 'close' }, { type: 'chat.window_command' });
  assert.equal(host.bubbleOpen, false);
  assert.equal(host.chatPinned, false);
});

test('short messages use the character bubble, long replies open chat and add reading time', async t => {
  const { host } = await fixture(t);
  host.hideBubble();
  host.stateChanged({ assistant_text: '你好' }, { type: 'front.assistant_text' });
  assert.equal(host.caption.text, '你好');
  assert.equal(host.bubbleOpen, false);
  host.stateChanged({ notification_id: 'wx', notification_source: 'wechat', notification_sender: '小林',
    notification_body: '今晚几点到？', notification_text: '来信摘要' }, { type: 'notification.pending' });
  host.stateChanged({ assistant_text: '欢迎回来' }, { type: 'voice.assistant_text' });
  assert.equal(host.caption.source, '微信');
  assert.equal(host.caption.sender, '小林');
  assert.equal(host.caption.text, '今晚几点到？');
  host.stateChanged({ assistant_text: '好'.repeat(120) }, { type: 'voice.assistant_text_done' });
  assert.equal(host.bubbleOpen, true);
  assert.equal(host.caption, null);
  assert.equal(host.chatCloseTimer._idleTimeout, 30000);
});

test('an expanded chat actually hides when its reading deadline expires', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { host } = await fixture(t);
  t.mock.timers.tick(19999);
  assert.equal(host.bubble.isVisible(), true);
  t.mock.timers.tick(1);
  assert.equal(host.bubble.isVisible(), false);
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  host.showBubble();
  t.mock.timers.tick(11999);
  assert.equal(host.bubble.isVisible(), true);
  t.mock.timers.tick(1);
  assert.equal(host.bubble.isVisible(), false);
});

test('slow Engine startup does not fail avatar startup or open a diagnostic chat', async t => {
  const { host } = await fixture(t, { engineOnline: false });
  host.hideBubble();
  assert.equal(host.current.ready, true);
  assert.equal(host.engineReady, false);
  host.stateChanged({ voice: 'off', task: 'idle' }, { type: 'engine.ready' });
  assert.equal(host.engineReady, true);
  assert.equal(host.bubbleOpen, false);
});

test('idle walking waits for animation and stays inside changing screen limits', async t => {
  const secondary = display(2, 1440, 0, 1440, 900);
  const { host } = await fixture(t, { displays: [display(1, 0, 0, 1440, 900), secondary] });
  clearInterval(host.pointerTimer);
  await reportCrop(host);
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  host.hideBubble();
  const pet = host.current;
  const center = secondary.workArea.x + (secondary.workArea.width - pet.viewport.width) / 2;
  drag(host, pet.window, center - pet.origin.x, 0);
  pet.walkEligible = true;
  host.stepWalk(0, () => 0);
  assert.equal(pet.walkAt, 15000);
  assert.equal(pet.walk, null);
  host.stepWalk(14999, () => 0);
  assert.equal(pet.walk, null);
  pet.walkAt = 1000;
  host.stepWalk(1000, () => 0);
  assert.equal(pet.walk.direction, -1);
  const start = pet.origin.x;
  host.stepWalk(1040, () => 0);
  assert.equal(pet.origin.x, start, 'no sliding before walking animation starts');
  pet.walkPlaying = true;
  host.stepWalk(1080, () => 0);
  assert(pet.origin.x < start);
  for (let now = 1120; now <= 60000 && pet.walk; now += 40) host.stepWalk(now, () => 0);
  assert.equal(pet.walk, null);
  assert.equal(pet.origin.x, secondary.workArea.x + 8);
  assert.equal(pet.walkAt - pet.walkTick, 15000);
  pet.walk = { target: 9999, direction: 1 };
  secondary.bounds.width = secondary.workArea.width = pet.viewport.width + 80;
  for (let now = 60040; now <= 65000 && pet.walk; now += 40) host.stepWalk(now, () => 0);
  assert(pet.origin.x + pet.viewport.width <= secondary.workArea.x + secondary.workArea.width - 8);
});

test('walking pauses for conversation, speech, dragging, music, task panels and avatar replacement', async t => {
  const { host } = await fixture(t);
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  clearInterval(host.pointerTimer);
  host.hideBubble();
  const pet = host.current;
  pet.walkEligible = pet.walkPlaying = true;
  const pauses = [
    ['bubbleOpen', true], ['drag', {}], ['pending', {}], ['loading', true],
    ['musicActive', true], ['tasks', { isVisible: () => true }],
    ['state', { speaking: true }], ['state', { user_speaking: true }],
  ];
  for (const [key, value] of pauses) {
    const previous = host[key];
    host[key] = value;
    const start = pet.origin.x;
    pet.walk = { target: start + 100, direction: 1 };
    host.stepWalk(20000, () => 0);
    assert.equal(pet.walk, null, key);
    assert.equal(pet.origin.x, start, key);
    host[key] = previous;
  }
});

test('walking on a display with negative coordinates keeps the scene aligned and stops on resize', async t => {
  const { host, screen } = await fixture(t, { displays: [display(1, 0, 0, 1440, 900), display(2, -1440, -100, 1440, 900)] });
  clearInterval(host.pointerTimer);
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  host.hideBubble();
  const pet = host.current;
  pet.walkEligible = pet.walkPlaying = true;
  pet.walkAt = 1;
  host.stepWalk(1000, () => 0);
  for (let now = 1040; now <= 60000 && pet.walk; now += 40) host.stepWalk(now, () => 0);
  assert.equal(pet.origin.x, screen.displays[1].bounds.x + 8);
  host.sendPet(pet);
  assert.equal(pet.stage.getBounds().x + pet.canvas.x + pet.crop.x, pet.window.getBounds().x);
  pet.walk = { target: -500, direction: 1 };
  await host.request('desktop_set_pet_scale', { scale: 2 });
  assert.equal(pet.walk, null);
  inside(pet.window.getBounds(), screen.displays[1].workArea);
});

test('select secondary moves companion, chat, task panels and notifications together', async t => {
  const { host, screen, calls } = await fixture(t);
  const chat = host.bubble.getBounds();
  host.setDisplayProfile('hardware');
  const result = await host.request('desktop_set_pet_display', { id: '2' });
  assert.equal(result.pet_display_id, '2');
  assert.equal(result.separate_windows, true);
  assert.notDeepEqual(host.bubble.getBounds(), chat);
  const pet = host.current.window.getBounds();
  inside(pet, screen.displays[1].workArea);
  host.resizeBubble(host.bubble.webContents, 570);
  host.showTasks();
  host.hideBubble();
  host.stateChanged({ task: 'succeeded' }, { type: 'task.succeeded' });
  host.collapseTasks();
  host.showBubble();
  assert.deepEqual(host.current.window.getBounds(), pet);
  inside(host.bubble.getBounds(), screen.displays[1].workArea);
  assert(!calls.includes('desktop_set_pet_display'), 'window choice never goes to Python or voice');
});

function drag(host, window, dx, dy) {
  const frame = window.getBounds(), point = { x: frame.x + 20, y: frame.y + 20 };
  host.dragWindow(window.webContents, { phase: 'start', buttons: 1, point });
  host.dragWindow(window.webContents, { phase: 'move', buttons: 1, point: { x: point.x + dx, y: point.y + dy } });
  host.dragWindow(window.webContents, { phase: 'end', buttons: 0 });
}

test('macOS fullscreen stage tracks the avatar after moving to another display', async t => {
  const { host, screen } = await fixture(t);
  const { stage, window } = host.current;
  let rendered;
  stage.webContents.send = (channel, frame) => { if (channel === 'desktop:pet') rendered = frame; };
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  assert.equal(stage.isSimpleFullScreen(), true);
  assert.deepEqual(stage.getBounds(), screen.displays[1].bounds);

  const assertAligned = () => {
    const stageFrame = stage.getBounds();
    const hit = window.getBounds();
    assert.equal(stageFrame.x + rendered.canvas.x + rendered.crop.x, hit.x);
    assert.equal(stageFrame.y + rendered.canvas.y + rendered.crop.y, hit.y);
  };
  assertAligned();
  drag(host, window, 0, -80);
  assertAligned();
  host.setPetDisplay('1');
  assert.equal(stage.isSimpleFullScreen(), false);
  assertAligned();
});

test('split dragging is independent and constrained to the assigned screen', async t => {
  const { host, screen } = await fixture(t);
  host.setDisplayProfile('hardware');
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
  inside(host.bubble.getBounds(), screen.displays[1].workArea);
  inside(host.tasks.getBounds(), screen.displays[1].workArea);
  drag(host, host.tasks, 80, 30);
  assert.deepEqual(host.current.window.getBounds(), pet);
});

test('unplug falls back to primary; reconnect restores target and updates menus', async t => {
  const { host, screen } = await fixture(t);
  const secondary = screen.displays[1];
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  screen.displays = [screen.displays[0]];
  screen.emit('display-removed');
  assert.equal(host.sendDesktop().pet_display_id, '1');
  assert.equal(host.sendDesktop().selected_display_id, '2');
  assert.equal(host.sendDesktop().display_profile, 'hardware');
  assert.equal(host.sendDesktop().active_display_profile, 'desktop');
  assert.equal(host.current.stage.isSimpleFullScreen(), false);
  inside(host.current.window.getBounds(), screen.displays[0].workArea);
  assert(host.displayMenuItems().some(item => item.label === '未检测到扩展副屏' && item.enabled === false));
  screen.displays.push(secondary);
  screen.emit('display-added');
  assert.equal(host.sendDesktop().pet_display_id, '2');
  assert.equal(host.sendDesktop().active_display_profile, 'hardware');
  inside(host.current.window.getBounds(), secondary.workArea);
  inside(host.bubble.getBounds(), secondary.workArea);
});

test('menu controls start an explicit companion session and wake stays opt-in', async t => {
  const { host, data } = await fixture(t);
  assert.equal(host.wakeWord.enabled, false);
  const commands = [];
  host.client.request = async (method, params) => { commands.push({ method, params }); };
  const find = label => host.tray.menu.items.find(item => item.label === label);
  await find('开启语音').click();
  await find('开启 LoL 陪玩').click();
  assert.deepEqual(JSON.parse(JSON.stringify(commands)), [
    { method: 'toggle_voice' },
    { method: 'set_companion_session', params: { game: 'lol', enabled: true } },
  ]);
  host.stateChanged({ voice: 'ready', lol_companion_enabled: true });
  assert(find('结束语音'));
  assert(find('退出 LoL 陪玩'));
  find('常驻语音唤醒').click({ checked: true });
  assert.equal(JSON.parse(fs.readFileSync(path.join(data, 'voice-preferences.json'), 'utf8')).wakeEnabled, true);
  find('常驻语音唤醒').click({ checked: false });
  assert.equal(host.wakeWord.enabled, false);
});

test('portrait rotation, negative coordinates and scale changes preserve full viewport', async t => {
  const { host, screen } = await fixture(t);
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  const chat = host.bubble.getBounds();
  await host.request('desktop_set_pet_scale', { scale: 6 });
  inside({ ...host.current.origin, ...host.current.viewport }, screen.displays[1].workArea);
  assert.deepEqual(host.bubble.getBounds(), chat);
  screen.displays[1] = display(2, 1440, 200, 1920, 480);
  screen.emit('display-metrics-changed');
  inside({ ...host.current.origin, ...host.current.viewport }, screen.displays[1].workArea);
  inside(host.bubble.getBounds(), screen.displays[1].workArea);
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
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(data, 'display.json'), 'utf8')), { version: 2, target: 'primary', profile: 'hardware' });
  assert.equal(host.sendDesktop().pet_display_id, '1');
});

test('selection saves the display name and startup upgrades a valid legacy preference', async t => {
  const { host, data } = await fixture(t, { preference: JSON.stringify({ version: 1, target: '2' }) });
  const saved = () => JSON.parse(fs.readFileSync(path.join(data, 'display.json'), 'utf8'));
  assert.deepEqual(saved(), { version: 2, target: '2', profile: 'hardware', label: 'Display 2' });
  host.setPetDisplay(null);
  assert.deepEqual(saved(), { version: 2, target: null, profile: 'hardware' });
  assert.equal(host.displayLabel, '');
  host.setPetDisplay('2');
  assert.deepEqual(saved(), { version: 2, target: '2', profile: 'hardware', label: 'Display 2' });
});

test('startup restores the named secondary when its ID changed', async t => {
  const secondary = { ...display(3, 1440, 0, 720, 1920), label: '12.3FHD' };
  const { host, screen } = await fixture(t, {
    preference: JSON.stringify({ version: 1, target: '2', label: '12.3FHD' }),
    displays: [display(1, 0, 0, 1440, 900, 2), secondary],
  });
  assert.equal(host.sendDesktop().pet_display_id, '3');
  inside(host.current.window.getBounds(), secondary.workArea);
  inside(host.bubble.getBounds(), secondary.workArea);
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
  inside(host.bubble.getBounds(), secondary.workArea);
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

test('primary reassignment keeps chat and companion on the selected physical display', async t => {
  const { host, screen } = await fixture(t);
  host.setPetDisplay('2');
  screen.displays = [display(3, 0, -900, 1440, 900), ...screen.displays];
  screen.emit('display-metrics-changed');
  inside(host.bubble.getBounds(), screen.displays.find(item => item.id === 2).workArea);
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

test('placement uses logical work area, not Retina pixels; chat fits the selected work area', () => {
  const area = { x: -480, y: 24, width: 480, height: 1896 };
  const result = petPlacement({ origin: { x: 0, y: 0 } }, 6, area, true);
  inside({ ...result.origin, ...result.viewport }, area);
  assert.equal(result.viewport.width, 464);
  inside(displayBubbleFrame({ x: 600, y: 300 }, { x: 0, y: 0, width: 400, height: 500 }, 600), { x: 0, y: 0, width: 400, height: 500 });
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
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  const chat = host.bubble.getBounds();
  const small = host.current.window.getBounds();
  await host.request('desktop_set_pet_scale', { scale: 6 });
  await reportCrop(host);
  const large = host.current.window.getBounds();
  assert(large.height > small.height, 'maximum scale still enlarges the adaptive default');
  assert(large.width > 440, 'uses nearly all the 480px portrait width');
  assert(host.current.viewport.width > screen.displays[1].workArea.width, 'transparent viewport may extend beyond display');
  inside(large, screen.displays[1].workArea);
  const grownChat = host.bubble.getBounds();
  assert(grownChat.width >= chat.width, 'Chat adapts to the larger companion within the available screen');
  inside(grownChat, screen.displays[1].workArea);
  drag(host, host.current.window, 5000, -5000);
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
  assert.deepEqual(host.bubble.getBounds(), grownChat);
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
  host.setDisplayProfile('hardware');
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
  host.setDisplayProfile('hardware');
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
  inside(host.bubble.getBounds(), screen.displays[1].workArea);
});

test('full-display render windows always pass through clicks; unmeasured hit windows do too', async t => {
  const { host, screen } = await fixture(t);
  assert.equal(host.current.stage.ignoreMouseEvents, true);
  assert.equal(host.current.window.ignoreMouseEvents, true);
  assert.ok(host.current.window.getBounds().width < screen.displays[0].workArea.width);
  await reportCrop(host);
  const frame = host.current.window.getBounds();
  host.updatePetInput(host.current, { x: frame.x + 10, y: frame.y + 10 });
  assert.equal(host.current.window.ignoreMouseEvents, false);
  assert.equal(host.current.window.getBounds().width, host.current.crop.width);
  host.setPetDisplay('2');
  screen.emit('display-metrics-changed');
  assert.equal(host.current.stage.ignoreMouseEvents, true);
  inside(host.current.window.getBounds(), screen.displays[1].workArea);
});

test('desktop avatar lets transparent cells pass through and holds input during a drag', async t => {
  const { host } = await fixture(t);
  const pet = host.current, { viewport } = pet;
  await host.report(pet.window.webContents, { type: 'bounds', viewport: [viewport.width, viewport.height],
    bounds: { x: 0, y: 0, ...viewport }, hitMask: { size: 3, cells: '000010000' } });
  const blank = { x: pet.origin.x + 10, y: pet.origin.y + 10 };
  const body = { x: pet.origin.x + viewport.width / 2, y: pet.origin.y + viewport.height / 2 };
  host.updatePetInput(pet, blank);
  assert.equal(pet.window.ignoreMouseEvents, true);
  host.updatePetInput(pet, body);
  assert.equal(pet.window.ignoreMouseEvents, false);
  host.dragWindow(pet.window.webContents, { phase: 'start', buttons: 1, point: body });
  host.updatePetInput(pet, blank);
  assert.equal(pet.window.ignoreMouseEvents, false);
  host.dragWindow(pet.window.webContents, { phase: 'cancel', buttons: 0 });
  host.updatePetInput(pet, blank);
  assert.equal(pet.window.ignoreMouseEvents, true);
});

test('only the explicit hardware profile enables a stage on the assigned secondary', async t => {
  const { host } = await fixture(t);
  let scene;
  host.current.stage.webContents.send = (channel, payload) => { if (channel === 'desktop:pet') scene = payload.scene; };
  host.setPetDisplay('2');
  assert.equal(scene, undefined);
  await host.request('desktop_set_display_profile', { profile: 'hardware' });
  assert.equal(scene, 'purple');
  host.setPetDisplay('1');
  assert.equal(scene, undefined);
  host.setPetDisplay(null);
  assert.equal(scene, undefined);
});

test('hardware background persists across profiles and applies without moving the character', async t => {
  const { host, data } = await fixture(t);
  await host.request('desktop_set_stage_background', { background: 'pink' });
  host.setPetDisplay('2');
  let scene;
  host.current.stage.webContents.send = (channel, payload) => { if (channel === 'desktop:pet') scene = payload.scene; };
  host.setDisplayProfile('hardware');
  assert.equal(scene, 'pink');
  const avatar = host.current.window.getBounds();
  await host.request('desktop_set_stage_background', { background: 'purple' });
  assert.equal(scene, 'purple');
  assert.deepEqual(host.current.window.getBounds(), avatar);
  await host.request('desktop_set_stage_background', { background: 'pink' });
  await assert.rejects(host.request('desktop_set_stage_background', { background: 'other' }), /副屏背景/);
  host.setDisplayProfile('desktop');
  assert.equal(scene, undefined);
  const preference = fs.readFileSync(path.join(data, 'display.json'), 'utf8');
  const { host: reopened } = await fixture(t, { preference });
  assert.equal(reopened.displayState().stage_background, 'pink');
  reopened.current.stage.webContents.send = host.current.stage.webContents.send;
  reopened.setDisplayProfile('hardware');
  assert.equal(scene, 'pink');
});

test('a pinned desktop pet stays transparent and moves with the 414-point chat', async t => {
  const secondary = display(2, 1440, -50, 1200, 800);
  const { host, calls } = await fixture(t, {
    preference: JSON.stringify({ version: 2, profile: 'desktop', target: '2', label: secondary.label }),
    displays: [display(1, 0, 0, 1440, 900, 2), secondary],
  });
  await reportCrop(host);
  assert.equal(host.displayState().selected_display_id, '2');
  assert.equal(host.displayState().display_profile, 'desktop');
  assert.equal(host.displayState().separate_windows, false);
  assert.equal(host.current.stage.isSimpleFullScreen(), false);
  assert.equal(host.current.stage.ignoreMouseEvents, true);
  host.resizeBubble(host.bubble.webContents, 800);
  assert.equal(host.bubble.getBounds().width, 414);
  assert.equal(host.bubble.getBounds().height, 345);
  const pet = host.current.window.getBounds();
  host.hideBubble();
  host.current.walkEligible = true;
  host.current.walk = { target: pet.x + 100, direction: 1 };
  host.stepWalk(20000, () => 0);
  assert.deepEqual(host.current.window.getBounds(), pet);
  assert.equal(host.current.walk, null);
  host.showBubble();
  assert.deepEqual(host.current.window.getBounds(), pet);
  const chat = host.bubble.getBounds();
  drag(host, host.bubble, -25, -35);
  assert.notEqual(host.bubble.getBounds().x, chat.x);
  assert.equal(host.bubble.getBounds().x - chat.x, host.current.window.getBounds().x - pet.x);
  inside(host.bubble.getBounds(), secondary.workArea);
  assert(!calls.includes('desktop_set_display_profile'));
});

test('hardware uses compact chat and a larger avatar on an ultrawide display', async t => {
  const secondary = display(2, 1440, 0, 1920, 690);
  const { host } = await fixture(t, { displays: [display(1, 0, 0, 1440, 900), secondary] });
  await reportCrop(host);
  host.setPetDisplay('2');
  const desktop = host.current.window.getBounds();
  host.setPetDisplay('1');
  host.setDisplayProfile('hardware');
  assert.equal(host.displayState().active_display_profile, 'desktop');
  await assert.rejects(host.request('desktop_set_display_profile', { profile: 'unknown' }), /展示形态/);
  host.setPetDisplay('2');
  host.resizeBubble(host.bubble.webContents, 600);
  const chat = host.bubble.getBounds();
  assert.equal(chat.width, 480);
  assert(chat.height <= secondary.workArea.height * .8);
  const hardware = host.current.window.getBounds();
  assert(hardware.height > desktop.height * 2);
  assert(Math.abs(hardware.height - secondary.workArea.height * .8) <= 4);
  await host.request('desktop_set_pet_scale', { scale: 1.1 });
  assert(host.current.window.getBounds().height > hardware.height);
  await host.request('desktop_set_pet_scale', { scale: 1 });
  assert(hardware.x + hardware.width <= chat.x);
  host.hideBubble();
  drag(host, host.current.window, 1600, 0);
  assert(host.current.window.getBounds().x >= chat.x, 'the pet can use the former chat area');
  host.showBubble();
  assert(host.current.window.getBounds().x + host.current.window.getBounds().width <= host.bubble.getBounds().x);
});

test('renderer failure and late shutdown callbacks cannot position destroyed avatar windows', async t => {
  const { host, screen } = await fixture(t);
  await reportCrop(host);
  host.setPetDisplay('2');
  host.showTasks();
  const lost = host.current;
  // Electron can mark the native object destroyed before the close callback is delivered.
  lost.window.destroyed = true;
  assert.doesNotThrow(() => screen.emit('display-metrics-changed'));
  lost.window.emit('closed');
  assert.equal(lost.stage.isDestroyed(), true);
  assert.equal(host.current, null);
  assert.equal(host.bubble.isVisible(), true, 'Failure feedback remains available');
  host.close();
  assert.doesNotThrow(() => host.failAvatar(lost, 'late renderer exit'));
  assert.doesNotThrow(() => host.stateChanged({}, { type: 'task.failed' }));
});

test('closing a pending avatar layer during commit retains the current avatar', async t => {
  const { host, screen, calls } = await fixture(t);
  const original = host.current;
  const request = host.service.request;
  let finishCommit;
  host.service.request = (method, params) => method === 'desktop_commit_avatar'
    ? new Promise(resolve => { finishCommit = resolve; }) : request(method, params);
  const loading = host.loadAvatar('other');
  await new Promise(resolve => setImmediate(resolve));
  const candidate = host.pending;
  const failed = assert.rejects(loading, /已保留当前形象/);
  const committing = host.report(candidate.stage.webContents, { type: 'ready' });
  assert(finishCommit);
  candidate.stage.destroy();
  assert.doesNotThrow(() => screen.emit('display-added'));
  assert.equal(candidate.window.isDestroyed(), true);
  assert.doesNotThrow(() => candidate.window.emit('closed'));
  finishCommit({});
  await committing;
  assert.equal(host.current, original);
  assert.equal(original.window.isDestroyed(), false);
  await failed;
  assert.equal(host.pending, null);
  assert.equal(calls.filter(call => call === 'desktop_cancel_avatar').length, 1);
});

test('chat and task display never cover the companion after growth, scaling or dragging', async t => {
  const { host, screen } = await fixture(t);
  await reportCrop(host);
  host.setDisplayProfile('hardware');
  host.setPetDisplay('2');
  const separated = () => {
    const pet = host.current.window.getBounds();
    for (const window of [host.bubble, host.tasks].filter(Boolean)) {
      const frame = window.getBounds();
      inside(frame, screen.displays[1].workArea);
      assert(frame.x + frame.width <= pet.x || pet.x + pet.width <= frame.x ||
        frame.y + frame.height <= pet.y || pet.y + pet.height <= frame.y, 'Chat/task panel covers the visible companion');
    }
  };
  host.resizeBubble(host.bubble.webContents, 570);
  host.showTasks();
  assert.equal(host.bubble.getBounds().height, 570, 'The reserved area must keep the requested chat height');
  separated();
  await host.request('desktop_set_pet_scale', { scale: 6 });
  separated();
  drag(host, host.current.window, -5000, -5000);
  drag(host, host.bubble, 5000, 5000);
  separated();
  for (const shape of [display(2, -1470, 33, 1470, 923), display(2, -720, 0, 720, 1000)]) {
    screen.displays[1] = shape;
    screen.emit('display-metrics-changed');
    separated();
    assert.equal(host.snapshot.scale, 6, 'Screen fitting must not overwrite the saved scale');
  }
});

test('avatar actions and music survive camera state updates while the avatar is on secondary', async t => {
  const { host } = await fixture(t);
  host.setPetDisplay('2');
  const frame = host.current.window.getBounds();
  let sent;
  host.current.stage.webContents.send = (channel, payload) => { if (channel === 'desktop:pet') sent = payload; };
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
