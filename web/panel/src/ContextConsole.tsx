import { t } from './i18n';
import { locale } from './preferences';
import { useMemo, useState } from 'react';
import { buildFrames, categoryDelta, colors, labels } from './context-model';
import type { Bridge, Composition, ContextSnapshot, Entry, OpenDetail, TimelineEvent } from './types';
const fmt = (value: number) => value.toLocaleString(locale());
const signed = (value: number) => `${value > 0 ? '+' : ''}${fmt(value)}`;
function Bar({ rows }: { rows: Composition[] }) {
  return <div className="console-bar">{rows.filter(row => row.tokens).map(row => <span key={row.key} style={{ width: `${row.percent}%`, background: colors[row.key] || '#aab2bb' }} title={`${t(row.label)} ≈${fmt(row.tokens)} tokens · ${row.percent.toFixed(1)}%`} />)}</div>;
}
function Legend({ rows }: { rows: Composition[] }) {
  return <div className="console-legend">{rows.map(row => <span key={row.key}><i style={{ background: colors[row.key] || '#aab2bb' }} />{t(row.label)}<b>≈{fmt(row.tokens)}</b><small>{row.percent.toFixed(1)}%</small></span>)}</div>;
}
function BrowserEntry({ entry, sessionId, bridge }: { entry: Entry; sessionId: string; bridge: Bridge }) {
  const [open, setOpen] = useState(false), [content, setContent] = useState<string | null>(null), [error, setError] = useState('');
  async function fetchContent() {
    setError('');
    try {
      const result = await bridge.request('context_entry', { session_id: sessionId, seq: entry.seq }) as { status: string; content: string };
      if (result.status !== 'ready') throw new Error(t("该条目无法读取"));
      setContent(result.content);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }
  return <details className="browser-entry" open={open} onToggle={event => {
    const next = event.currentTarget.open; setOpen(next);
    if (next && content === null && !error && entry.category !== 'unknown') void fetchContent();
  }}>
    <summary><span className="entry-index">#{entry.seq}</span><span className="browser-preview">{entry.preview || entry.label}</span><span className="metadata">{entry.category === 'unknown' ? t("未恢复") : `≈${fmt(entry.tokens)}`}</span></summary>
    {open && <div className="browser-entry-body">{error ? <><p role="alert">{error}</p><button className="button" onClick={() => void fetchContent()}>{t("重试")}</button></> : <pre>{content ?? (entry.category === 'unknown' ? entry.preview : t("正在读取完整内容…"))}</pre>}</div>}
  </details>;
}
export function ContextConsole({ state, timeline, bridge, query, show }: { state: ContextSnapshot; timeline: TimelineEvent[]; bridge: Bridge; query: string; show: OpenDetail }) {
  const language = locale();
  const frames = useMemo(() => buildFrames(state, timeline), [state, timeline, language]);
  const [picked, setPicked] = useState('live'), [hovered, setHovered] = useState<string | null>(null), [mode, setMode] = useState<'total' | 'delta'>('total');
  const [category, setCategory] = useState('');
  const frame = frames.find(row => row.key === (hovered || picked)) || frames.at(-1)!;
  const index = frames.indexOf(frame), previous = frames[index - 1];
  const requests = frames.filter(row => row.request), max = Math.max(1, ...requests.map(row => mode === 'total' ? row.tokens : Math.abs(row.delta)));
  const missing = requests.some(row => row.missing);
  const pending = state.provider_entries.filter(row => !state.entries.some(entry => entry.seq === row.seq));
  const visibleEntries = frame.entries.filter(entry => (!category || entry.category === category) && (!query || `${entry.preview} ${entry.label} ${entry.seq}`.toLowerCase().includes(query.toLowerCase())));
  const groups = frame.composition.filter(row => (!category || row.key === category) && visibleEntries.some(entry => entry.category === row.key));
  const totalTools = state.turns.reduce((sum, row) => sum + row.tool_calls.length, 0);
  const timing = state.turns.map(row => row.response_ms).filter((value): value is number => value !== null && value >= 0).sort((a, b) => a - b);
  const median = timing.length ? timing.length % 2 ? timing[Math.floor(timing.length / 2)] : Math.round((timing[timing.length / 2 - 1] + timing[timing.length / 2]) / 2) : null;
  return <div className="context-console">
    <div className="console-stats">
      {[[t("当前估算输入"), `≈${fmt(state.totals.tokens)}`, 'tokens'], [t("已记录请求"), fmt(state.requests.length), t("{0} 个模型回合", state.turns.length)], [t("工具调用"), fmt(totalTools), t("当前连接内记录")], [t("过期 / 删除回执"), `${state.expired_count} / ${state.delete_ack_count}`, pending.length ? t("{0} 项尚未确认同步", pending.length) : t("本地记录")], [t("开始生成 p50"), median === null ? '—' : `${fmt(median)} ms`, t("{0} 个已记录样本", timing.length)]].map(([title, value, caption]) => <div className="console-stat" key={title}><span>{title}</span><strong>{value}</strong><small>{caption}</small></div>)}
    </div>
    <div className="console-top-grid">
      <section className="console-card"><header><h2>{t("当前上下文构成")}</h2><span className="metadata">{t('第 {0} 次连接', state.generation)}</span></header>
        <div className="console-current"><strong>≈{fmt(state.totals.tokens)}</strong><span>tokens · {t('{0} 个条目', state.entries.length)}</span></div><Bar rows={state.composition}/><Legend rows={state.composition.filter(row => row.count > 0)}/>
      </section>
      <section className="console-card boundary-card"><header><h2>{t("观测状态")}</h2><span className="console-state">{t("宿主记录")}</span></header>
        <dl><div><dt>{t("对话模型")}</dt><dd>{state.model || state.runtime || t("未记录")}</dd></div><div><dt>{t("实际计费 tokens")}</dt><dd>{t("未提供")}</dd></div><div><dt>{t("KV 缓存命中")}</dt><dd>{t("未提供")}</dd></div><div><dt>{t("上下文窗口使用率")}</dt><dd>{t("未配置容量")}</dd></div></dl>
        <p>{t("以下构成与趋势来自本地账本，均为估算。")}{' '}{missing ? t("部分历史条目未恢复，相关请求显示已知下限。") : t("历史请求仅覆盖当前连接。")}</p>
      </section>
    </div>
    <div className="console-main-grid">
      <div className="console-left">
        <section className="console-card trend-card"><header><h2>{t("请求上下文趋势")}</h2><div className="segmented" aria-label={t("趋势模式")}><button aria-pressed={mode === 'total'} onClick={() => setMode('total')}>{t("总量")}</button><button aria-pressed={mode === 'delta'} onClick={() => setMode('delta')}>{t("增量")}</button></div></header>
          <p className="metadata">{t("悬停预览，点击固定请求；右侧同步展示当次输入。")}</p>
          {requests.length ? <div className="trend-chart"><div className="trend-axis"><span>{mode === 'delta' ? '±' : '≈'}{fmt(max)}</span><span>0</span></div><div className={`trend-scroll ${mode}`}><div className="trend-columns">{requests.map(row => <button key={row.key} className={`trend-column ${frame.key === row.key ? 'active' : ''}`} aria-pressed={picked === row.key} aria-label={`${row.title}，${row.missing ? t("已知下限") : t("约")} ${row.tokens} tokens`} onMouseEnter={() => setHovered(row.key)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(row.key)} onBlur={() => setHovered(null)} onClick={() => { setPicked(row.key); setHovered(null); }} title={t("{0} · {1} {2} tokens\n新增 {3} 项 · 移除 {4} 项{5}", row.title, row.missing ? t('已知下限') : t('估算'), fmt(row.tokens), row.added.length, row.removed.length, row.missing || frames[frames.indexOf(row)-1]?.missing ? '\n'+t('历史条目缺失，无法确定 token 增量') : '')}>
            <span className="trend-plot">{mode === 'total' ? <span className="trend-stack" style={{ height: `${Math.max(2, row.tokens / max * 100)}%` }}>{row.composition.filter(part => part.tokens).map(part => <i key={part.key} style={{ height: `${part.percent}%`, background: colors[part.key] || '#aab2bb' }}/>)}</span> : row.missing || frames[frames.indexOf(row)-1]?.missing ? <span className="metadata">?</span> : <span className={`trend-delta ${row.delta < 0 ? 'negative' : ''}`} style={{ height: `${Math.max(2, Math.abs(row.delta) / max * 47)}%` }}/>}</span>
            <span className="trend-step">{requests.indexOf(row) + 1}{row.removed.length > 0 ? ' ✂' : ''}{row.missing ? ' *' : ''}</span>
          </button>)}</div></div></div> : <p className="console-no-data">{t("尚未记录模型请求。")}</p>}
          <div className="step-brief"><div><strong>{frame.title}</strong><span>{frame.time ? new Date(frame.time * 1000).toLocaleTimeString(locale(), { hour12: false }) : t("本地当前记录")}</span></div><div className="step-changes"><span className="delta-positive">{t('新增 {0} 项', frame.added.length)}</span><span className="delta-negative">{t('移除 {0} 项', frame.removed.length)}</span><span>{frame.missing ? '≥' : '≈'}{fmt(frame.tokens)} tokens</span></div>
            {frame.request && <button className="text-button" onClick={event => show(frame.request, frame.title, t("有效输入序号与增删记录"), event.currentTarget)}>{t("查看请求原始记录 →")}</button>}
          </div>
        </section>
        <section className="console-card"><header><h2>{t("本轮参考生命周期")}</h2><span className="metadata">{t("当前连接")}</span></header><div className="lifecycle-stats"><div><strong>{state.entries.filter(row => ['reference', 'memory', 'environment'].includes(row.category)).length}</strong><span>{t("有效参考条目")}</span></div><div><strong>{state.expired_count}</strong><span>{t("已到期")}</span></div><div><strong>{state.delete_ack_count}</strong><span>{t("删除回执")}</span></div></div>
          {pending.length > 0 ? <p className="console-warning">{t('{0} 项本地已到期，服务端删除尚未确认。', pending.length)}</p> : <p className="metadata">{t("本轮参考和正式历史分别管理。请求增量标记可定位条目移除。")}</p>}
        </section>
      </div>
      <section className="console-card context-browser"><header><h2>{t("上下文浏览器")}</h2><select aria-label={t("选择上下文请求")} value={picked} onChange={event => { setPicked(event.target.value); setHovered(null); }}>{frames.map(row => <option value={row.key} key={row.key}>{row.title}{row.request ? ` · #${row.request.seq}` : ''}</option>)}</select></header>
        <div className="browser-frame-title"><strong>{frame.title}{hovered ? ' · '+t('预览') : ''}</strong><span>{frame.missing ? '≥' : '≈'}{fmt(frame.tokens)} tokens</span></div><Bar rows={frame.composition}/>
        {frame.missing > 0 && <p className="console-warning">{t('{0} 个条目不在最近 500 条事件中，构成为已知下限。', frame.missing)}</p>}
        <div className="browser-filters"><button className={!category ? 'active' : ''} onClick={() => setCategory('')}>{t("全部")}</button>{frame.composition.map(row => <button key={row.key} className={category === row.key ? 'active' : ''} onClick={() => setCategory(category === row.key ? '' : row.key)}>{t(row.label)}</button>)}</div>
        {!groups.length && <p className="console-no-data">{t("没有匹配的上下文条目。")}</p>}
        {groups.map(row => {
          const delta = categoryDelta(frame, previous, row.key), entries = visibleEntries.filter(entry => entry.category === row.key);
          return <details className="browser-category" key={`${frame.key}:${row.key}`}><summary><i style={{ background: colors[row.key] || '#aab2bb' }}/><strong>{t(labels[row.key] || row.label)}</strong><span>{t('{0} 项', entries.length)}</span>{delta.items !== 0 && row.key !== 'unknown' && <b className={delta.items > 0 ? 'delta-positive' : 'delta-negative'}>{signed(delta.items)}</b>}<span className="category-tokens">{row.key === 'unknown' ? t("未恢复") : `≈${fmt(row.tokens)}`}</span></summary><div className="browser-category-caption">{frame.missing || previous?.missing ? t("历史条目未完全恢复，无法确定分类 token 增量。") : t("相比上一请求 {0} tokens", signed(delta.tokens))}</div>{entries.map(entry => <BrowserEntry key={entry.seq} entry={entry} sessionId={state.session_id} bridge={bridge}/>)}</details>;
        })}
      </section>
    </div>
  </div>;
}
