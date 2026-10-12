import { t } from '../i18n';
import { locale } from '../preferences';
import { useEffect, useState } from 'react';
import { run, runtimeLabel, type PageProps } from '../hooks/useAction';
import type { Session, ProductMessage, SessionMessages } from '../types';
import { Icon } from '../components/ui';
export function Conversation({
  data,
  bridge,
  runtime,
  busy,
  act,
  refresh,
  query,
  setStatus,
}: PageProps<SessionMessages>) {
  const [text, setText] = useState(''),
    [title, setTitle] = useState(''),
    [creating, setCreating] = useState(false),
    [events, setEvents] = useState(data.events);
  useEffect(() => {
    let alive = true;
    const timer = setInterval(() => {
      void bridge
        .request<ProductMessage[]>('session_events', { session_id: data.session_id, limit: 240 })
        .then((value) => {
          if (alive) setEvents(value);
        })
        .catch(() => {});
    }, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [data.session_id]);
  const current = runtime.session_id === data.session_id;
  const rows = events
    .filter((row) => row.type === 'message.final')
    .filter(
      (row) =>
        !query ||
        String(row.content || '')
          .toLowerCase()
          .includes(query.toLowerCase()),
    );
  async function create() {
    const item = await act<Session>('create_session', { title: title.trim() || t('新会话') });
    await refresh(item.session_id);
  }
  async function activate() {
    await act('activate_session', { session_id: data.session_id });
    await refresh(data.session_id);
  }
  async function submit() {
    if (!current || !text.trim()) return;
    const result = await act<{ status: string; message?: string }>('submit_text', { goal: text.trim() });
    if (['accepted', 'succeeded', 'completed'].includes(result?.status)) {
      setText('');
    } else setStatus(result?.message || t('请求已提交'));
  }
  return (
    <>
      <div className="conversation-toolbar">
        <div className="conversation-toolbar-session">
          <span className={`conversation-status${current ? ' is-current' : ''}`}>
            <span className="status-dot" aria-hidden="true" />
            {current ? t('当前对话') : t('正在查看历史会话')}
          </span>
          {!current && (
            <button className="button primary" disabled={busy} onClick={() => run(activate())}>
              <Icon name="arrow" />
              {t('设为当前对话')}
            </button>
          )}
        </div>
        <div className="conversation-toolbar-actions">
          <button className={`button${current ? ' primary' : ''}`} aria-expanded={creating} aria-controls="conversation-create" onClick={() => setCreating(!creating)}>
            <Icon name="plus" />
            {t('新建会话')}
          </button>
          <button className="button conversation-tool" aria-pressed={runtime.voice !== 'off'} disabled={busy || !current} onClick={() => run(act('toggle_voice'))}>
            <Icon name={runtime.voice === 'off' ? 'mic' : 'mic-off'} />
            {runtime.voice === 'off' ? t('开启麦克风') : t('关闭麦克风')}
          </button>
          <button className="button conversation-tool" title={t('显示 / 收起桌面小窗')} aria-label={t('显示 / 收起桌面小窗')} disabled={busy || !current} onClick={() => run(act('desktop_toggle_bubble'))}>
            <Icon name="window" />
            {t('桌面对话小窗')}
          </button>
        </div>
      </div>
      {creating && (
        <form
          id="conversation-create"
          className="feature-inline"
          onSubmit={(e) => {
            e.preventDefault();
            run(create());
          }}
        >
          <input
            aria-label={t('新会话名称')}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t('新会话名称')}
          />
          <button className="button primary" disabled={busy}>
            {t('创建并开始')}
          </button>
        </form>
      )}
      <section className="chat-history">
        {rows.length ? (
          rows.map((row) => (
            <article key={row.event_id || row.sequence} className={`chat-message ${row.role}`}>
              <div>
                <strong>{row.role === 'user' ? t('你') : row.role === 'assistant' ? t('伙伴') : row.role}</strong>
                <time>{row.occurred_at ? new Date(row.occurred_at * 1000).toLocaleString(locale()) : ''}</time>
              </div>
              <p>{row.content}</p>
            </article>
          ))
        ) : (
          <p className="metadata">{t('这个会话还没有正式消息。')}</p>
        )}
      </section>
      <form
        className="chat-composer"
        onSubmit={(e) => {
          e.preventDefault();
          run(submit());
        }}
      >
        <textarea
          aria-label={t('对话消息')}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={current ? t('和伙伴说点什么…') : t('先设为当前对话，再继续交流')}
          disabled={!current}
        />
        <div>
          <span className="metadata">
            {runtimeLabel(runtime.task)}
            {' · '}
            {t('语音 {0}', runtimeLabel(runtime.voice))}
          </span>
          <button className="button primary" disabled={busy || !current || !text.trim()}>
            {t('发送')}
          </button>
        </div>
      </form>
      <button
        className="text-button danger"
        disabled={busy}
        onClick={() => {
          if (confirm(t('归档此会话？历史仍可查看。')))
            run(act('archive_session', { session_id: data.session_id }).then(() => refresh()));
        }}
      >
        {t('归档此会话')}
      </button>
    </>
  );
}
