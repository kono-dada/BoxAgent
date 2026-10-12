// Exercise actual Electron navigation and the Engine/desktop connection.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

async function runSmoke(window) {
  const directory = process.env.BOXAGENT_PANEL_CAPTURE_DIR || '/tmp/boxagent-panel-visual';
  await fs.mkdir(directory, { recursive: true });
  const js = async source => { try { return await window.webContents.executeJavaScript(source); } catch(error) { console.error('Renderer check failed:', source); throw error; } };
  window.webContents.setBackgroundThrottling(false);
  async function ready() {
    for (let attempt = 0; attempt < 160; attempt++) {
      if (await js(`document.getElementById('content').getAttribute('aria-busy') === 'false' && !document.getElementById('refresh').disabled && (document.querySelector('.context-console, .memory-explorer, .feature-page, .empty-state, .timeline-event, .log-row') !== null)`)) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Panel did not finish loading');
  }
  async function navigate(view) {
    if (['appearance','persona','controls','errors','interface'].includes(view)) {
      if (await js(`document.getElementById('settings-tabs').hidden`)) {
        await js(`document.getElementById('settings-button').click()`); await ready();
      }
      await js(`(document.querySelector('#content [data-view="${view}"]') || document.querySelector('#settings-tabs [data-view="${view}"]')).click()`);
    } else await js(`document.querySelector('${view === 'settings' ? '#settings-button' : '.navigation [data-view="' + view + '"]'}').click()`);
    await ready();
  }
  async function capture(name) {
    window.show(); window.focus();
    await js(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
    await new Promise(resolve => setTimeout(resolve, 250));
    await fs.writeFile(path.join(directory, `${name}.png`), (await window.webContents.capturePage(undefined, {stayAwake:true})).toPNG());
  }
  const report = { checks: [], pages: {} };
  const check = (condition, label) => { assert.ok(condition, label); report.checks.push(label); };
  await ready();
  check(await js(`document.body.dataset.view === 'conversation'`), 'panel opens on conversation');
  check(await js(`JSON.stringify([...document.querySelectorAll('.navigation [data-view]')].map(el=>el.dataset.view)) === JSON.stringify(['conversation','memory','skills','runs','context','timeline'])`), 'main navigation groups daily features and observation pages');
  check(await js(`(()=>{const settings=document.getElementById('settings-button').getBoundingClientRect(),status=document.getElementById('connection').getBoundingClientRect();return settings.right+8<=status.left && Math.abs((settings.top+settings.bottom)/2-(status.top+status.bottom)/2)<2})()`),'settings and connection status share one uncluttered footer row');
  const online = await js(`document.getElementById('connection').dataset.state === 'online'`);
  if (!online) {
    check(await js(`Boolean(document.querySelector('.empty-state'))`), 'disconnected state has recovery action');
    await capture('disconnected');
    await navigate('settings');
    check(await js(`document.querySelectorAll('.settings-category').length === 5`), 'settings categories remain available offline');
    await capture('settings-offline');
  } else {
    await navigate('context');
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
      check(await js(`Boolean(document.querySelector('.context-console'))`), 'context dashboard rendered from real records');
      await capture('context');
      await js(`document.querySelector('.browser-category').open = true`);
      await new Promise(resolve => setTimeout(resolve, 150));
      await js(`document.querySelector('.browser-entry').open = true`);
      for (let i = 0; i < 100; i++) {
        if (await js(`Boolean(document.querySelector('.browser-entry-body pre')) && !document.querySelector('.browser-entry-body pre').textContent.includes('正在读取')`)) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      check(await js(`document.querySelector('.browser-entry-body pre').textContent.length > 0`), 'context browser expands real full text inline');
      await js(`document.querySelector('.console-main-grid').scrollIntoView({block:'start'})`);
      await capture('context-browser');
      await js(`document.getElementById('page').scrollTop=0`);
      const requestCount = await js(`document.querySelectorAll('.trend-column').length`);
      if (requestCount) {
        await js(`document.querySelector('.trend-column').click()`);
        await new Promise(resolve => setTimeout(resolve, 150));
        check(await js(`document.querySelector('.context-browser select').value !== 'live' && document.querySelector('.browser-frame-title strong').textContent.includes('请求')`), 'trend click selects matching historical context');
        await js(`document.querySelector('.step-brief .text-button').click()`);
        check(await js(`document.getElementById('detail').open`), 'request raw details open');
        window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' });
        window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' });
        await new Promise(resolve => setTimeout(resolve, 120));
        check(await js(`!document.getElementById('detail').open && document.activeElement.classList.contains('text-button')`), 'Escape closes detail and restores focus');
        await js(`document.querySelector('.context-browser select').value = 'live'; document.querySelector('.context-browser select').dispatchEvent(new Event('change', {bubbles:true}))`);
        await new Promise(resolve => setTimeout(resolve, 150));
      }
      await js(`document.getElementById('search').value = '__no_match__'; document.getElementById('search').dispatchEvent(new Event('input'))`);
      await new Promise(resolve => setTimeout(resolve, 150));
      check(await js(`document.querySelectorAll('.browser-category').length === 0`), 'context search filters entries');
      await js(`document.getElementById('search').value = ''; document.getElementById('search').dispatchEvent(new Event('input'))`);
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    for (const view of ['timeline', 'memory', 'errors', 'context']) {
      await navigate(view);
      const state = await js(`({title: document.getElementById('page-title').textContent,
        summary: document.getElementById('summary').textContent,
        selected: document.querySelector('[aria-current="page"]').dataset.view,
        entries: document.querySelectorAll('.browser-category, .timeline-event, .memory-canvas, .log-row').length,
        empty: Boolean(document.querySelector('.empty-state')),
        rawMessageFinal: [...document.querySelectorAll('.entry-heading')].some(el => el.textContent === 'message.final')})`);
      check(state.selected === view && (state.entries > 0 || state.empty), `${view} has selected navigation and rendered state`);
      check(!state.rawMessageFinal, `${view} uses readable product event labels`);
      report.pages[view] = state;
      await capture(view);
    }
    await js(`document.querySelector('[data-view="memory"]').click()`); await ready();
    await new Promise(resolve => setTimeout(resolve, 1000));
    const graph = await js(`boxagent.request('memory_snapshot', {node_limit:100,edge_limit:500})`);
    report.graph = {nodes: graph.nodes.length, edges: graph.edges.length};
    const pixels = await js(`(() => {
      const canvas = document.querySelector('.memory-canvas canvas'), data = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
      let count = 0; for(let i=0;i<data.length;i+=4) if(data[i+3] && Math.max(data[i],data[i+1],data[i+2])-Math.min(data[i],data[i+1],data[i+2]) > 40) count++;
      return count;
    })()`);
    check(pixels > 1000 || !graph.nodes.length, 'memory graph draws actual colored nodes on canvas');
    await js(`document.getElementById('page').scrollTop=0; document.activeElement.blur()`); await capture('memory-graph');
    if (graph.nodes.length) {
      window.show(); window.focus();
      await js(`document.querySelector('.memory-canvas').scrollIntoView({block:'center'})`);
      await new Promise(resolve=>setTimeout(resolve,200));
      const point = await js(`(() => {
        const canvas=document.querySelector('.memory-canvas canvas'), ctx=canvas.getContext('2d'), box=canvas.getBoundingClientRect();
        const image=ctx.getImageData(0,0,canvas.width,canvas.height);
        for(let y=0;y<canvas.height;y+=2) for(let x=0;x<canvas.width-40;x+=2) {
          const i=(y*canvas.width+x)*4, j=(y*canvas.width+x+40)*4;
          if(image.data[i]===98 && image.data[i+1]===149 && image.data[i+2]===206 && image.data[j]===98 && image.data[j+1]===149 && image.data[j+2]===206)
            { const point={x:Math.round(box.left+(x+20)*box.width/canvas.width),y:Math.round(box.top+y*box.height/canvas.height)};
              if(point.y > 50 && point.y < innerHeight-20 && document.elementFromPoint(point.x,point.y)===canvas) return point; }
        }
        return null;
      })()`);
      if (point) {
        window.webContents.sendInputEvent({type:'mouseMove',...point});
        window.webContents.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...point});
        window.webContents.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,...point});
        await new Promise(resolve=>setTimeout(resolve,200));
        check(await js(`Boolean(document.querySelector('.inspector-content'))`), 'real canvas mouse click selects memory node');
        const before = await js(`document.querySelector('.memory-canvas canvas').toDataURL()`);
        window.webContents.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...point});
        for(let step=1;step<=6;step++) { window.webContents.sendInputEvent({type:'mouseMove',x:point.x+step*8,y:point.y+step*4}); await new Promise(resolve=>setTimeout(resolve,25)); }
        window.webContents.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,x:point.x+48,y:point.y+24});
        await new Promise(resolve=>setTimeout(resolve,150));
        check(await js(`document.querySelector('.memory-canvas canvas').toDataURL() !== ${JSON.stringify(before)}`), 'native mouse drag moves graph node');
        const beforeZoom = await js(`document.querySelector('.memory-canvas canvas').toDataURL()`);
        window.webContents.sendInputEvent({type:'mouseWheel',x:point.x+48,y:point.y+24,deltaY:120,deltaX:0,canScroll:true});
        await new Promise(resolve=>setTimeout(resolve,200));
        check(await js(`document.querySelector('.memory-canvas canvas').toDataURL() !== ${JSON.stringify(beforeZoom)}`), 'native wheel zoom changes graph view');
      } else throw new Error('No graph node pixel found for mouse selection');
    }
    await js(`document.querySelectorAll('.explorer-heading .segmented button')[1].click()`);
    await new Promise(resolve => setTimeout(resolve, 150));
    check(await js(`document.querySelectorAll('.memory-node-row').length === ${graph.nodes.length}`), 'graph and accessible list share actual nodes');
    if (graph.nodes.length) {
      const connected = [...graph.nodes].sort((a,b)=>graph.edges.filter(e=>e.source===b.id||e.target===b.id).length-graph.edges.filter(e=>e.source===a.id||e.target===a.id).length)[0];
      await js(`[...document.querySelectorAll('.memory-node-row')].find(row=>row.textContent.includes(${JSON.stringify(connected.content)})).click()`); await new Promise(resolve => setTimeout(resolve, 150));
      check(await js(`Boolean(document.querySelector('.inspector-content'))`), 'node selection opens structured inspector');
      await js(`document.querySelector('.node-actions button').click()`);
      for (let i = 0; i < 100; i++) { if(await js(`!document.querySelector('.node-actions button').disabled`)) break; await new Promise(resolve=>setTimeout(resolve,100)); }
      check(await js(`!document.querySelector('.graph-inspector [role="alert"]')`), 'expand neighbors reads graph without mutation');
      await js(`document.querySelectorAll('.explorer-heading .segmented button')[0].click()`); await new Promise(resolve=>setTimeout(resolve,900));
      await js(`document.getElementById('page').scrollTop=0`); await capture('memory-inspector');
    }
    await js(`document.querySelectorAll('.explorer-heading .segmented button')[0].click()`); await new Promise(resolve=>setTimeout(resolve,900));
    if (graph.edges.length) {
      const type = graph.edges[0].type;
      const before = await js(`document.querySelector('.graph-footer').textContent`);
      await js(`document.querySelector('[data-relation="${type}"]').click()`); await new Promise(resolve=>setTimeout(resolve,750));
      check(await js(`document.querySelector('.graph-footer').textContent !== ${JSON.stringify(before)}`), 'relationship filter changes visible graph edge count');
      await js(`document.querySelector('[data-relation="${type}"]').click()`);
    }
    await js(`document.querySelectorAll('.graph-toolbar .segmented button')[1].click()`); await new Promise(resolve=>setTimeout(resolve,200));
    check(await js(`Boolean(document.querySelector('.memory-canvas canvas')) && document.querySelectorAll('.graph-toolbar .segmented button')[1].getAttribute('aria-pressed') === 'true'`), 'type hierarchy layout remains usable');
    await js(`document.querySelectorAll('.graph-toolbar .segmented button')[0].click()`); await new Promise(resolve=>setTimeout(resolve,750));
    await js(`document.getElementById('search').value='__no_match__'; document.getElementById('search').dispatchEvent(new Event('input'))`); await new Promise(resolve=>setTimeout(resolve,150));
    check(await js(`document.querySelector('.graph-footer').textContent.includes('0 个可见节点')`), 'memory search filters graph nodes');
    await js(`document.getElementById('search').value=''; document.getElementById('search').dispatchEvent(new Event('input'))`);
    await js(`(() => {
      for (const view of ['timeline', 'memory', 'context', 'errors', 'timeline']) document.querySelector('[data-view="' + view + '"]').click();
    })()`); await ready();
    check(await js(`document.querySelector('[aria-current="page"]').dataset.view === 'timeline' && Boolean(document.querySelector('.timeline-event')) && !document.querySelector('.memory-explorer, .log-row, .context-console')`), 'rapid page changes discard responses from previous views');
    await js(`document.querySelector('[data-view="context"]').click()`); await ready();
    const emptySession = await js(`(async () => {
      for (const row of await boxagent.request('list_sessions', {include_archived:true})) {
        if ((await boxagent.request('context_snapshot', {session_id:row.session_id})).status === 'empty') return row.session_id;
      }
      return null;
    })()`);
    if (emptySession) {
      await js(`document.querySelector('[data-session-id="${emptySession}"]').click()`); await ready();
      check(await js(`Boolean(document.querySelector('.empty-state')) && !document.querySelector('.context-console')`), 'session without context does not retain previous entries');
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
      await navigate(view);
      check(await js(`document.getElementById('page').scrollWidth <= document.getElementById('page').clientWidth && document.body.scrollWidth <= innerWidth`), `${view} fits narrow window without horizontal overflow`);
    }
    await capture('narrow'); window.setContentSize(1200, 820);
    await navigate('timeline');
    await js(`document.getElementById('search').value='任务'; document.getElementById('search').dispatchEvent(new Event('input'))`);
    await new Promise(resolve=>setTimeout(resolve,150));
    await js(`document.getElementById('page').scrollTop=150`);
    const settingsReturnScroll=await js(`document.getElementById('page').scrollTop`);
    await navigate('settings');
    check(await js(`document.querySelectorAll('.settings-category').length===5 && document.getElementById('settings-button').getAttribute('aria-expanded')==='true'`),'settings overview exposes all five categories');
    await capture('settings');
    window.setContentSize(760,680); await new Promise(resolve=>setTimeout(resolve,150));
    check(await js(`document.getElementById('page').scrollWidth <= document.getElementById('page').clientWidth && document.body.scrollWidth <= innerWidth`),'settings fits narrow window');
    check(await js(`(()=>{const box=document.getElementById('settings-button').getBoundingClientRect();return box.bottom<=innerHeight && box.top>innerHeight-150})()`),'settings entry stays visible at bottom left in narrow window');
    await capture('settings-narrow'); window.setContentSize(1200,820);
    await js(`document.getElementById('settings-back').click()`); await ready();
    check(await js(`document.body.dataset.view==='timeline' && document.getElementById('settings-tabs').hidden && !document.getElementById('settings-button').hasAttribute('aria-current') && document.getElementById('search').value==='任务' && document.getElementById('page').scrollTop===${settingsReturnScroll}`),'settings back restores previous page, search and scroll');
    await js(`document.getElementById('search').value=''; document.getElementById('search').dispatchEvent(new Event('input'))`);

    for(const view of ['conversation','appearance','persona','skills','runs','controls']) {
      await navigate(view);
      check(await js(`Boolean(document.querySelector('.feature-page'))`), `${view} full menu feature is available in panel`);
      if(['appearance','persona','controls'].includes(view)) check(await js(`!document.getElementById('settings-tabs').hidden && document.getElementById('settings-button').getAttribute('aria-current')==='true'`), `${view} is reachable through visible settings navigation`);
      if(view==='appearance') {
        for(let i=0;i<120;i++) { const frame=window.webContents.mainFrame.frames.find(frame=>frame.url.startsWith('http://127.0.0.1')); if(frame && await frame.executeJavaScript(`Boolean(window.pet?.debug().ready)`)) break; await new Promise(resolve=>setTimeout(resolve,200)); }
        const frame=window.webContents.mainFrame.frames.find(frame=>frame.url.startsWith('http://127.0.0.1'));
        check(frame && await frame.executeJavaScript(`window.pet.debug().ready && window.pet.debug().frames > 0`),'3D avatar renders real VRM in embedded preview');
        await js(`document.querySelector('.avatar-layout .primary').click()`);
        await new Promise(resolve=>setTimeout(resolve,400));
        await js(`void document.activeElement.blur()`);
        let applied=false;
        for(let i=0;i<160;i++) { const snapshot=await js(`boxagent.request('desktop_snapshot')`); if(!snapshot.switching&&snapshot.appearance_ready){applied=true;break;}await new Promise(resolve=>setTimeout(resolve,200)); }
        check(applied, 'avatar apply finishes real native preload and replacement');
        await capture('appearance');
      } else if(view==='controls') {
        const before=await js(`boxagent.request('desktop_snapshot')`);
        await js(`[...document.querySelectorAll('.control-settings button')].find(b=>b.textContent==='显示 / 收起').click()`);
        await new Promise(resolve=>setTimeout(resolve,200));
        const after=await js(`boxagent.request('desktop_snapshot')`);
        check(before.bubble_open!==after.bubble_open,'panel action toggles actual desktop conversation window');
        await js(`[...document.querySelectorAll('.control-settings button')].find(b=>b.textContent==='显示 / 收起').click()`);
        await capture(view);
      } else await capture(view);
      window.setContentSize(760,680);
      await new Promise(resolve=>setTimeout(resolve,150));
      check(await js(`document.getElementById('page').scrollWidth <= document.getElementById('page').clientWidth && document.body.scrollWidth <= innerWidth`), `${view} fits narrow window`);
      window.setContentSize(1200,820);
    }
    window.setContentSize(1920,1080);
    for (const view of ['appearance','settings','memory','context','persona','skills','runs','controls','conversation']) {
      await navigate(view);
      await new Promise(resolve=>setTimeout(resolve,180));
      check(await js(`(()=>{const page=document.getElementById('page'),inner=document.querySelector('.page-inner'),style=getComputedStyle(inner);return (inner.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight))/page.clientWidth>.85 && page.scrollWidth<=page.clientWidth})()`), `${view} uses available wide-window content width without overflow`);
      if(view==='appearance') {
        for(let i=0;i<120;i++) {const frame=window.webContents.mainFrame.frames.find(frame=>frame.url.startsWith('http://127.0.0.1'));if(frame && await frame.executeJavaScript(`Boolean(window.pet?.debug().ready)`))break;await new Promise(resolve=>setTimeout(resolve,100));}
        report.wideAvatar = await js(`({viewport:innerHeight,footnoteBottom:document.querySelector('.page-footnote').getBoundingClientRect().bottom,previewHeight:document.querySelector('.avatar-stage iframe').clientHeight})`);
        check(await js(`(()=>{const layout=document.querySelector('.avatar-layout').getBoundingClientRect(),stage=document.querySelector('.avatar-stage').getBoundingClientRect(),frame=document.querySelector('.avatar-stage iframe');return stage.width/layout.width>.65 && frame.clientHeight>400})()`),'wide avatar preview grows while catalog stays compact');
        await capture('appearance-wide');
      }
      if(view==='conversation') check(await js(`document.querySelector('.feature-page').clientWidth<=1040 && [...document.querySelectorAll('.chat-message')].every(el=>el.clientWidth<=720)`),'wide conversation preserves readable message widths');
    }
    await navigate('appearance');
    await new Promise(resolve=>{
      window.once('enter-full-screen',resolve);
      window.setFullScreen(true);
      setTimeout(resolve,2500);
    });
    await new Promise(resolve=>setTimeout(resolve,400));
    check(window.isFullScreen(),'panel enters native full-screen mode');
    check(await js(`(()=>{const page=document.getElementById('page'),layout=document.querySelector('.avatar-layout');return layout.clientWidth/page.clientWidth>.85 && page.scrollWidth<=page.clientWidth})()`),'native full-screen avatar fills main workspace');
    report.fullscreen = await js(`({viewport:innerHeight,footnoteBottom:document.querySelector('.page-footnote').getBoundingClientRect().bottom,previewHeight:document.querySelector('.avatar-stage iframe').clientHeight,contentWidth:document.querySelector('.avatar-layout').clientWidth})`);
    check(await js(`document.querySelector('.page-footnote').getBoundingClientRect().bottom<=innerHeight`),'full-screen avatar keeps bottom explanation visible');
    for(let i=0;i<120;i++) {const frame=window.webContents.mainFrame.frames.find(frame=>frame.url.startsWith('http://127.0.0.1'));if(frame && await frame.executeJavaScript(`Boolean(window.pet?.debug().ready)`))break;await new Promise(resolve=>setTimeout(resolve,100));}
    await new Promise(resolve=>setTimeout(resolve,800));
    await capture('appearance-fullscreen');
    window.setFullScreen(false);
  }
  await fs.writeFile(path.join(directory, 'report.json'), JSON.stringify(report, null, 2));
  await fs.writeFile('/tmp/boxagent-panel-smoke.json', JSON.stringify(report, null, 2));
  await fs.writeFile('/tmp/boxagent-panel-smoke.png', (await window.webContents.capturePage()).toPNG());
  console.log(`Panel smoke: ${report.checks.length} checks passed; ${directory}`);
}
module.exports = { runSmoke };
