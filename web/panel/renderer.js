let selected = '', view = 'context', revision = 0;
const $ = id => document.getElementById(id);
const colors = ['#3d7757','#7d9e64','#cab57a','#73a6a7','#be94a2','#99a7c1'];
function card(meta, text, action) {
  const element = document.createElement('div'); element.className = 'card';
  const label = document.createElement('div'); label.className = 'meta'; label.textContent = meta;
  const body = document.createElement('div'); body.textContent = text;
  element.append(label, body); if (action) { element.tabIndex = 0; element.onclick = action; }
  $('content').append(element);
}
function show(value) { $('full').textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); $('detail').showModal(); }
async function load() {
  const mine = ++revision; $('content').replaceChildren();
  try {
    if (view === 'errors') { const rows = await boxagent.errors(); if (mine !== revision) return;
      $('summary').textContent = `最近 ${rows.length} 条 warning / error`; rows.reverse().forEach(row => card(row.level || row.event || '', JSON.stringify(row, null, 2))); return; }
    if (view === 'memory') { const graph = await boxagent.request('memory_snapshot', { node_limit: 100, edge_limit: 100 }); if (mine !== revision) return;
      $('summary').textContent = '长期记忆与当前 Query 的召回包分别管理。'; (graph.nodes || []).forEach(row => card(`${row.type || ''} · ${row.id}`, row.content || JSON.stringify(row), () => show(row))); return; }
    if (!selected) return;
    if (view === 'timeline') { const rows = await boxagent.request('session_timeline', { session_id: selected, limit: 150 }); if (mine !== revision) return;
      $('summary').textContent = `最近 ${rows.length} 条会话事件 · 对话、参考和任务`; rows.forEach(row => { const data = row.data?.product_event || row.data || row; card(`#${row.seq || row.sequence} · ${row.type} · ${data.runtime || ''}`, data.content || data.status || data.reference || data.query_id || '', () => show(row)); }); return; }
    const state = await boxagent.request('context_snapshot', { session_id: selected }); if (mine !== revision) return;
    $('summary').textContent = state.status === 'ready' ? `${state.totals.entries} 项 · 约 ${state.totals.tokens} tokens · ${state.generation} 次连接 · 删除回执 ${state.delete_ack_count || 0} · ${state.requests?.length || 0} 个请求快照` : state.message;
    if (state.status !== 'ready') return;
    card('观测边界', '下方为宿主记录的有效输入；tokens 为估计，服务端截断与 KV 缓存命中未知。');
    if ((state.provider_entries || []).length !== state.entries.length) card('同步未确认', '本地已到期的条目仍可能在 provider 上；等待删除回执或重建连接。', () => show(state.provider_entries));
    const bar = document.createElement('div'); bar.className = 'bar';
    state.composition.filter(row => row.tokens).forEach((row, i) => { const segment = document.createElement('span'); segment.style.width = row.percent + '%'; segment.style.background = colors[i % colors.length]; segment.title = `${row.label} ${row.percent}%`; bar.append(segment); }); $('content').append(bar);
    state.entries.forEach(row => card(`#${row.seq} · ${row.label} · 约 ${row.tokens} tokens`, row.preview, async () => { const detail = await boxagent.request('context_entry', { session_id: selected, seq: row.seq }); show(detail.content); }));
    (state.requests || []).slice(-10).forEach(row => card(`请求 #${row.seq}`, `有效条目 ${row.entries.join(', ')}\n新增 ${row.added.join(', ')}；移除 ${row.removed.join(', ')}`, () => show(row)));
  } catch (error) { $('summary').textContent = error.message; }
}
async function refresh() {
  try { const state = await boxagent.request('state'); $('status').textContent = `语音 ${state.voice} · 任务 ${state.task}`;
    const sessions = await boxagent.request('list_sessions', { include_archived: true }); $('sessions').replaceChildren();
    selected ||= sessions[0]?.session_id || ''; sessions.forEach(row => { const button = document.createElement('button'); button.textContent = row.title + (row.archived ? ' · 已归档' : ''); button.className = row.session_id === selected ? 'selected' : ''; button.onclick = () => { selected = row.session_id; refresh(); }; $('sessions').append(button); }); await load();
  } catch (error) { $('status').textContent = error.message; }
}
$('refresh').onclick = refresh; $('close').onclick = () => $('detail').close();
document.querySelectorAll('[data-view]').forEach(button => button.onclick = () => { view = button.dataset.view; load(); });
boxagent.onState(state => { $('status').textContent = `语音 ${state.voice} · 任务 ${state.task}`; });
boxagent.onDiagnostic(message => { $('status').textContent = message; if (message === 'Engine 已连接') refresh(); });
refresh();
