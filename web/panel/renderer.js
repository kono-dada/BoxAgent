'use strict';

const $ = id => document.getElementById(id);
const pages = {
  context: { title: '有效上下文', eyebrow: 'CONTEXT', description: '看看这个会话向模型提供了哪些信息。', search: '查找条目摘要或分类', footnote: '展示宿主记录的有效输入。Token 为估算；服务端截断与 KV 缓存命中不可由本页判断。' },
  timeline: { title: '事件时间线', eyebrow: 'TIMELINE', description: '把对话、参考与任务，放回它们发生的时刻。', search: '查找最近 150 条事件', footnote: '展示选中产品会话的最近 150 条记录，按时间排列；点击事件查看完整字段。' },
  memory: { title: '长期记忆', eyebrow: 'MEMORY', description: '那些被留下的信息，成为下一次相处的起点。', search: '查找已载入的记忆', footnote: '展示全局记忆库的最近 100 个节点。本轮召回包请在有效上下文中查看。' },
  errors: { title: '诊断日志', eyebrow: 'DIAGNOSTICS', description: '集中查看最近的问题与线索。', search: '查找已载入的日志', footnote: '展示本机诊断日志的最近 100 条记录，原始字段保留在详情中。' }
};
const categoryColors = { instructions: '#7c9483', persona: '#b2c1b4', tools: '#adb8c7', profile: '#c3b493', checkpoint: '#ab99b2', history: '#b0b8a4', environment: '#c2b38b', memory: '#98b7ac', reference: '#698f80', user: '#a2b8ca', assistant: '#c5cddd', tool_call: '#b2a6ba', tool_result: '#cfbfae', notification: '#d1c89d', system_other: '#c7ccca' };
const categoryLabels = { instructions: '系统指令', persona: '角色设定', tools: '工具定义', profile: '用户画像', checkpoint: '上下文检查点', history: '恢复历史', environment: '环境包', memory: '记忆包', reference: '本轮参考包', user: '用户消息', assistant: '助手回复', tool_call: '工具调用', tool_result: '工具结果', notification: '任务通知', system_other: '其他系统消息' };
const memoryTypes = { EVENT: '事件', ENTITY: '实体', CONCEPT: '概念', FACT: '事实', PREFERENCE: '偏好', PROCEDURE: '方法', SUMMARY: '摘要' };
const memorySources = { automatic: '自动记录', voice_explicit: '用户明确要求', manual: '手动记录' };
const eventLabels = { 'turn/start': '回合开始', 'turn/end': '回合结束', 'user/message': '你说', 'assistant/message': '伙伴回复', 'task/start': '任务开始', 'task/started': '任务开始', 'task/end': '任务结束', 'task/completed': '任务完成', 'task/status': '任务状态', 'task/result': '任务结果', 'context/rebuild': '上下文恢复', 'context/item': '上下文条目', 'context/bound': '参考包绑定', 'context/expired': '参考包到期', 'context/deleted': '参考包删除回执', 'context/response/requested': '请求生成回复', 'context/response/created': '开始生成回复', 'context/turn/start': '模型回合开始', 'context/turn/prepare': '回合前准备', 'migration/source': '历史迁移', 'tool/call': '调用工具', 'tool/result': '工具返回', 'task.accepted': '任务已接收', 'task.started': '任务开始', 'task.completed': '任务完成', 'task.failed': '任务失败', 'task.cancelled': '任务已停止', 'task.progress': '任务进展', 'task.heartbeat': '任务运行中', 'task.approval': '等待授权', 'task.approval_resolved': '授权已处理', 'task.cancelling': '正在停止任务', 'compaction/created': '上下文检查点' };
const runtimeLabels = { idle: '空闲', ready: '就绪', connected: '已连接', connecting: '连接中', disconnected: '未连接', listening: '聆听中', speaking: '回复中', running: '执行中', thinking: '思考中', failed: '失败', completed: '完成', off: '关闭', stopped: '已停止', error: '异常', accepted: '已接收', awaiting_approval: '等待授权', cancelling: '停止中', cancelled: '已停止' };
let selected = '', view = 'context', sessions = [], revision = 0, refreshRevision = 0;
let pageData = null, currentState = {}, detailRevision = 0, detailTrigger = null;
const viewState = new Map();

