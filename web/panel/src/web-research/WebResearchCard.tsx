import { useState } from 'react';
import { BookOpen, ChevronDown, ExternalLink, LoaderCircle } from 'lucide-react';
import { t } from '../i18n';
import { locale } from '../preferences';
import type { Bridge, WebResearchView } from '../types';
import './web-research.css';

function dateLabel(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(locale());
}

export function WebResearchStatus({ view }: { view: WebResearchView | null | undefined }) {
  if (!view || view.stage === 'ready') return null;
  const working = ['searching', 'reading'].includes(view.stage);
  return <p className="web-research-status" role="status" aria-live="polite">
    {working && <LoaderCircle size={14} aria-hidden="true" />}
    <span>{t(working ? view.stage === 'searching' ? '正在查资料' : '正在读攻略原文' : view.message)}</span>
  </p>;
}

export function WebResearchCard({ view, bridge }: { view: WebResearchView; bridge: Bridge }) {
  const [error, setError] = useState('');
  if (!view.sources.length) return null;
  const read = view.sources.filter(source => source.read_status === 'read').length;
  async function open(url: string) {
    setError('');
    try { await bridge.request('desktop_open_web_source', { url }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : t('暂时无法打开网页')); }
  }
  return <details className="web-reference-card">
    <summary>
      <BookOpen size={14} aria-hidden="true" />
      <span>{t('参考资料')}</span>
      <span className="web-reference-summary">{read ? t('{0} 篇已读', read) : t('仅找到摘要')}</span>
      <ChevronDown className="web-reference-chevron" size={14} aria-hidden="true" />
    </summary>
    <div className="web-reference-body">
      <p className="web-reference-version">
        {view.requested_version && <span>{t('提问版本：{0}', view.requested_version)}</span>}
        <span>{t('攻略适用版本待核对')}</span>
      </p>
      <ul className="web-reference-list">
        {view.sources.map(source => <li key={source.url}>
          <button className="web-reference-link" onClick={() => void open(source.url)}
            title={source.title} aria-label={t('打开原文：{0}', source.title)}>
            <span className="web-reference-title">{source.title || new URL(source.url).hostname}</span>
            <span className="web-reference-meta">
              <span>{new URL(source.url).hostname.replace(/^www\./, '')}</span>
              <span>{t(source.read_status === 'read' ? '已读正文' : source.read_status === 'unavailable' ? '未能读正文' : source.read_status === 'reading' ? '正在读取' : '搜索摘要')}</span>
            </span>
            {dateLabel(source.published_at) && <span className="web-reference-date">{t('文章日期：{0}', dateLabel(source.published_at))}</span>}
            <ExternalLink className="web-reference-open" size={14} aria-hidden="true" />
          </button>
        </li>)}
      </ul>
      {dateLabel(view.retrieved_at) && <p className="web-reference-retrieved">{t('检索于 {0}', dateLabel(view.retrieved_at))}</p>}
      {error && <p className="web-reference-error" role="alert">{error}</p>}
    </div>
  </details>;
}
