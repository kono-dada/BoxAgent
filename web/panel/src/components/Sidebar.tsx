import { Fragment } from 'react';
import { t } from '../i18n';
import { mainNavigation, settingViews, pageInfo, type View } from '../pages';
import { Icon, day } from './ui';
import type { Session } from '../types';
export function Sidebar({
  view,
  selected,
  sessions,
  online,
  message,
  navigate,
  select,
}: {
  view: View;
  selected: string;
  sessions: Session[];
  online: boolean;
  message: string;
  navigate: (view: View) => void;
  select: (id: string) => void;
}) {
  return (
    <aside className="sidebar" aria-label={t('会话管理导航')}>
      <div className="window-drag" aria-hidden="true" />
      <div className="brand">
        <span className="brand-mark">
          <img src="brand/boxagent-128.png" width="34" height="34" alt="" />
        </span>
        <div>
          <strong>BoxAgent</strong>
          <span>{t('陪伴与管理')}</span>
        </div>
      </div>
      <nav className="navigation" aria-label={t('主要功能')}>
        {mainNavigation.map((row, index) => (
          <Fragment key={row.view}>
            {index === 4 && <span className="navigation-label">{t('观察')}</span>}
            <button
              data-view={row.view}
              aria-current={view === row.view ? 'page' : undefined}
              onClick={() => navigate(row.view)}
            >
              <Icon name={row.icon} />
              <span>{pageInfo(row.view).title}</span>
            </button>
          </Fragment>
        ))}
      </nav>
      <div className="session-heading">
        <h2>{t('产品会话')}</h2>
        <span id="session-count">{sessions.length}</span>
      </div>
      <div id="sessions" className="session-list" aria-label={t('会话列表')}>
        {sessions.map((row) => (
          <button
            key={row.session_id}
            data-session-id={row.session_id}
            title={row.title}
            aria-current={selected === row.session_id ? 'true' : undefined}
            onClick={() => select(row.session_id)}
          >
            <Icon name="chat" />
            <span className="session-copy">
              <span className="session-title">{row.title || t('未命名会话')}</span>
              <span className="session-date">
                {[day(row.updated_at), row.archived ? t('已归档') : ''].filter(Boolean).join(' · ')}
              </span>
            </span>
          </button>
        ))}
        {!sessions.length && <p className="sidebar-empty">{t('还没有会话')}</p>}
      </div>
      <footer className="sidebar-footer">
        <button
          id="settings-button"
          className="settings-button"
          data-view="settings"
          aria-controls="settings-tabs"
          aria-expanded={settingViews.has(view)}
          aria-current={settingViews.has(view) ? 'true' : undefined}
          onClick={() => navigate('settings')}
        >
          <Icon name="settings" />
          <span>{t('设置')}</span>
        </button>
        <span
          id="connection"
          data-state={online ? 'online' : 'offline'}
          title={message || (online ? t('本机 Engine 已连接') : t('本机 Engine 未连接'))}
        >
          <span className="status-dot" />
          <span id="status" role="status">
            {t(online ? '已连接' : '未连接')}
          </span>
        </span>
      </footer>
    </aside>
  );
}
