import { t } from '../i18n';
import { normalizeEvent, type EventRecord } from '../event-model';
import { day, time, matches, EmptyState } from '../components/ui';
import type { OpenDetail, RecordData } from '../types';

export function Timeline({ data, query, show }: { data: EventRecord[]; query: string; show: OpenDetail }) {
  const rows = data
    .map(normalizeEvent)
    .filter((row) => matches(query, row.label, row.type, row.preview, row.query, row.runtime, row.seq));
  if (!rows.length)
    return (
      <EmptyState
        title={t(data.length ? '没有匹配的事件' : '时间线还很安静')}
        message={t(data.length ? '试试消息内容、事件类型或回合 ID。' : '发生对话或任务后，记录会出现在这里。')}
        icon={data.length ? 'search' : 'timeline'}
      />
    );
  const days = new Map<string, typeof rows>();
  rows.forEach((row) => {
    const label = day(row.time);
    days.set(label, [...(days.get(label) || []), row]);
  });
  return (
    <>
      {[...days].map(([label, items]) => (
        <section className="timeline-day" key={label}>
          <h2 className="day-title">{label}</h2>
          {items.map((row) => (
            <div className="timeline-row" key={row.seq}>
              <span className="timeline-time">{time(row.time)}</span>
              <div className="timeline-rail">
                <span className="timeline-dot" />
              </div>
              <button
                className="timeline-event"
                onClick={(event) =>
                  show(
                    row.original,
                    row.label,
                    t('{0} {1} · 事件 #{2}', day(row.time), time(row.time), row.seq),
                    event.currentTarget,
                  )
                }
              >
                <span className="entry-heading">
                  {row.label}
                  {(row.type.startsWith('context/') ||
                    row.type.startsWith('task/') ||
                    row.type.startsWith('tool/')) && (
                    <span className="badge">{t(row.type.startsWith('context/') ? '上下文' : '任务')}</span>
                  )}
                </span>
                {row.preview && <span className="entry-preview">{row.preview}</span>}
                <span className="timeline-meta">
                  <span>#{row.seq}</span>
                  {row.runtime && <span>{row.runtime}</span>}
                  {row.query && <span>{t('回合 {0}', String(row.query).slice(-8))}</span>}
                </span>
              </button>
            </div>
          ))}
        </section>
      ))}
    </>
  );
}
export function DiagnosticLogs({ data, query, show }: { data: RecordData[]; query: string; show: OpenDetail }) {
  const rows = [...data].reverse().filter((row) => matches(query, JSON.stringify(row)));
  if (!rows.length)
    return (
      <EmptyState
        title={t(data.length ? '没有匹配的日志' : '暂时没有诊断记录')}
        message={t(data.length ? '试试问题摘要、模块名或关联 ID。' : '本机 warning 和 error 记录会汇总在这里。')}
        icon={data.length ? 'search' : 'check'}
      />
    );
  return (
    <div className="log-list">
      {rows.map((row, index) => {
        const rawTitle = row.msg || row.message || row.event || row.error || t('诊断记录'),
          title = rawTitle === 'logging.ready' ? t('诊断日志已就绪') : rawTitle;
        const at = row.ts || row.time || row.timestamp || row.occurred_at,
          level = String(row.level || 'warning').toLowerCase();
        const nested = (row.data || {}) as RecordData,
          sessionId = row.session_id || nested.session_id,
          taskId = row.task_id || nested.task_id;
        const meta =
          [
            row.logger || row.module || row.process,
            sessionId ? t('会话 {0}', String(sessionId).slice(-8)) : '',
            taskId ? t('任务 {0}', String(taskId).slice(-8)) : '',
          ]
            .filter(Boolean)
            .join(' · ') || t('本机日志');
        return (
          <button
            className="log-row"
            key={index}
            onClick={(event) => show(row, t('诊断详情'), `${day(at)} ${time(at)}`, event.currentTarget)}
          >
            <span className="log-time">
              <span>{day(at)}</span>
              <br />
              {time(at)}
            </span>
            <span className="entry-copy">
              <span className="log-title">{typeof title === 'string' ? title : JSON.stringify(title)}</span>
              <span className="metadata">{meta}</span>
            </span>
            <span
              className={`badge ${['error', 'critical'].includes(level) ? 'error' : level === 'warning' ? 'warning' : ''}`}
            >
              {level.toUpperCase()}
            </span>
          </button>
        );
      })}
    </div>
  );
}
