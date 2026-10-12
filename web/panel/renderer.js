import { t } from './src/i18n';
import { locale, startPreferences } from './src/preferences';
import { mountComplexPage, disposeComplexPage } from './src/mount';

const $ = id => document.getElementById(id);
const settingViews = new Set(['settings', 'appearance', 'persona', 'controls', 'errors', 'interface']);
let previousMainView = 'conversation';
const pages = {
  interface: { get title() { return t('界面偏好'); }, eyebrow:'SETTINGS / INTERFACE', get description() { return t('选择你习惯的语言和界面明暗。'); }, search:'', get footnote() { return t('设置会自动保存，下次打开时继续使用。'); } },
  settings: { get title() { return t("设置"); }, eyebrow:'SETTINGS', get description() { return t("调整伙伴的形象、性格和运行方式。"); }, search:'', get footnote() { return t("设置改变伙伴的配置；对话、记忆与任务记录仍在主导航中。"); } },
  conversation: { get title() { return t("对话与会话"); }, eyebrow:'CONVERSATION', get description() { return t("继续相处，也可以从一个新话题开始。"); }, get search() { return t("查找消息"); }, get footnote() { return t("左侧选择查看的会话；发送前须将它设为当前对话。"); } },
  appearance: { get title() { return t("3D 形象"); }, eyebrow:'AVATAR', get description() { return t("选择陪在桌面上的伙伴，预览它的表情与动作。"); }, get search() { return t("查找形象"); }, get footnote() { return t("预览不会替换桌宠；应用后加载成功才保存选择。"); } },
  persona: { get title() { return t("角色设定"); }, eyebrow:'PERSONA', get description() { return t("名字、性格、语气和你们相处的方式。"); }, search:'', get footnote() { return t("保存到原来的 SOUL.md；后台在安全时机同步。"); } },
  skills: { get title() { return t("Skill 管理"); }, eyebrow:'SKILLS', get description() { return t("管理伙伴可以使用的能力与方法。"); }, get search() { return t("查找 Skill"); }, get footnote() { return t("内置 Skill 可启停，用户 Skill 可新建、编辑和删除。"); } },
  runs: { get title() { return t("任务与运行记录"); }, eyebrow:'RUNS', get description() { return t("查看后台任务状态、授权和每次执行的产物。"); }, get search() { return t("查找运行记录"); }, get footnote() { return t("按左侧选中会话读取运行记录，打开按钮使用原生 Finder。"); } },
  controls: { get title() { return t("语音与桌面"); }, eyebrow:'SETTINGS / CONTROLS', get description() { return t("管理麦克风、屏幕总结与桌面小窗。"); }, search:'', get footnote() { return t("退出伙伴会关闭桌宠和 Engine；面板可单独关闭。"); } },
  context: { get title() { return t("有效上下文"); }, eyebrow: 'CONTEXT', get description() { return t("看看这个会话向模型提供了哪些信息。"); }, get search() { return t("查找条目摘要或分类"); }, get footnote() { return t("展示宿主记录的有效输入。Token 为估算；服务端截断与 KV 缓存命中不可由本页判断。"); } },
  timeline: { get title() { return t("事件时间线"); }, eyebrow: 'TIMELINE', get description() { return t("把对话、参考与任务，放回它们发生的时刻。"); }, get search() { return t("查找最近 150 条事件"); }, get footnote() { return t("展示选中产品会话的最近 150 条记录，按时间排列；点击事件查看完整字段。"); } },
  memory: { get title() { return t("长期记忆"); }, eyebrow: 'MEMORY', get description() { return t("从一次相处，到一个相互关联的记忆世界。"); }, get search() { return t("查找已载入的记忆"); }, get footnote() { return t("初始载入最近 100 个节点和最多 500 条关系。展开邻居读取选中节点的直接关联；筛选仅作用于已载入图。"); } },
  errors: { get title() { return t("诊断日志"); }, eyebrow: 'DIAGNOSTICS', get description() { return t("集中查看最近的问题与线索。"); }, get search() { return t("查找已载入的日志"); }, get footnote() { return t("展示本机诊断日志的最近 100 条记录，原始字段保留在详情中。"); } }
};
const categoryLabels = { get instructions() { return t("系统指令"); }, get persona() { return t("角色设定"); }, get tools() { return t("工具定义"); }, get profile() { return t("用户画像"); }, get checkpoint() { return t("上下文检查点"); }, get history() { return t("恢复历史"); }, get environment() { return t("环境包"); }, get memory() { return t("记忆包"); }, get reference() { return t("本轮参考包"); }, get user() { return t("用户消息"); }, get assistant() { return t("助手回复"); }, get tool_call() { return t("工具调用"); }, get tool_result() { return t("工具结果"); }, get notification() { return t("任务通知"); }, get system_other() { return t("其他系统消息"); } };
const eventLabels = { get 'turn/start'() { return t("回合开始"); }, get 'turn/end'() { return t("回合结束"); }, get 'user/message'() { return t("你说"); }, get 'assistant/message'() { return t("伙伴回复"); }, get 'task/start'() { return t("任务开始"); }, get 'task/started'() { return t("任务开始"); }, get 'task/end'() { return t("任务结束"); }, get 'task/completed'() { return t("任务完成"); }, get 'task/status'() { return t("任务状态"); }, get 'task/result'() { return t("任务结果"); }, get 'context/rebuild'() { return t("上下文恢复"); }, get 'context/item'() { return t("上下文条目"); }, get 'context/bound'() { return t("参考包绑定"); }, get 'context/expired'() { return t("参考包到期"); }, get 'context/deleted'() { return t("参考包删除回执"); }, get 'context/response/requested'() { return t("请求生成回复"); }, get 'context/response/created'() { return t("开始生成回复"); }, get 'context/turn/start'() { return t("模型回合开始"); }, get 'context/turn/prepare'() { return t("回合前准备"); }, get 'migration/source'() { return t("历史迁移"); }, get 'tool/call'() { return t("调用工具"); }, get 'tool/result'() { return t("工具返回"); }, get 'task.accepted'() { return t("任务已接收"); }, get 'task.started'() { return t("任务开始"); }, get 'task.completed'() { return t("任务完成"); }, get 'task.failed'() { return t("任务失败"); }, get 'task.cancelled'() { return t("任务已停止"); }, get 'task.progress'() { return t("任务进展"); }, get 'task.heartbeat'() { return t("任务运行中"); }, get 'task.approval'() { return t("等待授权"); }, get 'task.approval_resolved'() { return t("授权已处理"); }, get 'task.cancelling'() { return t("正在停止任务"); }, get 'compaction/created'() { return t("上下文检查点"); } };
const runtimeLabels = { get idle() { return t("空闲"); }, get ready() { return t("就绪"); }, get connected() { return t("已连接"); }, get connecting() { return t("连接中"); }, get disconnected() { return t("未连接"); }, get listening() { return t("聆听中"); }, get speaking() { return t("回复中"); }, get running() { return t("执行中"); }, get thinking() { return t("思考中"); }, get failed() { return t("失败"); }, get completed() { return t("完成"); }, get off() { return t("关闭"); }, get stopped() { return t("已停止"); }, get error() { return t("异常"); }, get accepted() { return t("已接收"); }, get awaiting_approval() { return t("等待授权"); }, get cancelling() { return t("停止中"); }, get cancelled() { return t("已停止"); } };
let selected = '', view = 'conversation', sessions = [], revision = 0, refreshRevision = 0;
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
function number(value) { return Number(value || 0).toLocaleString(locale()); }
function date(value) {
  if (!value) return null;
  const result = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(result.getTime()) ? null : result;
}
function day(value) {
  const parsed = date(value);
  if (!parsed) return t("时间未记录");
  const today = new Date(), yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  if (parsed.toDateString() === today.toDateString()) return t("今天");
  if (parsed.toDateString() === yesterday.toDateString()) return t("昨天");
  return parsed.toLocaleDateString(locale(), { year: 'numeric', month: 'long', day: 'numeric' });
}
function time(value) { return date(value)?.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', hour12: false }) || '—'; }
function matches(...values) {
  const query = $('search').value.trim().toLocaleLowerCase();
  return !query || values.some(value => String(value ?? '').toLocaleLowerCase().includes(query));
}
function key() { return `${view}:${['memory', 'errors', 'appearance', 'persona', 'skills', 'controls', 'settings', 'interface'].includes(view) ? 'global' : selected}`; }
function saveView() { viewState.set(key(), { scroll: $('page').scrollTop, search: $('search').value }); }
function updateHeader() {
  document.body.dataset.view = view;
  const info = pages[view];
  $('settings-tabs').hidden = !settingViews.has(view);
  $('settings-button').setAttribute('aria-expanded', String(settingViews.has(view)));
  $('settings-back').textContent = t("返回{0}", pages[previousMainView].title);
  $('page-title').textContent = info.title; $('eyebrow').textContent = info.eyebrow;
  $('description').textContent = info.description; $('search').placeholder = info.search;
  $('footnote').textContent = info.footnote;
  document.querySelector('.content-tools').hidden = ['settings','persona','controls','interface'].includes(view);
  $('scope').textContent = ['memory', 'errors', 'appearance', 'persona', 'skills', 'controls', 'settings', 'interface'].includes(view) ? t("本机 / 全局记录") : sessions.find(row => row.session_id === selected)?.title || t("产品会话");
  document.querySelectorAll('[data-view]').forEach(node => {
    const active = node.id === 'settings-button' ? settingViews.has(view) : node.dataset.view === view;
    if (active) node.setAttribute('aria-current', node.id === 'settings-button' ? 'true' : 'page'); else node.removeAttribute('aria-current');
  });
}
function connection(online, message) {
  $('connection').dataset.state = online ? 'online' : 'offline';
  $('status').textContent = online ? t("已连接") : t("未连接");
  $('connection').title = message || (online ? t("本机 Engine 已连接") : t("本机 Engine 未连接"));
}
function updateState(state) {
  currentState = { ...currentState, ...state };
  document.dispatchEvent(new CustomEvent('panel-state', {detail:currentState}));
  $('runtime-status').textContent = t("语音 {0} · 任务 {1}", runtimeLabels[currentState.voice] || currentState.voice || '—', runtimeLabels[currentState.task] || currentState.task || '—');
}
function empty(title, message, name = view, retry = false) {
  const node = element('div', 'empty-state');
  const symbol = element('div', 'empty-symbol'); symbol.append(icon(name));
  node.append(symbol, element('h2', '', title), element('p', '', message));
  if (retry) { const action = button('button', refresh); action.textContent = t("重新连接 / 刷新"); node.append(action); }
  $('content').append(node);
}
function show(value, title = t("记录详情"), meta = '', trigger = document.activeElement) {
  detailRevision += 1; detailTrigger = trigger;
  $('detail-title').textContent = title; $('detail-meta').textContent = meta;
  $('full').textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  if (!$('detail').open) $('detail').showModal();
}
function resultCount(count, total, label = t("条")) { $('result-count').textContent = count === total ? `${number(count)} ${label}` : `${number(count)} / ${number(total)} ${label}`; }
function renderContext(state) {
  $('summary').textContent = t("等待会话上下文");
  empty(t("这里还没有上下文"), state.message || t("开启对话后，再来看看伙伴记住了哪些信息。"), 'context');
}
function normalizeEvent(row) {
  const data = row.data?.product_event || { ...row, ...row.data };
  const legacyType = row.type === 'message.final' ? `${row.role}/message` : ({ 'interaction.started': 'turn/start', 'interaction.finalized': 'turn/end', 'context.checkpoint.created': 'compaction/created' })[row.type] || row.type;
  const type = row.type === 'context/item' || row.data?.product_event ? row.type : legacyType;
  const label = type === 'context/item' ? categoryLabels[data.category] || t("上下文条目") : eventLabels[type] || type;
  let preview = data.content || data.task_text || data.data?.task_text || data.status || data.reference || '';
  if (!preview && data.category) preview = t("分类：{0}", data.category);
  if (!preview && type.includes('prepare')) {
    const fields = ['memory', 'jev', 'intent', 'environment'].filter(name => data[name]);
    preview = fields.length ? t("准备项目：{0}", fields.join(' · ')) : t("记录当前回合参考信息准备状态");
  }
  return { original: row, type, label, seq: row.seq || row.sequence, time: row.time || data.occurred_at || row.occurred_at, runtime: data.runtime || row.runtime || '', query: data.query_id || data.interaction_id || data.data?.query_id || '', preview: typeof preview === 'string' ? preview.trim() : JSON.stringify(preview) };
}
function renderTimeline(data) {
  const all = data.map(normalizeEvent), rows = all.filter(row => matches(row.label, row.type, row.preview, row.query, row.runtime, row.seq));
  $('summary').textContent = t("本会话最近 {0} 条事件 · 对话、上下文与任务", number(all.length));
  resultCount(rows.length, all.length);
  if (!rows.length) { empty(all.length ? t("没有匹配的事件") : t("时间线还很安静"), all.length ? t("试试消息内容、事件类型或回合 ID。") : t("发生对话或任务后，记录会出现在这里。"), all.length ? 'search' : 'timeline'); return; }
  const days = new Map();
  rows.forEach(row => { const label = day(row.time); if (!days.has(label)) days.set(label, []); days.get(label).push(row); });
  days.forEach((items, label) => {
    const section = element('section', 'timeline-day'); section.append(element('h2', 'day-title', label));
    items.forEach(row => {
      const node = element('div', 'timeline-row'), rail = element('div', 'timeline-rail'); rail.append(element('span', 'timeline-dot'));
      const action = button('timeline-event', () => show(row.original, row.label, t("{0} {1} · 事件 #{2}", day(row.time), time(row.time), row.seq), action));
      const heading = element('span', 'entry-heading', row.label);
      if (row.type.startsWith('context/')) heading.append(badge(t("上下文"))); else if (row.type.startsWith('task/') || row.type.startsWith('tool/')) heading.append(badge(t("任务")));
      action.append(heading);
      if (row.preview) action.append(element('span', 'entry-preview', row.preview));
      const meta = element('span', 'timeline-meta'); meta.append(element('span', '', `#${row.seq}`));
      if (row.runtime) meta.append(element('span', '', row.runtime));
      if (row.query) meta.append(element('span', '', t("回合 {0}", String(row.query).slice(-8))));
      action.append(meta); node.append(element('span', 'timeline-time', time(row.time)), rail, action); section.append(node);
    }); $('content').append(section);
  });
}
function renderErrors(data) {
  const all = [...data].reverse(), rows = all.filter(row => matches(JSON.stringify(row)));
  const errors = all.filter(row => ['error', 'critical'].includes(String(row.level).toLowerCase())).length;
  $('summary').textContent = t("最近 {0} 条诊断记录 · {1} 条错误", all.length, errors); resultCount(rows.length, all.length);
  if (!rows.length) { empty(all.length ? t("没有匹配的日志") : t("暂时没有诊断记录"), all.length ? t("试试问题摘要、模块名或关联 ID。") : t("本机 warning 和 error 记录会汇总在这里。"), all.length ? 'search' : 'check'); return; }
  const list = element('div', 'log-list');
  rows.forEach(row => {
    const rawTitle = row.msg || row.message || row.event || row.error || t("诊断记录");
    const title = rawTitle === 'logging.ready' ? t("诊断日志已就绪") : rawTitle;
    const value = row.ts || row.time || row.timestamp || row.occurred_at;
    const node = button('log-row', () => show(row, t("诊断详情"), `${day(value)} ${time(value)}`, node));
    const when = element('span', 'log-time'); when.append(element('span', '', day(value)), element('br'), document.createTextNode(time(value)));
    const copy = element('span', 'entry-copy'); copy.append(element('span', 'log-title', typeof title === 'string' ? title : JSON.stringify(title)));
    const sessionId = row.session_id || row.data?.session_id, taskId = row.task_id || row.data?.task_id;
    copy.append(element('span', 'metadata', [row.logger || row.module || row.process, sessionId ? t("会话 {0}", String(sessionId).slice(-8)) : '', taskId ? t("任务 {0}", String(taskId).slice(-8)) : ''].filter(Boolean).join(' · ') || t("本机日志")));
    const level = String(row.level || 'warning').toLowerCase();
    const tone = ['error', 'critical'].includes(level) ? 'error' : level === 'warning' ? 'warning' : '';
    node.append(when, copy, badge(level.toUpperCase(), tone)); list.append(node);
  }); $('content').append(list);
}
function render() {
  if (pageData === null) return;
  if (view === 'memory' || (view === 'context' && pageData.status === 'ready') || ['conversation','appearance','persona','skills','runs','controls','settings','interface'].includes(view)) {
    $('result-count').textContent = '';
    $('summary').textContent = view === 'memory' ? t("记忆关系与结构 · 选择节点探索关联") : view === 'context' ? t("{0} · 宿主记录的请求控制台", pageData.model || 'Qwen') : '';
    mountComplexPage($('content'), view, pageData, boxagent, $('search').value.trim(), show, currentState, async (sessionId) => { if(sessionId) selected=sessionId; await refresh(); });
    return;
  }
  disposeComplexPage();
  $('content').replaceChildren(); $('result-count').textContent = '';
  ({ context: renderContext, timeline: renderTimeline, errors: renderErrors })[view](pageData);
}
function loading() {
  disposeComplexPage();
  pageData = null; $('content').replaceChildren(); $('content').setAttribute('aria-busy', 'true');
  $('summary').textContent = t("正在读取…"); $('result-count').textContent = '';
  for (let i = 0; i < 3; i += 1) { const node = element('div', 'skeleton'); node.setAttribute('aria-hidden', 'true'); $('content').append(node); }
}
async function load({ restore = false } = {}) {
  const scroll = restore ? viewState.get(key())?.scroll || 0 : $('page').scrollTop;
  const mine = ++revision; updateHeader(); loading();
  try {
    let result;
    if (view === 'settings' || view === 'interface') result = {};
    else if (view === 'errors') result = await boxagent.errors();
    else if (view === 'memory') result = await boxagent.request('memory_snapshot', { node_limit: 100, edge_limit: 500 });
    else if (view === 'appearance') result = await boxagent.request('desktop_avatars');
    else if (view === 'persona') result = await boxagent.request('desktop_persona');
    else if (view === 'skills') result = await boxagent.request('list_skills');
    else if (view === 'controls') { try { result = await boxagent.request('desktop_snapshot'); } catch(error) { result = {unavailable:error.message}; } }
    else if (!selected) {
      $('content').replaceChildren(); $('summary').textContent = t("尚未选择产品会话");
      empty(t("从一次对话开始"), t("有会话后，可以在左侧选择并查看它的上下文与时间线。"), 'chat'); return;
    } else if (view === 'conversation') result = {session_id:selected, sessions, events:await boxagent.request('session_events',{session_id:selected,limit:240})};
    else if (view === 'runs') result = {session_id:selected,...await boxagent.request('desktop_runs',{session_id:selected})};
    else if (view === 'context') {
      const [snapshot, timeline] = await Promise.all([boxagent.request('context_snapshot', { session_id: selected }), boxagent.request('session_timeline', { session_id: selected, limit: 500 })]);
      result = { ...snapshot, timeline };
    } else result = await boxagent.request('session_timeline', { session_id: selected, limit: 150 });
    if (mine !== revision) return;
    pageData = result; render();
    $('page').scrollTop = scroll;
  } catch (error) {
    if (mine !== revision) return;
    $('content').replaceChildren(); $('summary').textContent = t("暂时无法读取");
    empty(['appearance','persona','runs'].includes(view) ? t("需要连接桌宠") : t("数据暂时不可用"), ['appearance','persona','runs'].includes(view) ? t("请启动桌宠后刷新；3D 形象和角色设置由桌面宿主负责。") : error.message, 'info', true);
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
    const copy = element('span', 'session-copy'); copy.append(element('span', 'session-title', row.title || t("未命名会话")), element('span', 'session-date', [day(row.updated_at), row.archived ? t("已归档") : ''].filter(Boolean).join(' · ')));
    node.append(icon('chat'), copy); $('sessions').append(node);
  });
  if (!sessions.length) $('sessions').append(element('p', 'sidebar-empty', t("还没有会话")));
  $('sessions').scrollTop = scroll;
}
async function refresh() {
  const mine = ++refreshRevision; ++revision; $('refresh').disabled = true;
  try {
    const [stateResult, sessionsResult] = await Promise.allSettled([boxagent.request('state'), boxagent.request('list_sessions', { include_archived: true })]);
    if (mine !== refreshRevision) return;
    if (stateResult.status === 'fulfilled') { updateState(stateResult.value); connection(true); }
    else { connection(false, stateResult.reason.message); $('runtime-status').textContent = t("等待 Engine 连接"); }
    if (sessionsResult.status === 'fulfilled') {
      sessions = sessionsResult.value;
      if (!sessions.some(row => row.session_id === selected)) selected = sessions.find(row => row.session_id === currentState.session_id)?.session_id || sessions[0]?.session_id || '';
      renderSessions();
    }
    if (sessionsResult.status === 'rejected' && !['errors', 'memory', 'appearance', 'persona', 'controls', 'settings', 'interface'].includes(view)) {
      updateHeader(); disposeComplexPage(); pageData = null; $('content').replaceChildren(); $('content').setAttribute('aria-busy', 'false');
      $('summary').textContent = t("等待连接到本机 Engine");
      empty(t("连接后，继续查看伙伴的记录"), t("先启动桌宠或 Engine。面板会自动重连，也可以点击刷新。"), 'context', true);
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
function navigate(next) {
  if (!pages[next] || view === next) return;
  saveView();
  if (!settingViews.has(view)) previousMainView = view;
  view = next; $('search').value = viewState.get(key())?.search || '';
  $('page').scrollTop = 0; load({ restore: true });
}
document.addEventListener('click', event => {
  const target = event.target.closest?.('[data-view]');
  if (target) navigate(target.dataset.view);
});
$('settings-back').onclick = () => navigate(previousMainView);
boxagent.onState(state => { connection(true); updateState(state); });
boxagent.onDiagnostic(message => {
  if (message === t("Engine 已连接")) { connection(true); refresh(); }
  else { connection(false, message); $('runtime-status').textContent = t("等待 Engine 连接"); }
});
refresh();

function localizeShell() {
  document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = t(node.dataset.i18n); });
  for (const attribute of ['aria-label', 'title', 'placeholder']) document.querySelectorAll(`[data-i18n-${attribute}]`).forEach(node => { node.setAttribute(attribute, t(node.getAttribute(`data-i18n-${attribute}`))); });
  document.title = t('伙伴 · 控制台');
}
document.addEventListener('panel-preferences', () => {
  localizeShell(); updateHeader(); updateState(currentState); connection($('connection').dataset.state === 'online'); renderSessions(); render();
});
startPreferences(); localizeShell();
