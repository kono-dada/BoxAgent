import { t } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { run, type PageProps } from '../hooks/useAction';
import type { Bridge, AvatarData, AvatarSnapshot } from '../types';
export function Appearance({ data, bridge, busy, act, query, setStatus }: PageProps<AvatarData>) {
  const preview = useRef<HTMLIFrameElement>(null);
  function syncPreviewTheme() {
    if (preview.current && url)
      preview.current.contentWindow?.postMessage(
        { type: 'boxagent-preview-theme', theme: document.documentElement.dataset.theme },
        new URL(url).origin,
      );
  }
  const [picked, setPicked] = useState(data.appearance),
    [url, setUrl] = useState(''),
    [snapshot, setSnapshot] = useState<AvatarSnapshot>(data),
    [loading, setLoading] = useState(false);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setUrl('');
    void bridge
      .request<{ url: string }>('desktop_preview_avatar', { id: picked })
      .then((result) => {
        if (alive) {
          const address = new URL(result.url);
          address.searchParams.set('previewTheme', document.documentElement.dataset.theme || 'light');
          setUrl(address.href);
        }
      })
      .catch((reason) => {
        if (alive) setStatus(reason.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [picked]);
  useEffect(() => {
    document.addEventListener('panel-theme', syncPreviewTheme);
    document.addEventListener('panel-preferences', syncPreviewTheme);
    return () => {
      document.removeEventListener('panel-theme', syncPreviewTheme);
      document.removeEventListener('panel-preferences', syncPreviewTheme);
    };
  }, [url]);
  useEffect(() => {
    let alive = true;
    const timer = setInterval(() => {
      void bridge
        .request<AvatarSnapshot>('desktop_snapshot')
        .then((value) => {
          if (alive) setSnapshot(value);
        })
        .catch(() => {});
    }, 700);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  const rows = data.items.filter(
    (row) => !query || `${row.name} ${row.description}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div className="avatar-layout">
      <section className="feature-card avatar-stage">
        <header>
          <h2>{t('形象预览')}</h2>
          <span className="badge">
            {snapshot.switching ? t('正在加载桌宠') : snapshot.appearance_ready ? t('桌宠已就绪') : t('桌宠加载中')}
          </span>
        </header>
        {url ? (
          <iframe
            ref={preview}
            onLoad={syncPreviewTheme}
            title={t('3D 形象实时预览')}
            src={url}
            sandbox="allow-scripts allow-same-origin"
          />
        ) : (
          <div className="avatar-loading">{loading ? t('正在准备 3D 预览…') : t('选择一个形象预览')}</div>
        )}
        <p className="metadata">{t('预览使用桌宠同一套模型与动作资源。')}</p>
      </section>
      <section className="feature-card">
        <h2>{t('本地形象')}</h2>
        {rows.map((row) => (
          <button
            className={`avatar-choice ${picked === row.id ? 'active' : ''}`}
            key={row.id}
            onClick={() => setPicked(row.id)}
          >
            <strong>{row.name}</strong>
            <span>{row.description || t('VRM · 本地形象')}</span>
            {snapshot.appearance === row.id && <small>{t('正在使用')}</small>}
          </button>
        ))}
        {!rows.length && <p className="metadata">{t('没有匹配的有效形象。')}</p>}
        <button
          className="button primary"
          disabled={busy || snapshot.switching || !picked}
          onClick={() =>
            run(
              act<AvatarSnapshot>('desktop_select_avatar', { id: picked }).then((value) => {
                setSnapshot(value);
                setStatus(value.switching ? t('形象正在预加载，成功后应用。') : value.feedback || t('形象已应用'));
              }),
            )
          }
        >
          {t('应用到桌宠')}
        </button>
        <PetScale snapshot={snapshot} bridge={bridge} setSnapshot={setSnapshot} setStatus={setStatus} />
        {snapshot.appearance_error && <p role="alert">{snapshot.appearance_error}</p>}
        {snapshot.feedback && <p className="metadata">{snapshot.feedback}</p>}
      </section>
    </div>
  );
}
function PetScale({
  snapshot,
  bridge,
  setSnapshot,
  setStatus,
}: {
  snapshot: AvatarSnapshot;
  bridge: Bridge;
  setSnapshot: (value: AvatarSnapshot) => void;
  setStatus: (value: string) => void;
}) {
  const [percent, setPercent] = useState(Math.round((snapshot.scale ?? 1) * 100));
  const [busy, setBusy] = useState(false);
  const pending = useRef<number | null>(null),
    applying = useRef(false),
    mounted = useRef(true),
    revision = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!applying.current && pending.current === null) setPercent(Math.round((snapshot.scale ?? 1) * 100));
  }, [snapshot.scale]);
  async function change(value: number) {
    setPercent(value);
    pending.current = value;
    revision.current++;
    if (applying.current) return;
    applying.current = true;
    setBusy(true);
    try {
      while (pending.current !== null) {
        const next = pending.current,
          requested = revision.current;
        pending.current = null;
        try {
          const result = await bridge.request<AvatarSnapshot>('desktop_set_pet_scale', { scale: next / 100 });
          if (mounted.current && requested === revision.current) setSnapshot(result);
        } catch (reason) {
          if (mounted.current) setStatus(reason instanceof Error ? reason.message : String(reason));
        }
      }
    } finally {
      applying.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <section className="pet-scale">
      <label htmlFor="pet-scale">
        <strong>{t('桌宠大小')}</strong>
        <output htmlFor="pet-scale">{percent}%</output>
      </label>
      <input
        id="pet-scale"
        aria-label={t('桌宠大小百分比')}
        type="range"
        min={Math.round((snapshot.min_scale ?? 0.5) * 100)}
        max={Math.round((snapshot.max_scale ?? 6) * 100)}
        step="1"
        value={percent}
        onChange={(event) => run(change(Number(event.target.value)))}
      />
      <div className="pet-scale-actions">
        <span className="metadata">{t('实际大小受屏幕范围限制，重启与换装保留。')}</span>
        <button className="button" disabled={busy} onClick={() => run(change(100))}>
          {t('恢复默认')}
        </button>
      </div>
    </section>
  );
}
