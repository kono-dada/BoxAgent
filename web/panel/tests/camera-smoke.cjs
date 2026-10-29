// Production camera UI + Python Engine, synthetic video and a fake Qwen transport.
const { app, BrowserWindow, systemPreferences } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'boxagent-camera-'));
const data = path.join(temporary, 'data'); fs.mkdirSync(data);
process.env.BOXAGENT_DATA_DIR = data;
process.env.BOXAGENT_ELECTRON_USER_DATA = path.join(temporary, 'profile');
const wrapper = path.join(temporary, 'python');
fs.writeFileSync(wrapper, `#!/bin/sh\nexec '${root}/.venv/bin/python' '${root}/tests/fixtures/electron_desktop.py'\n`, { mode: 0o700 });
process.env.BOXAGENT_DESKTOP_PYTHON = wrapper;
process.env.BOXAGENT_DESKTOP_ARGS = '[]';
process.argv.push('--desktop');
app.commandLine.appendSwitch('use-fake-device-for-media-stream');
// Still exercise our permission handlers; only OS prompt is stubbed in this fixture.
systemPreferences.askForMediaAccess = async () => true;
const engine = spawn(path.join(root, '.venv/bin/python'), [path.join(root, 'tests/fixtures/camera_engine.py'), path.join(data, 'engine.sock')], { stdio: ['ignore', 'ignore', 'pipe'] });
engine.stderr.on('data', (chunk) => process.stderr.write(chunk));
const js = (window, code) => window.webContents.executeJavaScript(code);
const wait = async (fn, label) => {
  for (let i = 0; i < 400; i++) { if (await fn()) return; await new Promise((r) => setTimeout(r, 50)); }
  throw Error(`Timed out: ${label}`);
};
const deadline = setTimeout(() => { console.error('camera smoke timed out'); engine.kill(); app.exit(1); }, 90000);
require('../main.cjs');
app.whenReady().then(async () => {
  try {
    await wait(() => BrowserWindow.getAllWindows().some((w) => w.webContents.getURL().endsWith('/companion.html')), 'desktop');
    const bubble = BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().endsWith('/companion.html'));
    await wait(() => BrowserWindow.getAllWindows().some((w) => w.webContents.getURL().endsWith('/pet.html') && w.isVisible()), '3D avatar ready');
    await wait(() => js(bubble, '!!window.boxagent'), 'bridge');
    await wait(() => js(bubble, '!!document.querySelector(".companion-camera:not(:disabled)")'), 'chat camera button');
    const artifacts = path.join(root, 'artifacts/camera'); fs.mkdirSync(artifacts, { recursive: true });
    for (const [width, language, theme] of [[440, 'zh-CN', 'light'], [520, 'zh-CN', 'dark'], [440, 'en', 'dark']]) {
      bubble.setSize(width, 600);
      await js(bubble, `localStorage.setItem('boxagent.interface-preferences', ${JSON.stringify(JSON.stringify({ language, theme }))}); window.dispatchEvent(new StorageEvent('storage', {key:'boxagent.interface-preferences'}));`);
      await new Promise((r) => setTimeout(r, 250));
      const layout = await js(bubble, `(() => {
        const footer = document.querySelector('.companion-footer');
        const buttons = [...footer.querySelectorAll('button')].map(b => {
          const r = b.getBoundingClientRect();
          return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, height:r.height, fits:b.scrollWidth <= b.clientWidth};
        });
        return {buttons, width:innerWidth, overflow:footer.scrollWidth > footer.clientWidth};
      })()`);
      assert.equal(layout.width, width, 'test viewport must match the requested width');
      assert(!layout.overflow, `footer overflow at ${width}/${language}`);
      assert.equal(layout.buttons[0].height, layout.buttons[1].height, 'matching voice and camera button heights');
      layout.buttons.forEach((button, index) => {
        assert(button.fits && button.left >= 0 && button.right <= layout.width, `button clipped at ${width}/${language}`);
        layout.buttons.slice(index + 1).forEach((other) => assert(
          button.right <= other.left || other.right <= button.left || button.bottom <= other.top || other.bottom <= button.top,
          'footer buttons must not overlap'));
      });
      fs.writeFileSync(path.join(artifacts, `chat-camera-${width}-${theme}.png`), (await bubble.webContents.capturePage()).toPNG());
    }
    bubble.setSize(440, 600);
    await js(bubble, `localStorage.setItem('boxagent.interface-preferences', '{"language":"zh-CN","theme":"light"}'); window.dispatchEvent(new StorageEvent('storage', {key:'boxagent.interface-preferences'}));`);
    assert.equal((await js(bubble, "boxagent.request('desktop_camera_state')")).status, 'off');
    assert.equal((await js(bubble, "boxagent.request('state')")).voice, 'off');
    await js(bubble, 'document.querySelector(".companion-camera").click()');
    await wait(() => BrowserWindow.getAllWindows().some(w => w.getTitle().includes('摄像头')), 'camera window launched from chat');
    const camera = BrowserWindow.getAllWindows().find((w) => w.getTitle().includes('摄像头'));
    camera.webContents.on('console-message', (_e, _l, message) => { if (/Error|Refused|failed/.test(message)) console.log(message); });
    camera.webContents.session.webRequest.onErrorOccurred((details) => console.log('Camera resource failed', details.url, details.error));
    await wait(() => js(camera, 'document.querySelector("main").dataset.status === "waiting"'), 'waiting for audio is not a connection error');
    await wait(async () => {
      const err = await js(camera, 'document.querySelector(".error")?.textContent');
      if (err) throw Error(err);
      return js(camera, 'document.querySelector("main").dataset.status === "active"');
    }, 'JPEG camera frames accepted by the conversation adapter');
    assert(await js(camera, 'document.querySelector("video").videoWidth > 0'));
    assert.equal((await js(bubble, "boxagent.request('state')")).camera_status, 'active');
    await wait(() => js(bubble, 'document.querySelector(".companion-camera.active")?.textContent === "关闭摄像头"'), 'camera state reflected in chat');
    assert.equal(await js(bubble, 'document.querySelector(".companion-camera").getAttribute("aria-pressed")'), 'true');
    assert.equal((await js(bubble, "boxagent.request('state')")).assistant_text, '', 'frames alone must not trigger replies');
    const pixels = await js(camera, `(() => {
      const v = document.querySelector('video'), c = document.createElement('canvas'); c.width = 64; c.height = 48;
      const x = c.getContext('2d'); x.drawImage(v, 0, 0, 64, 48);
      return new Set(x.getImageData(0, 0, 64, 48).data).size;
    })()`);
    assert(pixels > 10, 'video preview is nonblank');
    assert(await js(camera, '!/千问|API|用量|已发送|你说|暂无回应/.test(document.body.innerText)'), 'video window has no model/debug copy');
    assert(await js(camera, 'getComputedStyle(document.querySelector(".call-header")).webkitAppRegion === "drag"'), 'dark header is draggable');
    assert(await js(camera, 'getComputedStyle(document.querySelector(".window-tools")).webkitAppRegion === "no-drag"'), 'pin stays clickable');
    assert(await js(camera, `(() => {
      const t = document.querySelector('.call-time').getBoundingClientRect();
      return Math.abs((t.left + t.right) / 2 - innerWidth / 2) < 1 && t.left > 90;
    })()`), 'timer is centered without overlapping native window buttons');
    const framesBeforeMinimize = (await js(bubble, "boxagent.request('state')")).camera_frames_sent;
    camera.minimize();
    await wait(() => camera.isMinimized(), 'native minimize');
    await new Promise(resolve => setTimeout(resolve, 9500));
    const minimizedState = await js(bubble, "boxagent.request('state')");
    assert.equal(minimizedState.camera_status, 'active', 'minimize must not trigger the frame watchdog');
    assert(minimizedState.camera_frames_sent > framesBeforeMinimize, 'capture continues while minimized');
    camera.restore();
    await wait(() => !camera.isMinimized(), 'native restore');
    await js(camera, 'document.querySelector(".pin-button").click()');
    await wait(() => camera.isAlwaysOnTop(), 'pin is applied to the actual window');
    await wait(() => js(camera, 'document.querySelector(".pin-button[aria-pressed=true]:not(:disabled)") !== null'), 'pin acknowledgement reaches renderer');
    await js(camera, 'document.querySelector(".pin-button").click()');
    await wait(() => !camera.isAlwaysOnTop(), 'unpin');
    await wait(() => js(camera, 'document.querySelector(".pin-button[aria-pressed=false]:not(:disabled)") !== null'), 'unpin acknowledgement reaches renderer');
    await js(camera, 'document.querySelector(".mirror-button").click()');
    assert.equal(await js(camera, 'getComputedStyle(document.querySelector("video")).transform'), 'none');
    assert(await js(camera, 'document.querySelector("video").srcObject.active'), 'mirror never stops capture');
    await js(camera, 'document.querySelector(".mirror-button").click()');
    await wait(() => js(camera, 'document.querySelector(".call-time").textContent !== "00:00"'), 'connected timer advances');
    fs.writeFileSync(path.join(artifacts, 'chat-camera-active.png'), (await bubble.webContents.capturePage()).toPNG());
    fs.writeFileSync(path.join(artifacts, 'camera-desktop.png'), (await camera.webContents.capturePage()).toPNG());
    for (const [width, height] of [[320, 360], [400, 480], [800, 600]]) {
      camera.setSize(width, height);
      await new Promise((r) => setTimeout(r, 250));
      const layout = await js(camera, `(() => {
        const preview = document.querySelector('.preview').getBoundingClientRect();
        const controls = [...document.querySelectorAll('button')].map(b => b.getBoundingClientRect().toJSON());
        return {width:innerWidth, height:innerHeight, preview:preview.toJSON(), controls,
          fits:document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight};
      })()`);
      assert(layout.fits, 'compact window must not scroll');
      for (const button of layout.controls) assert(button.left >= 0 && button.right <= layout.width && button.top >= 0 && button.bottom <= layout.height, 'controls fit the window');
      assert(layout.preview.height > 100, 'video remains usable at minimum size');
      await js(camera, 'document.querySelector(".device-button").click()');
      assert(await js(camera, `(() => { const r = document.querySelector('.device-menu').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0; })()`), 'camera menu stays inside the window');
      assert(await js(camera, 'document.querySelector("select").disabled'), 'camera device is locked during a call');
      await js(camera, 'document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))');
      assert(await js(camera, '!document.querySelector(".device-menu")'), 'escape dismisses menu');
      fs.writeFileSync(path.join(artifacts, `camera-${width}.png`), (await camera.webContents.capturePage()).toPNG());
    }
    camera.setSize(400, 480);
    await js(camera, 'window.savedStream = document.querySelector("video").srcObject; document.querySelector("button.stop").click()');
    await wait(() => js(camera, 'savedStream.getTracks().every(t => t.readyState === "ended")'), 'tracks stop');
    await wait(async () => (await js(bubble, "boxagent.request('state')")).camera_status === 'off', 'backend off');
    await wait(() => js(bubble, 'document.querySelector(".companion-camera").getAttribute("aria-pressed") === "false"'), 'in-window stop resets chat toggle');
    await wait(() => js(camera, 'document.querySelector(".call-time").textContent === "00:00"'), 'stop resets call timer');
    await js(camera, 'document.querySelector(".device-button").click()');
    assert(await js(camera, '!document.querySelector("select").disabled'), 'camera can be selected while stopped');
    await js(camera, 'document.querySelector(".device-button").click()');
    await wait(() => js(camera, '["rgb(7, 169, 85)", "rgb(9, 191, 97)"].includes(getComputedStyle(document.querySelector("button.start")).backgroundColor)'), 'start button returns to green');
    fs.writeFileSync(path.join(artifacts, 'camera-idle.png'), (await camera.webContents.capturePage()).toPNG());
    await js(bubble, 'document.querySelector(".companion-camera").click()');
    await wait(async () => (await js(bubble, "boxagent.request('state')")).camera_status === 'active', 'camera restart');
    // Simulated response traverses the normal conversation path, never the removed greeting event.
    await js(bubble, "boxagent.request('submit_text', {goal:'画面测试'})");
    await wait(async () => (await js(bubble, "boxagent.request('state')")).assistant_text.includes('测试通道回复'), 'normal conversation response still reaches chat');
    assert(await js(camera, '!document.querySelector(".response")'), 'transcript is not duplicated in video window');
    const state = await js(bubble, "boxagent.request('state')");
    assert.equal(state.task, 'idle'); assert.equal(state.user_text, '画面测试');
    fs.writeFileSync(path.join(artifacts, 'conversation.png'), (await camera.webContents.capturePage()).toPNG());
    await js(bubble, 'document.querySelector(".companion-camera").click()');
    await wait(() => camera.isDestroyed(), 'old camera window destroyed');
    await wait(async () => (await js(bubble, "boxagent.request('state')")).camera_status === 'off', 'close cleans up');
    assert.equal((await js(bubble, "boxagent.request('state')")).voice, 'off');
    // A pre-existing call is borrowed, not owned, by the camera window.
    await wait(() => js(bubble, 'document.querySelector(".companion-camera").textContent === "开启摄像头"'), 'camera button reset');
    await js(bubble, 'document.querySelector(".companion-voice").click()');
    await wait(async () => (await js(bubble, "boxagent.request('state')")).voice === 'ready', 'original voice button');
    await wait(() => js(bubble, '!document.querySelector(".companion-camera").disabled'), 'camera launch available during voice');
    await js(bubble, 'document.querySelector(".companion-camera").click()');
    await wait(() => BrowserWindow.getAllWindows().some(w => w.getTitle().includes('摄像头')), 'new camera window launched');
    const attached = BrowserWindow.getAllWindows().find((w) => w.getTitle().includes('摄像头'));
    await wait(() => js(attached, '!!document.querySelector(".status")'), 'reopen camera');
    await wait(async () => {
      const error = await js(attached, 'document.querySelector(".error")?.textContent');
      if (error) throw Error(error);
      return (await js(bubble, "boxagent.request('state')")).camera_status === 'active';
    }, 'camera attached to original voice');
    attached.webContents.send('engine:event', { type: 'camera.stopped', state: { revision: 0, camera_error: '' } });
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal((await js(bubble, "boxagent.request('state')")).camera_status, 'active', 'late stop from a previous startup must not close the current camera');
    assert(await js(attached, 'document.querySelector("video").srcObject?.active'), 'stale events must not release current media tracks');
    await wait(() => js(bubble, '!document.querySelector(".companion-camera").disabled'), 'switch-off available');
    await js(bubble, 'document.querySelector(".companion-camera").click()');
    await wait(() => attached.isDestroyed(), 'switch-off releases camera renderer');
    await wait(async () => (await js(bubble, "boxagent.request('state')")).camera_status === 'off', 'detached camera');
    assert.equal((await js(bubble, "boxagent.request('state')")).voice, 'ready', 'closing camera must preserve the original call');
    await js(bubble, "boxagent.request('submit_text', {goal:'关闭摄像头后继续交流'})");
    assert((await js(bubble, "boxagent.request('state')")).assistant_text.includes('关闭摄像头后继续交流'));
    // Ordinary navigation is still opt-in; opening settings must not start capture.
    await js(bubble, "boxagent.request('desktop_open_camera')");
    const idle = BrowserWindow.getAllWindows().find(w => w.getTitle().includes('摄像头'));
    await wait(() => js(idle, '!!document.querySelector("button.start")'), 'manual entry stays off');
    assert.equal((await js(bubble, "boxagent.request('desktop_camera_state')")).status, 'off');
    if (process.platform === 'darwin') {
      systemPreferences.askForMediaAccess = async () => false;
      await js(bubble, 'document.querySelector(".companion-camera").click()');
      await wait(() => js(idle, 'document.querySelector(".error")?.textContent.includes("权限")'), 'permission denial surfaced');
      await wait(() => js(bubble, 'document.querySelector(".companion-camera").getAttribute("aria-pressed") === "false"'), 'denial resets switch');
      assert.equal((await js(bubble, "boxagent.request('state')")).voice, 'ready', 'denial must not stop original voice');
      idle.setSize(320, 360);
      await new Promise(resolve => setTimeout(resolve, 200));
      assert(await js(idle, `(() => { const r = document.querySelector('.error').getBoundingClientRect(); const p = document.querySelector('.preview').getBoundingClientRect(); return r.top >= p.top && r.bottom <= p.bottom && r.right <= innerWidth; })()`), 'long errors stay inside preview and do not cover controls');
      fs.writeFileSync(path.join(artifacts, 'camera-error.png'), (await idle.webContents.capturePage()).toPNG());
      systemPreferences.askForMediaAccess = async () => true;
    }
    await js(bubble, 'document.querySelector(".companion-camera").click()');
    await wait(async () => (await js(bubble, "boxagent.request('state')")).camera_status === 'active', 'restart after denied permission');
    idle.reload();
    await wait(async () => (await js(bubble, "boxagent.request('desktop_camera_state')")).status === 'off', 'reload resets capture intent');
    await wait(() => js(idle, '!!document.querySelector("button.start") && !document.querySelector("video").srcObject'), 'reload must not restart camera');
    assert.equal((await js(bubble, "boxagent.request('state')")).voice, 'ready', 'reload preserves original voice');
    idle.close();
    await js(bubble, "boxagent.request('toggle_voice')");
    console.log('PASS: one-click camera on/off, switch state, light/dark layouts, synthetic JPEG -> Python adapter, no auto-reply, normal response, cleanup, borrowed voice preserved, permission denial and reload stay off. Cloud model is simulated in this UI test.');
    clearTimeout(deadline); app.quit();
  } catch (error) {
    console.error(error);
    for (const window of BrowserWindow.getAllWindows().filter(w => w.getTitle().includes('摄像头'))) {
      console.error('Camera fixture state:', await js(window, `({
        status:document.querySelector('.status')?.textContent,
        error:document.querySelector('.error')?.textContent,
        visibility:document.visibilityState,
        videoReady:document.querySelector('video')?.readyState,
        tracks:document.querySelector('video')?.srcObject?.getTracks().map(t => ({ready:t.readyState, muted:t.muted}))
      })`).catch(() => 'renderer unavailable'));
    }
    clearTimeout(deadline); engine.kill(); app.exit(1);
  }
});
app.on('will-quit', () => { engine.kill(); fs.rmSync(temporary, { recursive: true, force: true }); });
