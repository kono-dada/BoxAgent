import { t } from '../i18n';
import { run, runtimeLabel, type PageProps } from '../hooks/useAction';
import type { RunData } from '../types';
export function TaskControls({ runtime, busy, act }: PageProps<RunData>) {
  return (
    <section className="feature-card">
      <header>
        <h2>{t('当前后台任务')}</h2>
        <span className="badge">{runtimeLabel(runtime.task)}</span>
      </header>
      <p>{runtime.task_text || t('当前没有任务')}</p>
      {runtime.approval && (
        <div className="approval-card">
          <p>{runtime.approval}</p>
          <button
            className="button primary"
            disabled={busy}
            onClick={() => run(act('answer_approval', { allowed: true }))}
          >
            {t('允许操作')}
          </button>
          <button className="button" disabled={busy} onClick={() => run(act('answer_approval', { allowed: false }))}>
            {t('拒绝')}
          </button>
        </div>
      )}
      <button
        className="button"
        disabled={busy || !['accepted', 'running', 'awaiting_approval', 'cancelling'].includes(String(runtime.task))}
        onClick={() => run(act('cancel_task'))}
      >
        {t('停止后台任务')}
      </button>
      {runtime.error && (
        <p role="alert" className="feature-status">
          {runtime.error}
        </p>
      )}
    </section>
  );
}
export function Runs(props: PageProps<RunData>) {
  const { data, query, busy, act } = props;
  return (
    <>
      <TaskControls {...props} />
      <div className="feature-actions">
        <button
          className="button"
          disabled={busy}
          onClick={() => run(act('desktop_open_run', { session_id: data.session_id, latest: true }))}
        >
          {t('打开最近运行')}
        </button>
        <button
          className="button"
          disabled={busy}
          onClick={() => run(act('desktop_open_run', { session_id: data.session_id }))}
        >
          {t('打开会话运行目录')}
        </button>
      </div>
      <div className="run-list">
        {data.items
          .filter((row) => !query || JSON.stringify(row.manifest).toLowerCase().includes(query.toLowerCase()))
          .map((row) => (
            <details className="feature-card" key={row.id}>
              <summary>
                <strong>
                  {String(row.manifest.goal || row.manifest.task_text || row.manifest.task_id || t('任务运行'))}
                </strong>
                <span className="metadata">{String(row.manifest.status || t('已记录'))}</span>
              </summary>
              <p className="metadata">{row.files.join(' · ')}</p>
              <pre>{JSON.stringify(row.manifest, null, 2)}</pre>
              <button
                className="button"
                disabled={busy}
                onClick={() => run(act('desktop_open_run', { session_id: data.session_id, id: row.id }))}
              >
                {t('打开运行产物')}
              </button>
            </details>
          ))}
        {!data.items.length && <p className="metadata">{t('此会话还没有运行目录。')}</p>}
      </div>
    </>
  );
}
