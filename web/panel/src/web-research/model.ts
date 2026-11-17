import type { ProductMessage, RuntimeState, WebReference, WebResearchView } from '../types';

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

export function sourceUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 8192 || /[\u0000-\u0020]/.test(value)) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        !host.includes('.') || /^[\d.]+$/.test(host) || host.includes(':') ||
        /\.(local|localhost|internal)$/.test(host)) return null;
    url.hash = '';
    return url.href;
  } catch { return null; }
}

export function researchView(value: unknown): WebResearchView | null {
  const row = record(value);
  if (!row || typeof row.session_id !== 'string' || typeof row.interaction_id !== 'string' || !row.interaction_id ||
      typeof row.stage !== 'string' || !['searching', 'reading', 'ready', 'empty', 'failed', 'cancelled'].includes(row.stage) ||
      typeof row.query !== 'string' || typeof row.requested_version !== 'string' ||
      typeof row.message !== 'string' || typeof row.retrieved_at !== 'string' || !Array.isArray(row.sources)) return null;
  const sources: WebReference[] = [];
  for (const value of row.sources.slice(0, 8)) {
    const source = record(value);
    const url = sourceUrl(source?.url);
    if (!source || !url || typeof source.title !== 'string' || typeof source.read_status !== 'string' ||
        !['not_read', 'reading', 'read', 'unavailable'].includes(source.read_status)) continue;
    sources.push({ title: source.title, url,
      published_at: typeof source.published_at === 'string' ? source.published_at : null,
      site_name: typeof source.site_name === 'string' ? source.site_name : null,
      read_status: source.read_status as WebReference['read_status'] });
  }
  return { session_id: row.session_id, interaction_id: row.interaction_id,
    stage: row.stage as WebResearchView['stage'], query: row.query,
    requested_version: row.requested_version, message: row.message,
    retrieved_at: row.retrieved_at, sources };
}

export function researchByInteraction(history: ProductMessage[], state: RuntimeState = {}): Record<string, WebResearchView> {
  const result: Record<string, WebResearchView> = {};
  function merge(view: WebResearchView) {
    const previous = result[view.interaction_id];
    const sources = new Map((previous?.sources || []).map(source => [source.url, source]));
    for (const source of view.sources) {
      const old = sources.get(source.url);
      sources.set(source.url, { ...source, published_at: source.published_at || old?.published_at || null });
    }
    result[view.interaction_id] = { ...view,
      requested_version: view.requested_version || previous?.requested_version || '',
      sources: [...sources.values()].slice(-8) };
  }
  for (const row of history) {
    if (row.type !== 'web.research.completed') continue;
    const view = researchView(row.data?.web_research);
    if (view && view.interaction_id === row.interaction_id && (!row.session_id || view.session_id === row.session_id)) merge(view);
  }
  const live = researchView(state.web_research);
  if (live && live.session_id === state.session_id) merge(live);
  return result;
}

export function lastAssistantIds(history: ProductMessage[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const row of history) {
    if (row.type === 'message.final' && row.role === 'assistant' && row.interaction_id && row.content?.trim())
      result[row.interaction_id] = row.event_id || String(row.sequence);
  }
  return result;
}
