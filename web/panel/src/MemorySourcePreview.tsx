import { useEffect, useRef, useState } from 'react';
import type { Bridge } from './types';
import { t } from './i18n';
import { locale } from './preferences';

export interface MemorySource {
  image: string;
  caption: string;
  user_request: string;
  captured_at: number;
}

export function deletedMemoryIds(value: unknown, selected: string): Set<string> {
  const ids = value && typeof value === 'object' ? (value as Record<string, unknown>).deleted_ids : null;
  return new Set([selected, ...(Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string' && id.length <= 200) : [])]);
}

export function parseMemorySource(value: unknown): MemorySource | null {
  if (!value || typeof value !== 'object') return null;
  const source = value as Record<string, unknown>;
  if (source.status !== 'succeeded' || typeof source.image !== 'string'
      || !/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(source.image)
      || source.image.length > 6 * 1024 * 1024
      || typeof source.caption !== 'string' || typeof source.user_request !== 'string'
      || typeof source.captured_at !== 'number' || !Number.isFinite(source.captured_at)) return null;
  return { image: source.image, caption: source.caption, user_request: source.user_request, captured_at: source.captured_at };
}

export function MemorySourcePreview({ memoryId, bridge }: { memoryId: string; bridge: Bridge }) {
  const [source, setSource] = useState<MemorySource | null>(null);
  const [status, setStatus] = useState('');
  const loading = useRef(false), alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  async function load() {
    if (source || loading.current) return;
    loading.current = true; setStatus(t('正在读取来源画面…'));
    try {
      const result = parseMemorySource(await bridge.request('memory_evidence', { memory_id: memoryId }));
      if (alive.current) { setSource(result); setStatus(result ? '' : t('来源画面不可用或已删除。')); }
    } catch { if (alive.current) setStatus(t('暂时无法读取来源画面。')); }
    finally { loading.current = false; }
  }
  return <details className="memory-source-preview" onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <summary>{t('查看当时画面')}</summary>
    {status && <p role="status">{status}</p>}
    {source && <div>
      <img src={source.image} alt={t('用户明确保存的来源画面')} />
      <p className="metadata">{new Date(source.captured_at * 1000).toLocaleString(locale())}</p>
      <p><strong>{t('当时的请求')}</strong>{source.user_request}</p>
      <p><strong>{t('画面观察')}</strong>{source.caption}</p>
      <p className="metadata">{t('画面观察仅代表当时的可见内容。')}</p>
    </div>}
  </details>;
}