function element(tag, className = '', text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function icon(name) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  node.setAttribute('class', 'icon'); node.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`); node.append(use); return node;
}
function button(className, action) {
  const node = element('button', className); node.type = 'button'; node.onclick = action; return node;
}
function badge(text, tone = '') { return element('span', `badge ${tone}`, text); }
function number(value) { return Number(value || 0).toLocaleString('zh-CN'); }
function date(value) {
  if (!value) return null;
  const result = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(result.getTime()) ? null : result;
}
function day(value) {
  const parsed = date(value);
  if (!parsed) return '时间未记录';
  const today = new Date(), yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  if (parsed.toDateString() === today.toDateString()) return '今天';
  if (parsed.toDateString() === yesterday.toDateString()) return '昨天';
  return parsed.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
}
function time(value) { return date(value)?.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }) || '—'; }
function matches(...values) {
  const query = $('search').value.trim().toLocaleLowerCase();
  return !query || values.some(value => String(value ?? '').toLocaleLowerCase().includes(query));
}
function key() { return `${view}:${['memory', 'errors'].includes(view) ? 'global' : selected}`; }
function saveView() { viewState.set(key(), { scroll: $('page').scrollTop, search: $('search').value }); }
function updateHeader() {
  const info = pages[view];
  $('page-title').textContent = info.title; $('eyebrow').textContent = info.eyebrow;
  $('description').textContent = info.description; $('search').placeholder = info.search;
  $('footnote').textContent = info.footnote;
  $('scope').textContent = ['memory', 'errors'].includes(view) ? '本机 / 全局记录' : sessions.find(row => row.session_id === selected)?.title || '产品会话';
  document.querySelectorAll('[data-view]').forEach(node => {
    if (node.dataset.view === view) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
  });
}
function connection(online, message) {
  $('connection').dataset.state = online ? 'online' : 'offline';
  $('status').textContent = online ? 'Engine 已连接' : 'Engine 未连接';
  $('connection').title = message || '';
}
function updateState(state) {
  currentState = { ...currentState, ...state };
  $('runtime-status').textContent = `语音 ${runtimeLabels[currentState.voice] || currentState.voice || '—'} · 任务 ${runtimeLabels[currentState.task] || currentState.task || '—'}`;
}
function empty(title, message, name = view, retry = false) {
  const node = element('div', 'empty-state');
  const symbol = element('div', 'empty-symbol'); symbol.append(icon(name));
  node.append(symbol, element('h2', '', title), element('p', '', message));
  if (retry) { const action = button('button', refresh); action.textContent = '重新连接 / 刷新'; node.append(action); }
  $('content').append(node);
}
function show(value, title = '记录详情', meta = '', trigger = document.activeElement) {
  detailRevision += 1; detailTrigger = trigger;
  $('detail-title').textContent = title; $('detail-meta').textContent = meta;
  $('full').textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  if (!$('detail').open) $('detail').showModal();
}
async function showEntry(row, sessionId, trigger) {
  show('正在读取完整内容…', row.label, `条目 #${row.seq} · 约 ${number(row.tokens)} tokens`, trigger);
  const mine = detailRevision;
  try {
    const result = await boxagent.request('context_entry', { session_id: sessionId, seq: row.seq });
    if (mine !== detailRevision || !$('detail').open) return;
    $('full').textContent = result.status === 'ready' ? result.content : '该条目暂时无法读取，请刷新后重试。';
  } catch (error) { if (mine === detailRevision && $('detail').open) $('full').textContent = `读取失败：${error.message}`; }
}
function notice(text, warning = false) {
  const node = element('div', `notice${warning ? ' warning' : ''}`); node.append(icon('info'), element('span', '', text)); $('content').append(node);
}
function resultCount(count, total, label = '条') { $('result-count').textContent = count === total ? `${number(count)} ${label}` : `${number(count)} / ${number(total)} ${label}`; }
function entryRow(row, sessionId) {
  const node = button('entry-row', () => showEntry(row, sessionId, node));
  const symbol = element('span', 'entry-symbol'); symbol.append(icon(['user', 'assistant', 'history'].includes(row.category) ? 'chat' : ['tool_call', 'tool_result', 'notification'].includes(row.category) ? 'task' : row.category === 'memory' ? 'memory' : 'context'));
  const copy = element('span', 'entry-copy'), heading = element('span', 'entry-heading', row.label);
  if (row.category === 'reference') heading.append(badge('本轮有效', 'reference'));
  copy.append(heading, element('span', 'entry-preview', row.preview || '无文本内容 · 点击查看记录'));
  const tail = element('span', 'entry-tail'); tail.append(element('span', '', `约 ${number(row.tokens)} tokens`), icon('arrow'));
  node.append(symbol, copy, tail); return node;
}
function renderContext(state) {
  if (state.status !== 'ready') { $('summary').textContent = '等待会话上下文'; empty('这里还没有上下文', state.message || '开启对话后，再来看看伙伴记住了哪些信息。', 'context'); return; }
  const all = state.entries || [], rows = all.filter(row => matches(row.preview, row.label, row.category, row.seq));
  $('summary').textContent = `${state.model || state.runtime || 'Qwen'} · 第 ${number(state.generation)} 次连接的记录`;
  resultCount(rows.length, all.length, '项');
  const overview = element('section', 'overview'), top = element('div', 'overview-top'), value = element('div');
  const metric = element('div', 'overview-number', number(state.totals.tokens)); metric.append(element('small', '', '估算 tokens'));
  value.append(element('div', 'overview-label', '有效上下文总量'), metric);
  const metadata = element('div', 'overview-meta'); metadata.append(element('div', '', `${number(state.totals.entries)} 个有效条目`), element('div', '', `${number(state.delete_ack_count)} 个删除回执 · ${number(state.requests?.length)} 次请求`));
  top.append(value, metadata); overview.append(top);
  const bar = element('div', 'composition'), legend = element('div', 'legend');
  (state.composition || []).filter(row => row.tokens).forEach(row => {
    const color = categoryColors[row.key] || '#c7ccca';
    const segment = element('span'); segment.style.width = `${Math.max(0, Math.min(100, row.percent))}%`; segment.style.backgroundColor = color; segment.title = `${row.label} · ${row.percent}% · 约 ${number(row.tokens)} tokens`; bar.append(segment);
    const item = element('span', 'legend-item'), dot = element('span', 'legend-dot'); dot.style.backgroundColor = color;
    item.append(dot, document.createTextNode(`${row.label} ${row.percent}%`)); legend.append(item);
  });
  overview.append(bar, legend); $('content').append(overview);
  const localSeqs = new Set(all.map(row => row.seq));
  if ((state.provider_entries || []).some(row => !localSeqs.has(row.seq))) notice('部分本地已到期条目尚未收到服务端删除回执，服务端可能仍保留这些条目。', true);
  const groups = [
    { title: '角色与基础信息', categories: ['instructions', 'persona', 'tools', 'profile', 'checkpoint'] },
    { title: '对话与正式历史', categories: ['history', 'user', 'assistant'] },
    { title: '当前回合的参考', categories: ['reference', 'memory', 'environment'] },
    { title: '工具与其他信息', categories: ['tool_call', 'tool_result', 'notification', 'system_other'] }
  ];
  const known = groups.flatMap(group => group.categories);
  const sessionId = selected;
  groups.forEach((group, index) => {
    const items = rows.filter(row => group.categories.includes(row.category) || (index === groups.length - 1 && !known.includes(row.category)));
    if (!items.length) return;
    const section = element('section', 'group'), heading = element('h2', 'group-heading', group.title);
    heading.append(element('span', '', `${items.length} 项`));
    const list = element('div', 'entry-list'); items.forEach(row => list.append(entryRow(row, sessionId)));
    section.append(heading, list); $('content').append(section);
  });
  if (!rows.length) empty($('search').value ? '没有匹配的条目' : '暂无有效条目', $('search').value ? '换个关键词试试。' : '上下文建立后，条目会出现在这里。', 'search');
  if (!$('search').value && state.requests?.length) {
    const section = element('details', 'request-group'); section.append(element('summary', '', `请求快照 · 最近 ${Math.min(10, state.requests.length)} 次`));
    state.requests.slice(-10).reverse().forEach(row => {
      const node = button('request-row', () => show(row, `请求 #${row.seq}`, '宿主记录的有效条目与增删快照', node));
      const changes = element('span', 'metadata'); changes.append(element('span', '', `新增 ${row.added.length}`), element('span', '', `移除 ${row.removed.length}`), icon('arrow'));
      node.append(element('span', '', `${time(row.time)} · ${row.entries.length} 个条目`), changes); section.append(node);
    }); $('content').append(section);
  }
}
function normalizeEvent(row) {
  const data = row.data?.product_event || { ...row, ...row.data };
  const legacyType = row.type === 'message.final' ? `${row.role}/message` : ({ 'interaction.started': 'turn/start', 'interaction.finalized': 'turn/end', 'context.checkpoint.created': 'compaction/created' })[row.type] || row.type;
  const type = row.type === 'context/item' || row.data?.product_event ? row.type : legacyType;
  const label = type === 'context/item' ? categoryLabels[data.category] || '上下文条目' : eventLabels[type] || type;
  let preview = data.content || data.task_text || data.data?.task_text || data.status || data.reference || '';
  if (!preview && data.category) preview = `分类：${data.category}`;
  if (!preview && type.includes('prepare')) {
    const fields = ['memory', 'jev', 'intent', 'environment'].filter(name => data[name]);
    preview = fields.length ? `准备项目：${fields.join(' · ')}` : '记录当前回合参考信息准备状态';
  }
  return { original: row, type, label, seq: row.seq || row.sequence, time: row.time || data.occurred_at || row.occurred_at, runtime: data.runtime || row.runtime || '', query: data.query_id || data.interaction_id || data.data?.query_id || '', preview: typeof preview === 'string' ? preview.trim() : JSON.stringify(preview) };
}
function renderTimeline(data) {
  const all = data.map(normalizeEvent), rows = all.filter(row => matches(row.label, row.type, row.preview, row.query, row.runtime, row.seq));
  $('summary').textContent = `本会话最近 ${number(all.length)} 条事件 · 对话、上下文与任务`;
  resultCount(rows.length, all.length);
  if (!rows.length) { empty(all.length ? '没有匹配的事件' : '时间线还很安静', all.length ? '试试消息内容、事件类型或回合 ID。' : '发生对话或任务后，记录会出现在这里。', all.length ? 'search' : 'timeline'); return; }
  const days = new Map();
  rows.forEach(row => { const label = day(row.time); if (!days.has(label)) days.set(label, []); days.get(label).push(row); });
  days.forEach((items, label) => {
    const section = element('section', 'timeline-day'); section.append(element('h2', 'day-title', label));
    items.forEach(row => {
      const node = element('div', 'timeline-row'), rail = element('div', 'timeline-rail'); rail.append(element('span', 'timeline-dot'));
      const action = button('timeline-event', () => show(row.original, row.label, `${day(row.time)} ${time(row.time)} · 事件 #${row.seq}`, action));
      const heading = element('span', 'entry-heading', row.label);
      if (row.type.startsWith('context/')) heading.append(badge('上下文')); else if (row.type.startsWith('task/') || row.type.startsWith('tool/')) heading.append(badge('任务'));
      action.append(heading);
      if (row.preview) action.append(element('span', 'entry-preview', row.preview));
      const meta = element('span', 'timeline-meta'); meta.append(element('span', '', `#${row.seq}`));
      if (row.runtime) meta.append(element('span', '', row.runtime));
      if (row.query) meta.append(element('span', '', `回合 ${String(row.query).slice(-8)}`));
      action.append(meta); node.append(element('span', 'timeline-time', time(row.time)), rail, action); section.append(node);
    }); $('content').append(section);
  });
}
function renderMemory(graph) {
  const all = graph.nodes || [], rows = all.filter(row => matches(row.content, row.type, row.source, row.id));
  $('summary').textContent = '全局记忆库 · 与单次对话的召回包分别查看'; resultCount(rows.length, all.length, '条');
  const overview = element('div', 'memory-overview'); overview.append(element('strong', '', number(graph.statistics?.node_count ?? all.length)), document.createTextNode('条记忆'), element('span', 'metadata', `· 已载入 ${all.length} 条`)); $('content').append(overview);
  if (!rows.length) { empty(all.length ? '没有匹配的记忆' : '记忆正在慢慢积累', all.length ? '试试记忆内容、类型或来源。' : '记忆库中的信息会在这里显示。', all.length ? 'search' : 'memory'); return; }
  const grid = element('div', 'memory-grid');
  rows.forEach(row => {
    const node = button('memory-card', () => show(row, '记忆详情', [row.type, row.id].filter(Boolean).join(' · '), node));
    const top = element('span', 'memory-card-top'); top.append(badge(memoryTypes[row.type] || row.type || '记忆'), icon('memory'));
    const meta = [row.timestamp ? day(row.timestamp) : '', row.source ? `来源：${memorySources[row.source] || row.source}` : ''].filter(Boolean).join(' · ');
    node.append(top, element('span', 'memory-body', row.content || '该节点没有文本内容'), element('span', 'metadata', meta || '点击查看完整记录'));
    grid.append(node);
  }); $('content').append(grid);
}
function renderErrors(data) {
  const all = [...data].reverse(), rows = all.filter(row => matches(JSON.stringify(row)));
  const errors = all.filter(row => ['error', 'critical'].includes(String(row.level).toLowerCase())).length;
  $('summary').textContent = `最近 ${all.length} 条诊断记录 · ${errors} 条错误`; resultCount(rows.length, all.length);
  if (!rows.length) { empty(all.length ? '没有匹配的日志' : '暂时没有诊断记录', all.length ? '试试问题摘要、模块名或关联 ID。' : '本机 warning 和 error 记录会汇总在这里。', all.length ? 'search' : 'check'); return; }
  const list = element('div', 'log-list');
  rows.forEach(row => {
    const rawTitle = row.msg || row.message || row.event || row.error || '诊断记录';
    const title = rawTitle === 'logging.ready' ? '诊断日志已就绪' : rawTitle;
    const value = row.ts || row.time || row.timestamp || row.occurred_at;
    const node = button('log-row', () => show(row, '诊断详情', `${day(value)} ${time(value)}`, node));
    const when = element('span', 'log-time'); when.append(element('span', '', day(value)), element('br'), document.createTextNode(time(value)));
    const copy = element('span', 'entry-copy'); copy.append(element('span', 'log-title', typeof title === 'string' ? title : JSON.stringify(title)));
    const sessionId = row.session_id || row.data?.session_id, taskId = row.task_id || row.data?.task_id;
    copy.append(element('span', 'metadata', [row.logger || row.module || row.process, sessionId ? `会话 ${String(sessionId).slice(-8)}` : '', taskId ? `任务 ${String(taskId).slice(-8)}` : ''].filter(Boolean).join(' · ') || '本机日志'));
    const level = String(row.level || 'warning').toLowerCase();
    const tone = ['error', 'critical'].includes(level) ? 'error' : level === 'warning' ? 'warning' : '';
    node.append(when, copy, badge(level.toUpperCase(), tone)); list.append(node);
  }); $('content').append(list);
}
function render() {
  if (pageData === null) return;
  $('content').replaceChildren(); $('result-count').textContent = '';
  ({ context: renderContext, timeline: renderTimeline, memory: renderMemory, errors: renderErrors })[view](pageData);
}
function loading() {
  pageData = null; $('content').replaceChildren(); $('content').setAttribute('aria-busy', 'true');
  $('summary').textContent = '正在读取…'; $('result-count').textContent = '';
  for (let i = 0; i < 3; i += 1) { const node = element('div', 'skeleton'); node.setAttribute('aria-hidden', 'true'); $('content').append(node); }
}
async function load({ restore = false } = {}) {
  const scroll = restore ? viewState.get(key())?.scroll || 0 : $('page').scrollTop;
  const mine = ++revision; updateHeader(); loading();
  try {
    let result;
    if (view === 'errors') result = await boxagent.errors();
    else if (view === 'memory') result = await boxagent.request('memory_snapshot', { node_limit: 100, edge_limit: 100 });
    else if (!selected) {
      $('content').replaceChildren(); $('summary').textContent = '尚未选择产品会话';
      empty('从一次对话开始', '有会话后，可以在左侧选择并查看它的上下文与时间线。', 'chat'); return;
    } else result = await boxagent.request(view === 'timeline' ? 'session_timeline' : 'context_snapshot', { session_id: selected, ...(view === 'timeline' ? { limit: 150 } : {}) });
    if (mine !== revision) return;
    pageData = result; render();
    $('page').scrollTop = scroll;
  } catch (error) {
    if (mine !== revision) return;
    $('content').replaceChildren(); $('summary').textContent = '暂时无法读取';
    empty('数据暂时不可用', error.message, 'info', true);
  } finally { if (mine === revision) $('content').setAttribute('aria-busy', 'false'); }
}
function renderSessions() {
  const scroll = $('sessions').scrollTop; $('sessions').replaceChildren(); $('session-count').textContent = sessions.length;
  sessions.forEach(row => {
    const node = button('', () => {
      if (selected === row.session_id) return;
      saveView(); selected = row.session_id; $('search').value = viewState.get(key())?.search || '';
      renderSessions(); load({ restore: true });
    });
    node.dataset.sessionId = row.session_id; node.title = row.title;
    if (row.session_id === selected) node.setAttribute('aria-current', 'true');
    const copy = element('span', 'session-copy'); copy.append(element('span', 'session-title', row.title || '未命名会话'), element('span', 'session-date', [day(row.updated_at), row.archived ? '已归档' : ''].filter(Boolean).join(' · ')));
    node.append(icon('chat'), copy); $('sessions').append(node);
  });
  if (!sessions.length) $('sessions').append(element('p', 'sidebar-empty', '还没有会话'));
  $('sessions').scrollTop = scroll;
}
async function refresh() {
  const mine = ++refreshRevision; ++revision; $('refresh').disabled = true;
  try {
    const [stateResult, sessionsResult] = await Promise.allSettled([boxagent.request('state'), boxagent.request('list_sessions', { include_archived: true })]);
    if (mine !== refreshRevision) return;
    if (stateResult.status === 'fulfilled') { updateState(stateResult.value); connection(true); }
    else { connection(false, stateResult.reason.message); $('runtime-status').textContent = '等待 Engine 连接'; }
    if (sessionsResult.status === 'fulfilled') {
      sessions = sessionsResult.value;
      if (!sessions.some(row => row.session_id === selected)) selected = sessions.find(row => row.session_id === currentState.session_id)?.session_id || sessions[0]?.session_id || '';
      renderSessions();
    }
    if (sessionsResult.status === 'rejected' && !['errors', 'memory'].includes(view)) {
      updateHeader(); pageData = null; $('content').replaceChildren(); $('content').setAttribute('aria-busy', 'false');
      $('summary').textContent = '等待连接到本机 Engine';
      empty('连接后，继续查看伙伴的记录', '先启动桌宠或 Engine。面板会自动重连，也可以点击刷新。', 'context', true);
      return;
    }
    await load();
  } finally { if (mine === refreshRevision) $('refresh').disabled = false; }
}
$('refresh').onclick = refresh;
$('close').onclick = () => $('detail').close();
$('detail').addEventListener('close', () => { detailRevision += 1; if (detailTrigger?.isConnected) detailTrigger.focus({ preventScroll: true }); });
$('detail').addEventListener('click', event => { if (event.target === $('detail')) { const box = $('detail').getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) $('detail').close(); } });
$('search').addEventListener('input', () => { render(); $('page').scrollTop = 0; });
document.querySelectorAll('[data-view]').forEach(node => { node.onclick = () => {
  if (view === node.dataset.view) return;
  saveView(); view = node.dataset.view; $('search').value = viewState.get(key())?.search || '';
  $('page').scrollTop = 0; load({ restore: true });
}; });
boxagent.onState(state => { connection(true); updateState(state); });
boxagent.onDiagnostic(message => {
  if (message === 'Engine 已连接') { connection(true); refresh(); }
  else { connection(false, message); $('runtime-status').textContent = '等待 Engine 连接'; }
});
refresh();
