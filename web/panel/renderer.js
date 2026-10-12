import { mountComplexPage, disposeComplexPage } from './src/mount';

const $ = id => document.getElementById(id);
const pages = {
  context: { title: '有效上下文', eyebrow: 'CONTEXT', description: '看看这个会话向模型提供了哪些信息。', search: '查找条目摘要或分类', footnote: '展示宿主记录的有效输入。Token 为估算；服务端截断与 KV 缓存命中不可由本页判断。' },
  timeline: { title: '事件时间线', eyebrow: 'TIMELINE', description: '把对话、参考与任务，放回它们发生的时刻。', search: '查找最近 150 条事件', footnote: '展示选中产品会话的最近 150 条记录，按时间排列；点击事件查看完整字段。' },
  memory: { title: '长期记忆', eyebrow: 'MEMORY', description: '从一次相处，到一个相互关联的记忆世界。', search: '查找已载入的记忆', footnote: '初始载入最近 100 个节点和最多 500 条关系。展开邻居读取选中节点的直接关联；筛选仅作用于已载入图。' },
  errors: { title: '诊断日志', eyebrow: 'DIAGNOSTICS', description: '集中查看最近的问题与线索。', search: '查找已载入的日志', footnote: '展示本机诊断日志的最近 100 条记录，原始字段保留在详情中。' }
};
const categoryLabels = { instructions: '系统指令', persona: '角色设定', tools: '工具定义', profile: '用户画像', checkpoint: '上下文检查点', history: '恢复历史', environment: '环境包', memory: '记忆包', reference: '本轮参考包', user: '用户消息', assistant: '助手回复', tool_call: '工具调用', tool_result: '工具结果', notification: '任务通知', system_other: '其他系统消息' };
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
  document.body.dataset.view = view;
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
function resultCount(count, total, label = '条') { $('result-count').textContent = count === total ? `${number(count)} ${label}` : `${number(count)} / ${number(total)} ${label}`; }
function renderContext(state) {
  $('summary').textContent = '等待会话上下文';
  empty('这里还没有上下文', state.message || '开启对话后，再来看看伙伴记住了哪些信息。', 'context');
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
  if (view === 'memory' || (view === 'context' && pageData.status === 'ready')) {
    $('result-count').textContent = '';
    $('summary').textContent = view === 'memory' ? '记忆关系与结构 · 选择节点探索关联' : `${pageData.model || 'Qwen'} · 宿主记录的请求控制台`;
    mountComplexPage($('content'), view, pageData, boxagent, $('search').value.trim(), show);
    return;
  }
  disposeComplexPage();
  $('content').replaceChildren(); $('result-count').textContent = '';
  ({ context: renderContext, timeline: renderTimeline, errors: renderErrors })[view](pageData);
}
function loading() {
  disposeComplexPage();
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
    else if (view === 'memory') result = await boxagent.request('memory_snapshot', { node_limit: 100, edge_limit: 500 });
    else if (!selected) {
      $('content').replaceChildren(); $('summary').textContent = '尚未选择产品会话';
      empty('从一次对话开始', '有会话后，可以在左侧选择并查看它的上下文与时间线。', 'chat'); return;
    } else if (view === 'context') {
      const [snapshot, timeline] = await Promise.all([boxagent.request('context_snapshot', { session_id: selected }), boxagent.request('session_timeline', { session_id: selected, limit: 500 })]);
      result = { ...snapshot, timeline };
    } else result = await boxagent.request('session_timeline', { session_id: selected, limit: 150 });
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
