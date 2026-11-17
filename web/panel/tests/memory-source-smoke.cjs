// Real renderer + preload, using public synthetic data and no user memory.
const { app, BrowserWindow, ipcMain } = require('electron');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const output = path.resolve(__dirname, '../../../.runtime/visual-memory/ui');
app.setPath('userData', path.join(output, 'profile'));
const state = { session_id: 'fixture', voice: 'off', task: 'idle' };
const node = { id: 'memory-fixture', type: 'EPISODE', content: '鸣潮配队画面 · 示例资料',
  timestamp: '2026-11-17T13:00:00Z', source: 'visual_memory', metadata: { evidence_id: 'fixture-image' } };
const atom = { ...node, id: 'atom-fixture', type: 'EVENT', content: '画面观察：角色与武器 · 示例资料' };
const edge = { id: 'contains-fixture', source: node.id, target: atom.id, type: 'SEMANTIC', subtype: 'CONTAINS' };
let reads = 0, deletions = 0, deleted = false;
app.whenReady().then(async () => {
  await fs.mkdir(output, { recursive: true });
  const window = new BrowserWindow({ show: false, width: 1500, height: 1100,
    webPreferences: { preload: path.resolve(__dirname, '../preload.cjs'), contextIsolation: true, nodeIntegration: false } });
  const js = code => window.webContents.executeJavaScript(code);
  await window.loadURL('about:blank');
  const fixture = await js(`(() => {
    const canvas = document.createElement('canvas'); canvas.width=960; canvas.height=480;
    const context=canvas.getContext('2d'); context.fillStyle='#1e2831'; context.fillRect(0,0,960,480);
    context.fillStyle='#d8dfde'; context.font='16px sans-serif'; context.fillText('PUBLIC LAYOUT FIXTURE',32,42);
    ['#43546b','#565e53','#6b5b54'].forEach((color,index)=>{
      context.fillStyle=color; context.fillRect(32+index*306,94,282,276);
    });
    context.fillStyle='#d8dfde'; context.fillText('TEAM ALPHA / BETA / GAMMA   -   WEAPON DEMO SWORD',32,436);
    return canvas.toDataURL('image/png');
  })()`);
  const image = fixture.split(',')[1];
  await fs.writeFile(path.join(output, 'fixture.png'), Buffer.from(image, 'base64'));
  ipcMain.handle('engine:errors', () => []);
  ipcMain.handle('engine:request', (_event, method) => {
    if (method === 'state') return state;
    if (method === 'list_sessions') return [];
    if (method === 'memory_snapshot') return { nodes: deleted ? [] : [node, atom], edges: deleted ? [] : [edge] };
    if (method === 'memory_evidence') { reads++; return { status: 'succeeded', image: 'data:image/png;base64,' + image,
      captured_at: 1794920400, user_request: '记住这套配队，下次再看。', caption: '示例截图中显示角色与武器，来源是人工生成的公开布局验收素材。' }; }
    if (method === 'session_events' || method === 'session_timeline') return [];
    if (method === 'delete_memory_node') { deletions++; deleted = true; return { status: 'succeeded', deleted_ids: [node.id, atom.id] }; }
    return {};
  });
  const wait = async condition => {
    for (let i = 0; i < 150; i++) { if (await js(condition)) return; await new Promise(r => setTimeout(r, 40)); }
    throw new Error(condition);
  };
  await window.loadFile(path.resolve(__dirname, '../dist/index.html'));
  const results = [];
  for (const theme of ['light', 'dark']) {
    // Load through the real preferences path so background updates cannot reset it.
    await js(`localStorage.setItem('boxagent.interface-preferences', JSON.stringify({ language:'zh-CN', theme:'${theme}' }))`);
    await window.loadFile(path.resolve(__dirname, '../dist/index.html'));
    await wait(`!document.getElementById('refresh').disabled`);
    await js(`document.querySelector('.navigation [data-view="memory"]').click()`);
    await wait(`document.querySelector('.memory-explorer')`);
    await js(`[...document.querySelectorAll('.segmented button')].find(b=>b.textContent==='列表').click()`);
    await wait(`document.querySelectorAll('.memory-node-row').length === 2`);
    await js(`document.querySelector('.memory-node-row').click()`);
    await wait(`document.querySelector('.memory-source-preview')`);
    assert.equal(await js(`document.querySelector('.memory-source-preview').open`), false);
    assert.equal(reads, results.length);
    await js(`document.querySelector('.memory-source-preview summary').click()`);
    await wait(`document.querySelector('.memory-source-preview img')?.naturalWidth > 0`);
    assert.equal(reads, results.length + 1);
    await js(`document.querySelector('.memory-source-preview').scrollIntoView({ block:'center', behavior:'instant' })`);
    await new Promise(r => setTimeout(r, 250));
    const measure = await js(`(() => { const card = document.querySelector('.memory-source-preview');
      const rect = card.getBoundingClientRect(), root = document.documentElement;
      return { overflow: card.scrollWidth > card.clientWidth, color: getComputedStyle(card).color,
        width: card.clientWidth, imageWidth: card.querySelector('img').clientWidth,
        theme: root.dataset.theme, preference: root.dataset.themePreference,
        visible: rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight && rect.right <= innerWidth,
        viewport: [innerWidth, innerHeight], background: getComputedStyle(document.body).backgroundColor }; })()`);
    assert.equal(measure.overflow, false);
    assert.equal(measure.visible, true);
    assert.equal(measure.theme, theme);
    assert.equal(measure.preference, theme);
    assert.ok(measure.imageWidth <= measure.width);
    results.push({ theme, ...measure });
    await fs.writeFile(path.join(output, `${theme}.png`), (await window.webContents.capturePage()).toPNG());
  }
  assert.notEqual(results[0].color, results[1].color);
  await js(`window.confirm = () => true; document.querySelector('.graph-inspector .danger').click()`);
  await wait(`!document.querySelector('.memory-source-preview') && document.querySelectorAll('.memory-node-row').length === 0`);
  assert.equal(deletions, 1);
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify({ reads, deletions, results }, null, 2));
  console.log('Memory source real renderer: visible light/dark preview, lazy PNG loading and cascade deletion passed');
  app.quit();
}).catch(error => { console.error(error); app.exit(1); });
