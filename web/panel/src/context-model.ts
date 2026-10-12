import { t } from './i18n';
import type { Composition, ContextSnapshot, Entry, RequestSnapshot, TimelineEvent } from './types';
export const labels: Record<string, string> = { instructions: '系统指令', persona: '角色设定', tools: '工具定义', profile: '用户画像', checkpoint: '检查点', history: '恢复历史', environment: '环境', memory: '记忆', reference: '本轮参考', user: '用户消息', assistant: '助手回复', tool_call: '工具调用', tool_result: '工具结果', notification: '任务通知', system_other: '其他系统', unknown: '未恢复条目' };
export const colors: Record<string, string> = { instructions: '#6875e9', persona: '#9a9bef', tools: '#eda83f', profile: '#c29758', checkpoint: '#8797a4', history: '#8094a8', environment: '#b997d8', memory: '#9673ce', reference: '#ab70e1', user: '#53b989', assistant: '#5189da', tool_call: '#7d9ad0', tool_result: '#42aaa7', notification: '#cfb270', system_other: '#aab2bb', unknown: '#c6cbd2' };
export interface Frame { key: string; title: string; request?: RequestSnapshot; entries: Entry[]; missing: number; composition: Composition[]; tokens: number; added: number[]; removed: number[]; delta: number; time?: number; }
export function composition(entries: Entry[]): Composition[] {
  const groups = new Map<string, Composition>();
  entries.forEach(entry => {
    const group = groups.get(entry.category) || { key: entry.category, label: labels[entry.category] || entry.label, tokens: 0, count: 0, percent: 0 };
    group.tokens += entry.tokens; group.count++; groups.set(entry.category, group);
  });
  const total = entries.reduce((sum, entry) => sum + entry.tokens, 0);
  return [...groups.values()].map(row => ({ ...row, percent: total ? row.tokens * 100 / total : 0 }));
}
export function buildFrames(state: ContextSnapshot, timeline: TimelineEvent[]): Frame[] {
  const bySeq = new Map<number, Entry>();
  // The timeline contains deleted/expired entries absent from the current view.
  // Only decode explicitly recorded context items; formal messages are separate.
  timeline.filter(row => row.type === 'context/item').forEach(row => {
    const data = row.data, category = String(data.category || 'system_other');
    bySeq.set(row.seq, { seq: row.seq, category, label: labels[category] || category,
      tokens: Number(data.tokens || 0), preview: String(data.content || '').replace(/\s+/g, ' ').slice(0, 200), role: String(data.role || ''), time: row.time });
  });
  [...state.provider_entries, ...state.entries].forEach(entry => bySeq.set(entry.seq, entry));
  const frames: Frame[] = [];
  for (const [index, request] of state.requests.entries()) {
    const entries = request.entries.map(seq => bySeq.get(seq) || { seq, category: 'unknown', label: labels.unknown, tokens: 0, preview: t('该条目不在已载入历史中，无法恢复内容与 token 数。') });
    const tokens = entries.reduce((sum, row) => sum + row.tokens, 0);
    frames.push({ key: String(request.seq), title: t('请求 {0}', index + 1), request, entries, missing: entries.filter(entry => entry.category === 'unknown').length, composition: composition(entries), tokens, added: request.added, removed: request.removed, delta: tokens - (frames.at(-1)?.tokens || 0), time: request.time });
  }
  const previous = frames.at(-1)?.entries || [], current = new Set(state.entries.map(entry => entry.seq)), last = new Set(previous.map(entry => entry.seq));
  frames.push({ key: 'live', title: t('当前有效上下文'), entries: state.entries, missing: 0, composition: composition(state.entries), tokens: state.totals.tokens, added: state.entries.filter(entry => !last.has(entry.seq)).map(entry => entry.seq), removed: previous.filter(entry => !current.has(entry.seq)).map(entry => entry.seq), delta: state.totals.tokens - (frames.at(-1)?.tokens || 0) });
  return frames;
}
export function categoryDelta(frame: Frame, previous: Frame | undefined, category: string): { items: number; tokens: number } {
  const now = frame.composition.find(row => row.key === category), before = previous?.composition.find(row => row.key === category);
  return { items: (now?.count || 0) - (before?.count || 0), tokens: (now?.tokens || 0) - (before?.tokens || 0) };
}
