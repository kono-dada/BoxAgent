const { BrowserWindow, screen } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function inside(frame, area) {
  assert(frame.x >= area.x && frame.y >= area.y && frame.x + frame.width <= area.x + area.width &&
    frame.y + frame.height <= area.y + area.height, `${JSON.stringify(frame)} outside ${JSON.stringify(area)}`);
}

exports.run = async ({ pet, bubble, js, wait, dragBy, emit }) => {
  const primary = screen.getPrimaryDisplay();
  const secondary = screen.getAllDisplays().find(display => display.id !== primary.id);
  assert(secondary, 'Connect an extended second display for this hardware smoke test');
  console.log('Physical displays:', screen.getAllDisplays().map(({ id, label, workArea, scaleFactor }) => ({ id, label, workArea, scaleFactor })));
  await js(bubble, "boxagent.request('desktop_toggle_bubble')");
  await wait(() => js(bubble, "Boolean(document.querySelector('.companion-display'))"), 'display button');
  const selected = await js(bubble, `boxagent.request('desktop_set_pet_display', {id:${JSON.stringify(String(secondary.id))}})`);
  assert.equal(selected.pet_display_id, String(secondary.id));
  await wait(() => screen.getDisplayMatching(pet.getBounds()).id === secondary.id, 'pet on secondary');
  inside(pet.getBounds(), secondary.workArea);
  inside(bubble.getBounds(), primary.workArea);
  await wait(() => js(bubble, "document.querySelector('.companion-display').dataset.active==='true'"), 'active button');
  const chat = bubble.getBounds();
  await dragBy(pet, 0, -80);
  assert.deepEqual(bubble.getBounds(), chat, 'dragging pet does not move chat');
  await dragBy(bubble, -70, -30);
  assert.equal(screen.getDisplayMatching(pet.getBounds()).id, secondary.id);
  emit('front.assistant_text', { assistant_text: '分屏测试：小人在副屏，对话在主屏。' });
  await js(bubble, "boxagent.desktopAction('show-tasks')");
  await wait(() => BrowserWindow.getAllWindows().some(window => window.webContents.getURL().endsWith('/tasks.html')), 'task panel');
  const tasks = BrowserWindow.getAllWindows().find(window => window.webContents.getURL().endsWith('/tasks.html'));
  await wait(() => tasks.isVisible(), 'tasks visible');
  inside(tasks.getBounds(), primary.workArea);
  inside(bubble.getBounds(), primary.workArea);
  inside(pet.getBounds(), secondary.workArea);
  await js(bubble, "boxagent.request('desktop_set_pet_scale', {scale:2})");
  await new Promise(resolve => setTimeout(resolve, 800));
  inside(pet.getBounds(), secondary.workArea);
  await js(bubble, "boxagent.desktopAction('show-panel')");
  await wait(() => BrowserWindow.getAllWindows().some(window => window.webContents.getURL().endsWith('/index.html')), 'console');
  const panel = BrowserWindow.getAllWindows().find(window => window.webContents.getURL().endsWith('/index.html'));
  await wait(() => js(panel, "Boolean(document.getElementById('settings-button'))"), 'settings');
  await js(panel, "document.getElementById('settings-button').click()");
  await wait(() => js(panel, "Boolean(document.querySelector('#settings-tabs [data-view=controls]'))"), 'controls tab');
  await js(panel, "document.querySelector('#settings-tabs [data-view=controls]').click()");
  await wait(() => js(panel, "Boolean(document.getElementById('pet-display'))"), 'screen select');
  assert.equal(await js(panel, "document.getElementById('pet-display').value"), String(secondary.id));
  const choose = async id => {
    await js(panel, `(()=>{const element=document.getElementById('pet-display');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(element,${JSON.stringify(id)});element.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await wait(() => js(panel, `document.getElementById('pet-display').value===${JSON.stringify(id)}&&!document.getElementById('pet-display').disabled`), 'applied display');
  };
  await choose(String(primary.id));
  inside(pet.getBounds(), primary.workArea);
  await choose(String(secondary.id));
  inside(pet.getBounds(), secondary.workArea);
  await new Promise(resolve => setTimeout(resolve, 800));
  const smaller = pet.getBounds();
  await js(bubble, "boxagent.request('desktop_set_pet_scale', {scale:6})");
  await new Promise(resolve => setTimeout(resolve, 1800));
  const larger = pet.getBounds();
  console.log('Actual avatar window at 200% / 600%:', { smaller, larger });
  inside(larger, secondary.workArea);
  if (secondary.workArea.height > secondary.workArea.width * 2) {
    assert(larger.height > smaller.height * 1.8, 'portrait pet visibly grows past the old canvas-width limit');
    assert(larger.width > smaller.width * 1.8, 'portrait width is no longer limited by transparent margins');
  }
  const chatBeforeLargeDrag = bubble.getBounds();
  await dragBy(pet, 0, -100);
  inside(pet.getBounds(), secondary.workArea);
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
      const output = process.env.BOXAGENT_DISPLAY_SCREENSHOTS;
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
  await choose('together');
  inside(pet.getBounds(), primary.workArea);
  assert.equal((await js(bubble, "boxagent.request('desktop_snapshot')")).separate_windows, false);
  console.log('Real dual-display placement, independent drag, notifications, tasks, scale, controls and legacy restore passed');
};
