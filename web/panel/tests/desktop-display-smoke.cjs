const { BrowserWindow, screen } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function inside(frame, area) {
  assert(frame.x >= area.x && frame.y >= area.y && frame.x + frame.width <= area.x + area.width &&
    frame.y + frame.height <= area.y + area.height, `${JSON.stringify(frame)} outside ${JSON.stringify(area)}`);
}
function separated(first, second) {
  return first.x + first.width <= second.x || second.x + second.width <= first.x ||
    first.y + first.height <= second.y || second.y + second.height <= first.y;
}

exports.run = async ({ pet, stage, bubble, js, wait, dragBy, emit }) => {
  const primary = screen.getPrimaryDisplay();
  const secondary = screen.getAllDisplays().find(display => display.id !== primary.id);
  const currentArea = display => screen.getAllDisplays().find(item => item.id === display.id).workArea;
  assert(secondary, 'Connect an extended second display for this hardware smoke test');
  console.log('Physical displays:', screen.getAllDisplays().map(({ id, label, workArea, scaleFactor }) => ({ id, label, workArea, scaleFactor })));
  const output = process.env.BOXAGENT_DISPLAY_SCREENSHOTS;
  const saveWindow = async (window, name) => {
    if (!output) return;
    fs.mkdirSync(output, { recursive: true });
    fs.writeFileSync(path.join(output, name), (await window.webContents.capturePage()).toPNG());
  };
  const measureAvatar = async (display, label) => {
    let size;
    const ratio = Math.min(display.scaleFactor, 2);
    await wait(async () => {
      const viewer = stage.webContents.mainFrame.frames[0];
      if (!viewer || viewer.isDestroyed()) return false;
      size = await viewer.executeJavaScript(`(() => {
        const canvas = document.querySelector('canvas');
        if (!window.pet?.debug().ready || !canvas) return null;
        return { ratio: devicePixelRatio, viewport: [innerWidth, innerHeight], buffer: [canvas.width, canvas.height] };
      })()`);
      return size?.ratio === display.scaleFactor && size.viewport.every((points, axis) =>
        size.buffer[axis] === Math.floor(points * ratio));
    }, `avatar drawing buffer follows the scale of ${label}`);
    console.log('Avatar drawing buffer:', JSON.stringify({ label, ...size }));
  };
  await measureAvatar(primary, 'primary');
  await js(bubble, "boxagent.request('desktop_toggle_bubble')");
  await wait(() => js(bubble, "Boolean(document.querySelector('.companion-display'))"), 'display button');
  const selected = await js(bubble, `boxagent.request('desktop_set_pet_display', {id:${JSON.stringify(String(secondary.id))}})`);
  assert.equal(selected.pet_display_id, String(secondary.id));
  assert.equal(selected.active_display_profile, 'desktop');
  await measureAvatar(secondary, 'secondary desktop');
  const desktopAvatar = pet.getBounds();
  assert.equal(await js(stage, "Boolean(document.querySelector('.pet-stage-bg'))"), false);
  const hardware = await js(bubble, "boxagent.request('desktop_set_display_profile', {profile:'hardware'})");
  assert.equal(hardware.active_display_profile, 'hardware');
  await wait(() => screen.getDisplayMatching(pet.getBounds()).id === secondary.id, 'pet on secondary');
  inside(pet.getBounds(), currentArea(secondary));
  inside(bubble.getBounds(), currentArea(secondary));
  assert.deepEqual(stage.getBounds(), secondary.bounds, 'secondary scene covers the full physical display');
  const assertAvatarAligned = async () => {
    const { canvas, crop } = await js(stage, `new Promise(resolve => {
      const stop = boxagent.onPet(({ canvas, crop }) => { stop(); resolve({ canvas, crop }); });
      boxagent.desktopAction('pet-ready');
    })`);
    const stageFrame = stage.getBounds(), hitFrame = pet.getBounds();
    assert(Math.abs(stageFrame.x + canvas.x + crop.x - hitFrame.x) <= 1, 'avatar and hit window share the same horizontal origin');
    assert(Math.abs(stageFrame.y + canvas.y + crop.y - hitFrame.y) <= 1, 'avatar and hit window share the same vertical origin');
  };
  await assertAvatarAligned();
  await wait(() => separated(pet.getBounds(), bubble.getBounds()), 'chat clear of companion');
  await measureAvatar(secondary, 'secondary');
  const hardwareAvatar = pet.getBounds(), hardwareChat = bubble.getBounds();
  if (secondary.workArea.width >= 1280 && secondary.workArea.width > secondary.workArea.height) {
    assert.equal(hardwareChat.width, 480);
    assert(hardwareAvatar.height > desktopAvatar.height, 'hardware mode gives the avatar more room');
  }
  console.log('Default secondary layout:', JSON.stringify({ desktopAvatar, hardwareAvatar, hardwareChat, stage: stage.getBounds() }));
  await saveWindow(bubble, 'secondary-chat-default.png');
  await wait(() => js(stage, "document.querySelector('.pet-stage-bg') && getComputedStyle(document.querySelector('.pet-stage-bg')).backgroundImage.startsWith('radial-gradient')"), 'secondary scene background');
  stage.webContents.invalidate();
  await js(stage, 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  const sceneCapture = await stage.webContents.capturePage();
  const scenePixels = sceneCapture.toBitmap();
  let opaque = 0;
  for (let index = 3; index < scenePixels.length; index += 4) if (scenePixels[index] > 0) opaque++;
  if (output) {
    fs.mkdirSync(output, { recursive: true });
    fs.writeFileSync(path.join(output, 'secondary-scene.png'), sceneCapture.toPNG());
  }
  assert(opaque > scenePixels.length / 4 * .8, 'The selected secondary must show its scene background');
  await wait(() => js(bubble, "document.querySelector('.companion-display').dataset.active==='true'"), 'active button');
  const chat = bubble.getBounds();
  await dragBy(pet, 0, -80);
  await assertAvatarAligned();
  const moved = bubble.getBounds();
  assert.deepEqual([moved.x, moved.y], [chat.x, chat.y], 'dragging pet does not move chat');
  await dragBy(bubble, -70, -30);
  assert.equal(screen.getDisplayMatching(pet.getBounds()).id, secondary.id);
  emit('front.assistant_text', { assistant_text: '副屏测试：伙伴、对话与任务展示都在副屏。' });
  await js(bubble, "boxagent.desktopAction('show-tasks')");
  await wait(() => BrowserWindow.getAllWindows().some(window => window.webContents.getURL().endsWith('/tasks.html')), 'task panel');
  const tasks = BrowserWindow.getAllWindows().find(window => window.webContents.getURL().endsWith('/tasks.html'));
  await wait(() => tasks.isVisible(), 'tasks visible');
  inside(tasks.getBounds(), currentArea(secondary));
  inside(bubble.getBounds(), currentArea(secondary));
  inside(pet.getBounds(), currentArea(secondary));
  await js(bubble, "boxagent.request('desktop_set_pet_scale', {scale:2})");
  await new Promise(resolve => setTimeout(resolve, 800));
  inside(pet.getBounds(), currentArea(secondary));
  await saveWindow(bubble, 'secondary-chat.png');
  await js(bubble, "boxagent.request('desktop_toggle_bubble')");
  await wait(() => !bubble.isVisible(), 'hardware chat collapsed');
  assert.equal(await js(stage, "Boolean(document.querySelector('.pet-stage-bg'))"), true);
  await saveWindow(stage, 'secondary-chat-collapsed.png');
  await js(bubble, "boxagent.request('desktop_toggle_bubble')");
  await wait(() => bubble.isVisible(), 'hardware chat reopened');
  await js(bubble, "boxagent.desktopAction('show-panel')");
  await wait(() => BrowserWindow.getAllWindows().some(window => window.webContents.getURL().endsWith('/index.html')), 'console');
  const panel = BrowserWindow.getAllWindows().find(window => window.webContents.getURL().endsWith('/index.html'));
  await wait(() => js(panel, "Boolean(document.getElementById('settings-button'))"), 'settings');
  await js(panel, "document.getElementById('settings-button').click()");
  await wait(() => js(panel, "Boolean(document.querySelector('#settings-tabs [data-view=controls]'))"), 'controls tab');
  await js(panel, "document.querySelector('#settings-tabs [data-view=controls]').click()");
  await wait(() => js(panel, "Boolean(document.getElementById('pet-display'))"), 'screen select');
  assert.equal(await js(panel, "document.getElementById('display-profile').value"), 'hardware');
  assert.equal(await js(panel, "document.getElementById('pet-display').value"), String(secondary.id));
  const choose = async (id, value) => {
    const select = `document.getElementById(${JSON.stringify(id)})`;
    await js(panel, `(()=>{const element=${select};Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(element,${JSON.stringify(value)});element.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await wait(() => js(panel, `${select}.value===${JSON.stringify(value)}&&!${select}.disabled`), `applied ${id}`);
  };
  await js(bubble, "boxagent.request('desktop_set_pet_scale', {scale:1})");
  const beforeBackground = pet.getBounds();
  await choose('stage-background', 'pink');
  await wait(() => js(stage, "document.querySelector('.pet-stage-bg img')?.naturalWidth > 0"), 'original pink background loaded');
  assert.deepEqual(pet.getBounds(), beforeBackground, 'background switching keeps the avatar in place');
  const viewer = stage.webContents.mainFrame.frames[0];
  const framesBefore = await viewer.executeJavaScript('window.pet.debug().frames');
  await wait(async () => await viewer.executeJavaScript('window.pet.debug().frames') > framesBefore, 'avatar continues rendering on pink');
  stage.webContents.invalidate();
  await js(stage, 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  let pinkCapture;
  await wait(async () => {
    pinkCapture = await stage.webContents.capturePage();
    const pixels = pinkCapture.toBitmap();
    let dark = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index] < 90 && pixels[index + 1] < 90 && pixels[index + 2] < 90) dark++;
    }
    return dark > 100;
  }, 'avatar pixels remain visible on pink');
  if (output) fs.writeFileSync(path.join(output, 'secondary-pink.png'), pinkCapture.toPNG());
  await choose('stage-background', 'purple');
  await wait(() => js(stage, "document.querySelector('.pet-stage-bg') && !document.querySelector('.pet-stage-bg img')"), 'purple background restored');
  await choose('pet-display', String(primary.id));
  inside(pet.getBounds(), currentArea(primary));
  await wait(() => js(stage, "!document.querySelector('.pet-stage-bg')"), 'primary remains transparent');
  await measureAvatar(primary, 'primary after switching');
  await choose('pet-display', String(secondary.id));
  inside(pet.getBounds(), currentArea(secondary));
  await js(bubble, "boxagent.request('desktop_set_pet_scale', {scale:2})");
  await new Promise(resolve => setTimeout(resolve, 800));
  const smaller = pet.getBounds();
  await js(bubble, "boxagent.request('desktop_set_pet_scale', {scale:6})");
  await new Promise(resolve => setTimeout(resolve, 1800));
  const larger = pet.getBounds();
  console.log('Actual avatar window at 200% / 600%:', { smaller, larger });
  inside(larger, currentArea(secondary));
  assert(separated(larger, bubble.getBounds()), 'A large companion must remain clear of chat');
  assert(separated(larger, tasks.getBounds()), 'Task display must remain clear of companion');
  assert(larger.height >= smaller.height, 'increasing scale cannot reduce the avatar height');
  const chatBeforeLargeDrag = bubble.getBounds();
  await dragBy(pet, 0, -100);
  inside(pet.getBounds(), currentArea(secondary));
  assert.deepEqual(bubble.getBounds(), chatBeforeLargeDrag);

  // Optional Playwright screenshots use the bundled tool runtime, not a production dependency.
  if (process.env.BOXAGENT_PLAYWRIGHT_MODULE) {
    const { chromium } = require(process.env.BOXAGENT_PLAYWRIGHT_MODULE);
    const browser = await chromium.connectOverCDP('http://127.0.0.1:9224');
    try {
      const pages = browser.contexts().flatMap(context => context.pages());
      const chatPage = pages.find(page => page.url().endsWith('/companion.html'));
      const petPage = pages.find(page => page.url().endsWith('/pet.html'));
      const panelPage = pages.find(page => page.url().endsWith('/index.html'));
      assert(output, 'Set BOXAGENT_DISPLAY_SCREENSHOTS for screenshots');
      fs.mkdirSync(output, { recursive: true });
      await chatPage.screenshot({ path: path.join(output, 'chat-440.png') });
      await panelPage.screenshot({ path: path.join(output, 'controls.png') });
      await new Promise(resolve => setTimeout(resolve, 500));
      await petPage.screenshot({ path: path.join(output, 'pet-secondary.png'), omitBackground: true });
      const capture = await pet.webContents.capturePage();
      const first = capture.toBitmap();
      let visible = 0;
      for (let i = 3; i < first.length; i += 4) if (first[i] > 0) visible++;
      assert(visible > 500, '3D avatar renders nonblank pixels');
      const { width, height } = capture.getSize();
      let left = width, right = -1, top = height, bottom = -1;
      for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
        if (first[(y * width + x) * 4 + 3] > 8) {
          left = Math.min(left, x); right = Math.max(right, x);
          top = Math.min(top, y); bottom = Math.max(bottom, y);
        }
      }
      assert(left > 0 && top > 0 && right < width - 1 && bottom < height - 1,
        'camera preserves full body rather than cropping off head or feet');
      await new Promise(resolve => setTimeout(resolve, 650));
      assert(!first.equals((await pet.webContents.capturePage()).toBitmap()), 'avatar animates');
      await chatPage.setViewportSize({ width: 320, height: 580 });
      await chatPage.screenshot({ path: path.join(output, 'chat-320.png') });
      const boxes = await chatPage.locator('.companion-heading, .companion-task-summary, .companion-controls').evaluateAll(elements =>
        elements.map(element => { const { x, width } = element.getBoundingClientRect(); return { x, width }; }));
      assert(boxes[0].x + boxes[0].width <= boxes[1].x && boxes[1].x + boxes[1].width <= boxes[2].x, 'narrow header does not overlap');
      assert(await chatPage.evaluate(() => document.body.scrollWidth <= innerWidth), 'no narrow overflow');
      console.log('Playwright screenshots and animated avatar pixels passed:', output);
    } finally { await browser.close(); }
  }
  await js(bubble, "boxagent.request('desktop_set_pet_scale', {scale:2})");
  await new Promise(resolve => setTimeout(resolve, 500));
  await choose('pet-display', 'together');
  inside(pet.getBounds(), currentArea(primary));
  assert.equal((await js(bubble, "boxagent.request('desktop_snapshot')")).separate_windows, false);
  stage.destroy();
  assert.doesNotThrow(() => screen.emit('display-metrics-changed', {}, primary, ['workArea']));
  await wait(() => pet.isDestroyed(), 'closing the renderer also releases the hit window');
  assert.equal((await js(bubble, "boxagent.request('desktop_snapshot')")).appearance_ready, false);
  console.log('Real dual-display placement, independent drag, notifications, tasks, scale, controls and legacy restore passed');
};
