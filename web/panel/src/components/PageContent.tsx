import { t } from '../i18n';
import { useEffect } from 'react';
import { ContextConsole } from '../ContextConsole';
import { MemoryExplorer } from '../MemoryExplorer';
import { Conversation } from '../pages/ConversationPage';
import { Appearance } from '../pages/AppearancePage';
import { Persona } from '../pages/PersonaPage';
import { Skills } from '../pages/SkillsPage';
import { Runs } from '../pages/RunsPage';
import { Controls } from '../pages/ControlsPage';
import { SettingsOverview, InterfacePreferences } from '../pages/SettingsPage';
import { Timeline, DiagnosticLogs } from '../pages/ObservationPages';
import { useAction, type Refresh } from '../hooks/useAction';
import { EmptyState } from './ui';
import type { View } from '../pages';
import type { LoadedPage } from '../data';
import type { PanelBridge, RuntimeState, OpenDetail } from '../types';

export function PageContent({
  page,
  bridge,
  query,
  runtime,
  refresh,
  show,
  navigate,
}: {
  page: LoadedPage;
  bridge: PanelBridge;
  query: string;
  runtime: RuntimeState;
  refresh: Refresh;
  show: OpenDetail;
  navigate: (view: View) => void;
}) {
  const action = useAction(bridge),
    shared = { ...action, bridge, query, runtime, refresh };
  useEffect(() => bridge.onState(() => action.clearConnectionError()), [bridge]);
  const { view, data } = page;
  if (view === 'context')
    return data.status === 'ready' ? (
      <ContextConsole state={data} timeline={data.timeline} bridge={bridge} query={query} show={show} />
    ) : (
      <EmptyState
        title={t('这里还没有上下文')}
        message={data.message || t('开启对话后，再来看看伙伴记住了哪些信息。')}
        icon="context"
      />
    );
  if (view === 'memory') return <MemoryExplorer initial={data} bridge={bridge} query={query} show={show} />;
  if (view === 'timeline') return <Timeline data={data} query={query} show={show} />;
  if (view === 'errors') return <DiagnosticLogs data={data} query={query} show={show} />;
  return (
    <div className="feature-page">
      {action.status && (
        <p role="status" className="feature-status">
          {action.status}
        </p>
      )}
      {view === 'settings' ? (
        <SettingsOverview runtime={runtime} navigate={navigate} />
      ) : view === 'interface' ? (
        <InterfacePreferences />
      ) : view === 'conversation' ? (
        <Conversation {...shared} data={data} />
      ) : view === 'appearance' ? (
        <Appearance {...shared} data={data} />
      ) : view === 'persona' ? (
        <Persona {...shared} data={data} />
      ) : view === 'skills' ? (
        <Skills {...shared} data={data} />
      ) : view === 'runs' ? (
        <Runs {...shared} data={data} />
      ) : (
        <Controls {...shared} data={data} />
      )}
    </div>
  );
}
