// Exercise the actual Electron renderer and its read-only Engine connection.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

async function runSmoke(window) {
  const directory = process.env.BOXAGENT_PANEL_CAPTURE_DIR || '/tmp/boxagent-panel-visual';
  await fs.mkdir(directory, { recursive: true });
  const js = source => window.webContents.executeJavaScript(source);
  async function ready() {
    for (let attempt = 0; attempt < 160; attempt++) {
      if (await js(`document.getElementById('content').getAttribute('aria-busy') === 'false' && !document.getElementById('refresh').disabled`)) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Panel did not finish loading');
  }
  async function capture(name) {
    await new Promise(resolve => setTimeout(resolve, 180));
    await fs.writeFile(path.join(directory, `${name}.png`), (await window.webContents.capturePage()).toPNG());
  }
  const report = { checks: [], pages: {} };
  const check = (condition, label) => { assert.ok(condition, label); report.checks.push(label); };
  await ready();
  const online = await js(`document.getElementById('connection').dataset.state === 'online'`);
  if (!online) {
    check(await js(`Boolean(document.querySelector('.empty-state'))`), 'disconnected state has recovery action');
    await capture('disconnected');
  } else {
    const candidates = await js(`(async () => {
      const rows = await boxagent.request('list_sessions', {include_archived: true});
      for (const row of rows) {
        const context = await boxagent.request('context_snapshot', {session_id: row.session_id});
        if (context.status === 'ready' && context.entries.length) return row.session_id;
      }
      return null;
    })()`);
    if (candidates) {
      await js(`document.querySelector('[data-session-id="${candidates}"]').click()`); await ready();
      check(await js(`document.querySelectorAll('.entry-row').length > 0`), 'real context entries rendered');
      await capture('context');
      const counts = await js(`(() => {
        const before = document.querySelectorAll('.entry-row').length;
        const input = document.getElementById('search'); input.value = '__no_match_9f841__'; input.dispatchEvent(new Event('input'));
        const after = document.querySelectorAll('.entry-row').length;
        const empty = Boolean(document.querySelector('.empty-state'));
        input.value = ''; input.dispatchEvent(new Event('input'));
        return {before, after, empty};
      })()`);
      check(counts.before > 0 && counts.after === 0 && counts.empty, 'search excludes unmatched context entries');
      await js(`document.querySelector('.entry-row').click()`);
      for (let i = 0; i < 100; i++) {
        if (!await js(`document.getElementById('full').textContent.includes('正在读取')`)) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      check(await js(`document.getElementById('detail').open && document.getElementById('full').textContent.length > 0`), 'full context detail loaded');
      await capture('detail');
      window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' });
      window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' });
      await new Promise(resolve => setTimeout(resolve, 120));
      check(await js(`!document.getElementById('detail').open && document.activeElement.classList.contains('entry-row')`), 'Escape closes detail and restores focus');
    }
    for (const view of ['timeline', 'memory', 'errors', 'context']) {
      await js(`document.querySelector('[data-view="${view}"]').click()`); await ready();
      const state = await js(`({title: document.getElementById('page-title').textContent,
        summary: document.getElementById('summary').textContent,
        selected: document.querySelector('[aria-current="page"]').dataset.view,
        entries: document.querySelectorAll('.entry-row, .timeline-event, .memory-card, .log-row').length,
        empty: Boolean(document.querySelector('.empty-state')),
        rawMessageFinal: [...document.querySelectorAll('.entry-heading')].some(el => el.textContent === 'message.final')})`);
      check(state.selected === view && (state.entries > 0 || state.empty), `${view} has selected navigation and rendered state`);
      check(!state.rawMessageFinal, `${view} uses readable product event labels`);
      report.pages[view] = state;
      await capture(view);
    }
    await js(`(() => {
      for (const view of ['timeline', 'memory', 'context', 'errors', 'timeline']) document.querySelector('[data-view="' + view + '"]').click();
    })()`); await ready();
    check(await js(`document.querySelector('[aria-current="page"]').dataset.view === 'timeline' && Boolean(document.querySelector('.timeline-event')) && !document.querySelector('.memory-card, .log-row, .entry-row')`), 'rapid page changes discard responses from previous views');
    await js(`document.querySelector('[data-view="context"]').click()`); await ready();
    const emptySession = await js(`(async () => {
      for (const row of await boxagent.request('list_sessions', {include_archived:true})) {
        if ((await boxagent.request('context_snapshot', {session_id:row.session_id})).status === 'empty') return row.session_id;
      }
      return null;
    })()`);
    if (emptySession) {
      await js(`document.querySelector('[data-session-id="${emptySession}"]').click()`); await ready();
      check(await js(`Boolean(document.querySelector('.empty-state')) && !document.querySelector('.entry-row')`), 'session without context does not retain previous entries');
      await capture('empty');
      if (candidates) { await js(`document.querySelector('[data-session-id="${candidates}"]').click()`); await ready(); }
    }
    await js(`document.querySelector('[data-view="timeline"]').click()`); await ready();
    await js(`document.getElementById('page').scrollTop = 320`);
    const scrollBefore = await js(`document.getElementById('page').scrollTop`);
    await js(`document.querySelector('[data-view="context"]').click()`); await ready();
    await js(`document.querySelector('[data-view="timeline"]').click()`); await ready();
    check(await js(`document.getElementById('page').scrollTop === ${scrollBefore}`), 'page scroll restored after navigation');
    window.setContentSize(760, 680);
    for (const view of ['timeline', 'memory', 'errors', 'context']) {
      await js(`document.querySelector('[data-view="${view}"]').click()`); await ready();
      check(await js(`document.getElementById('page').scrollWidth <= document.getElementById('page').clientWidth && document.body.scrollWidth <= innerWidth`), `${view} fits narrow window without horizontal overflow`);
    }
    await capture('narrow'); window.setContentSize(1200, 820);
  }
  await fs.writeFile(path.join(directory, 'report.json'), JSON.stringify(report, null, 2));
  await fs.writeFile('/tmp/boxagent-panel-smoke.json', JSON.stringify(report, null, 2));
  await fs.writeFile('/tmp/boxagent-panel-smoke.png', (await window.webContents.capturePage()).toPNG());
  console.log(`Panel smoke: ${report.checks.length} checks passed; ${directory}`);
}
module.exports = { runSmoke };
