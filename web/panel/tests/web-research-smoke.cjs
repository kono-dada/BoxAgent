// Exercise the real companion renderer and preload with isolated public-data fixtures.
const { app, BrowserWindow, ipcMain } = require('electron');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { publicSourceUrl } = require('../desktop/web-links.cjs');
const output = path.resolve(__dirname, '../../../.runtime/web-research/ui');
app.setPath('userData', path.join(output, 'profile'));
const source = { title: '无冠者（敌人）', url: 'https://wiki.kurobbs.com/mc/item/1226961704338931712',
  published_at: '2026-03-14', site_name: '库洛 Wiki', read_status: 'read' };
const view = { session_id: 'preview', interaction_id: 'guide', stage: 'ready',
  query: '鸣潮 野外 普通无冠者 打断', requested_version: '3.7', message: '资料已找到',
  retrieved_at: '2026-11-17T11:00:00Z', sources: [source,
    { title: '同名 Boss 的不同形态需要分开核对', url: 'https://example.com/guide', published_at: '2024-05-30', site_name: null, read_status: 'unavailable' }] };
const state = { session_id: 'preview', voice: 'off', task: 'idle', user_text: '', assistant_text: '', web_research: view };
let history = [
  { type: 'message.final', sequence: 1, event_id: 'u', interaction_id: 'guide', role: 'user', content: '我在3.7版打野外普通无冠者，帮我查一下，只说两点。' },
  { type: 'web.research.completed', sequence: 2, interaction_id: 'guide', data: { web_research: view } },
  { type: 'message.final', sequence: 3, event_id: 'a', interaction_id: 'guide', role: 'assistant', content: '野外无冠者飞行蓄力闪红光时，用磐石守卫或青羽鹭的声骸技能打断。注意它没有体术步态，别按剧情版打法硬扛。' },
];
const opened = [];
app.whenReady().then(async () => {
  await fs.mkdir(output, { recursive: true });
  const window = new BrowserWindow({ width: 400, height: 650, show: false,
    webPreferences: { preload: path.resolve(__dirname, '../preload.cjs'), contextIsolation: true, nodeIntegration: false } });
  ipcMain.handle('engine:request', (_event, method, params) => {
    if (method === 'state') return state;
    if (method === 'session_events') return history;
    if (method === 'desktop_snapshot') return { name: '伙伴' };
    if (method === 'desktop_camera_state') return { status: 'off', error: '' };
    if (method === 'desktop_open_web_source') { opened.push(publicSourceUrl(params?.url)); return { status: 'opened' }; }
    return {};
  });
  const evaluate = value => window.webContents.executeJavaScript(value);
  const wait = async condition => {
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      if (await evaluate(condition)) return;
      await new Promise(resolve => setTimeout(resolve, 40));
    }
    throw Error(`UI condition timed out: ${condition}`);
  };
  const capture = async name => {
    await new Promise(resolve => setTimeout(resolve, 200));
    await fs.writeFile(path.join(output, `${name}.png`), (await window.webContents.capturePage()).toPNG());
  };
  await window.loadFile(path.resolve(__dirname, '../dist/companion.html'));
  await wait(`document.querySelector('.web-reference-card')`);
  await evaluate(`document.documentElement.dataset.theme = 'light'`);
  assert.equal(await evaluate(`document.querySelector('.web-reference-card').open`), false);
  await capture('collapsed-light');
  await evaluate(`document.querySelector('.web-reference-card summary').click()`);
  await wait(`document.querySelectorAll('.web-reference-link').length === 2`);
  assert.equal(await evaluate(`document.querySelector('.web-reference-version').textContent.includes('待核对')`), true);
  await evaluate(`document.querySelector('.web-reference-link').click()`);
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.deepEqual(opened, [source.url]);
  await capture('expanded-light');
  const results = [];
  for (const theme of ['light', 'dark']) {
    for (const width of [340, 400]) {
      window.setContentSize(width, 650);
      await evaluate(`document.documentElement.dataset.theme = '${theme}'`);
      await new Promise(resolve => setTimeout(resolve, 100));
      const measure = await evaluate(`(() => {
        const card = document.querySelector('.web-reference-card');
        const link = document.querySelector('.web-reference-link');
        const title = getComputedStyle(document.querySelector('.web-reference-title'));
        const meta = getComputedStyle(document.querySelector('.web-reference-meta'));
        return { width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth,
          cardOverflow: card.scrollWidth > card.clientWidth, background: getComputedStyle(link).backgroundColor,
          title: title.color, meta: meta.color, target: link.getBoundingClientRect().height };
      })()`);
      assert.equal(measure.overflow, false);
      assert.equal(measure.cardOverflow, false, JSON.stringify({ theme, ...measure }));
      assert.ok(measure.target >= 28);
      results.push({ theme, ...measure });
      await capture(`expanded-${theme}-${width}`);
    }
  }
  window.webContents.debugger.attach('1.3');
  await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  state.web_research = { ...view, interaction_id: 'pending', stage: 'reading', message: '正在读攻略原文', sources: [] };
  window.webContents.send('engine:state', state);
  await wait(`document.querySelector('.web-research-status svg')`);
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.web-research-status svg')).animationName`), 'none');
  history = [];
  state.session_id = 'new-session';
  window.webContents.send('engine:state', state); // Deliberately leave a stale research view in the snapshot.
  await wait(`!document.querySelector('.web-reference-card') && !document.querySelector('.web-research-status')`);
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify({ results, opened, staleSessionHidden: true }, null, 2));
  console.log(JSON.stringify({ status: 'passed', screenshots: output, results }));
  window.destroy();
  app.quit();
}).catch(error => { console.error(error); app.exit(1); });
