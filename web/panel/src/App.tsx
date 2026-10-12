import { useCallback, useEffect, useRef, useState } from 'react';
import { t } from './i18n';
import { pageInfo, settingViews, globalViews, type View } from './pages';
import { readPage, type LoadedPage } from './data';
import { normalizeEvent } from './event-model';
import { Sidebar } from './components/Sidebar';
import { PageContent } from './components/PageContent';
import { RecordDetail, type Detail } from './components/RecordDetail';
import { EmptyState, Icon, number, matches } from './components/ui';
import { runtimeLabel } from './hooks/useAction';
import type { PanelBridge, RuntimeState, Session } from './types';

interface Route {
  view: View;
  selected: string;
  query: string;
  previous: View;
}
interface ContentState {
  scope?: string;
  data: LoadedPage | null;
  loading: boolean;
  error: string;
}
export function App({ bridge }: { bridge: PanelBridge }) {
  const [route, setRoute] = useState<Route>({
    view: 'conversation',
    selected: '',
    query: '',
    previous: 'conversation',
  });
  const [sessions, setSessions] = useState<Session[]>([]),
    [runtime, setRuntime] = useState<RuntimeState>({});
  const [online, setOnline] = useState(false),
    [diagnostic, setDiagnostic] = useState(''),
    [refreshing, setRefreshing] = useState(false);
  const [content, setContent] = useState<ContentState>({ data: null, loading: true, error: '' }),
    [detail, setDetail] = useState<Detail | null>(null);
  const [, setLanguageRevision] = useState(0),
    [reload, setReload] = useState(0);
  const page = useRef<HTMLElement>(null),
    routeRef = useRef(route),
    runtimeRef = useRef(runtime);
  routeRef.current = route;
  runtimeRef.current = runtime;
  const saved = useRef(new Map<string, { scroll: number; query: string }>()),
    restoreScroll = useRef<number | null>(null),
    refreshId = useRef(0),
    loadId = useRef(0);
  const key = (view: View, selected: string) => `${view}:${globalViews.has(view) ? 'global' : selected}`;
  const scope = key(route.view, route.selected);
  function change(next: View, selected = route.selected) {
    if (next === route.view && selected === route.selected) return;
    saved.current.set(key(route.view, route.selected), { scroll: page.current?.scrollTop || 0, query: route.query });
    const state = saved.current.get(key(next, selected));
    restoreScroll.current = state?.scroll || 0;
    setRoute({
      view: next,
      selected,
      query: state?.query || '',
      previous: settingViews.has(route.view) ? route.previous : route.view,
    });
  }
  const refresh = useCallback(
    async (selected?: string) => {
      const mine = ++refreshId.current;
      loadId.current++;
      restoreScroll.current = page.current?.scrollTop || 0;
      setRefreshing(true);
      try {
        const [state, list] = await Promise.allSettled([
          bridge.request<RuntimeState>('state'),
          bridge.request<Session[]>('list_sessions', { include_archived: true }),
        ]);
        if (mine !== refreshId.current) return;
        if (state.status === 'fulfilled') {
          setRuntime(state.value);
          runtimeRef.current = state.value;
          setOnline(true);
          setDiagnostic('');
        } else {
          setOnline(false);
          setDiagnostic(state.reason instanceof Error ? state.reason.message : String(state.reason));
        }
        if (list.status === 'fulfilled') {
          setSessions(list.value);
          const current = selected || routeRef.current.selected;
          const id = list.value.some((row) => row.session_id === current)
            ? current
            : list.value.find((row) => row.session_id === runtimeRef.current.session_id)?.session_id ||
              list.value[0]?.session_id ||
              '';
          if (id !== routeRef.current.selected) setRoute((value) => ({ ...value, selected: id }));
        } else if (!globalViews.has(routeRef.current.view)) {
          setContent({ data: null, loading: false, error: t('先启动桌宠或 Engine。面板会自动重连，也可以点击刷新。') });
          return;
        }
        setReload((value) => value + 1);
      } finally {
        if (mine === refreshId.current) setRefreshing(false);
      }
    },
    [bridge],
  );
  useEffect(() => {
    const stopState = bridge.onState((state) => {
      setOnline(true);
      setRuntime((current) => ({ ...current, ...state }));
    });
    const stopDiagnostic = bridge.onDiagnostic((message) => {
      if (message === 'Engine 已连接') {
        setOnline(true);
        setDiagnostic('');
        void refresh();
      } else {
        setOnline(false);
        setDiagnostic(message);
      }
    });
    void refresh();
    return () => {
      stopState();
      stopDiagnostic();
      refreshId.current++;
    };
  }, [bridge, refresh]);
  useEffect(() => {
    const listener = () => setLanguageRevision((value) => value + 1);
    document.addEventListener('panel-preferences', listener);
    return () => document.removeEventListener('panel-preferences', listener);
  }, []);
  useEffect(() => {
    document.title = t('伙伴 · 控制台');
    document.body.dataset.view = route.view;
  });
  useEffect(() => {
    let alive = true;
    const mine = ++loadId.current;
    if (!route.selected && !globalViews.has(route.view)) {
      setContent({ data: null, loading: false, error: '' });
      return;
    }
    setContent({ data: null, loading: true, error: '' });
    void readPage(bridge, route.view, route.selected, sessions)
      .then((data) => {
        if (alive && mine === loadId.current) setContent({ scope, data, loading: false, error: '' });
      })
      .catch((error) => {
        if (alive && mine === loadId.current)
          setContent({ data: null, loading: false, error: error instanceof Error ? error.message : String(error) });
      });
    return () => {
      alive = false;
    };
    // Session list refresh drives reload; query filtering never re-fetches or remounts a form.
  }, [bridge, scope, reload]);
  useEffect(() => {
    if (!content.loading && page.current && restoreScroll.current !== null) {
      page.current.scrollTop = restoreScroll.current;
      restoreScroll.current = null;
    }
  }, [content.loading]);
  const info = pageInfo(route.view),
    data = content.data;
  const loading = content.loading || Boolean(data && content.scope !== scope);
  let summary = '',
    count = '';
  if (loading) summary = t('正在读取…');
  else if (content.error) summary = t(online ? '暂时无法读取' : '等待连接到本机 Engine');
  else if (!data) summary = t('尚未选择产品会话');
  else if (route.view === 'memory') summary = t('记忆关系与结构 · 选择节点探索关联');
  else if (data.view === 'context')
    summary =
      data.data.status === 'ready' ? t('{0} · 宿主记录的请求控制台', data.data.model || 'Qwen') : t('等待会话上下文');
  else if (data.view === 'timeline') {
    const all = data.data.map(normalizeEvent),
      filtered = all.filter((row) =>
        matches(route.query, row.label, row.type, row.preview, row.query, row.runtime, row.seq),
      );
    summary = t('本会话最近 {0} 条事件 · 对话、上下文与任务', number(all.length));
    count = `${number(filtered.length)}${filtered.length === all.length ? '' : ` / ${number(all.length)}`} ${t('条')}`;
  } else if (data.view === 'errors') {
    const all = data.data,
      filtered = all.filter((row) => matches(route.query, JSON.stringify(row))),
      errors = all.filter((row) => ['error', 'critical'].includes(String(row.level).toLowerCase())).length;
    summary = t('最近 {0} 条诊断记录 · {1} 条错误', all.length, errors);
    count = `${number(filtered.length)}${filtered.length === all.length ? '' : ` / ${number(all.length)}`} ${t('条')}`;
  }
  const desktopRequired = ['appearance', 'persona', 'runs'].includes(route.view);
  return (
    <>
      <div className="workspace">
        <Sidebar
          view={route.view}
          selected={route.selected}
          sessions={sessions}
          online={online}
          message={diagnostic}
          navigate={(view) => change(view)}
          select={(id) => change(route.view, id)}
        />
        <main className="page" id="page" ref={page}>
          <div className="page-topbar">
            <span id="scope">
              {globalViews.has(route.view)
                ? t('本机 / 全局记录')
                : sessions.find((row) => row.session_id === route.selected)?.title || t('产品会话')}
            </span>
            <span id="runtime-status">
              {online
                ? t('语音 {0} · 任务 {1}', runtimeLabel(runtime.voice), runtimeLabel(runtime.task))
                : t('等待 Engine 连接')}
            </span>
          </div>
          <div className="page-inner">
            <header className="page-header">
              <div>
                <p className="eyebrow" id="eyebrow">
                  {info.eyebrow}
                </p>
                <h1 id="page-title">{info.title}</h1>
                <p className="page-description" id="description">
                  {info.description}
                </p>
              </div>
              <button
                id="refresh"
                className="button"
                title={t('刷新当前数据')}
                disabled={refreshing}
                onClick={() => void refresh()}
              >
                <Icon name="refresh" />
                <span>{t('刷新')}</span>
              </button>
            </header>
            <nav
              id="settings-tabs"
              className="settings-tabs"
              aria-label={t('设置分类')}
              hidden={!settingViews.has(route.view)}
            >
              {(['settings', 'interface', 'appearance', 'persona', 'controls', 'errors'] as View[]).map((view) => (
                <button
                  key={view}
                  data-view={view}
                  aria-current={route.view === view ? 'page' : undefined}
                  onClick={() => change(view)}
                >
                  {view === 'settings' ? t('概览') : pageInfo(view).title}
                </button>
              ))}
              <button id="settings-back" className="settings-back" onClick={() => change(route.previous)}>
                {t('返回{0}', pageInfo(route.previous).title)}
              </button>
            </nav>
            <div id="summary" className="summary" role="status" aria-live="polite">
              {summary}
            </div>
            <div
              className="content-tools"
              hidden={['settings', 'persona', 'controls', 'interface'].includes(route.view)}
            >
              <label className="search">
                <Icon name="search" />
                <input
                  id="search"
                  type="search"
                  placeholder={info.search}
                  aria-label={t('查找当前页内容')}
                  autoComplete="off"
                  value={route.query}
                  onChange={(event) => {
                    setRoute((value) => ({ ...value, query: event.target.value }));
                    if (page.current) page.current.scrollTop = 0;
                  }}
                />
              </label>
              <span id="result-count" className="metadata">
                {count}
              </span>
            </div>
            <div id="content" aria-busy={loading}>
              {loading ? (
                <>
                  {[0, 1, 2].map((index) => (
                    <div className="skeleton" aria-hidden="true" key={index} />
                  ))}
                </>
              ) : content.error ? (
                <EmptyState
                  title={t(desktopRequired ? '需要连接桌宠' : online ? '数据暂时不可用' : '连接后，继续查看伙伴的记录')}
                  message={desktopRequired ? t('请启动桌宠后刷新；3D 形象和角色设置由桌面宿主负责。') : content.error}
                  retry={() => void refresh()}
                />
              ) : data ? (
                <PageContent
                  key={`${route.view}:${globalViews.has(route.view) ? 'global' : route.selected}:${reload}`}
                  page={data}
                  bridge={bridge}
                  query={route.query.trim()}
                  runtime={runtime}
                  refresh={refresh}
                  navigate={(view) => change(view)}
                  show={(value, title, meta = '', trigger) =>
                    setDetail({ value, title, meta, trigger: trigger || (document.activeElement as HTMLElement) })
                  }
                />
              ) : (
                <EmptyState
                  title={t('从一次对话开始')}
                  message={t('有会话后，可以在左侧选择并查看它的上下文与时间线。')}
                  icon="chat"
                />
              )}
            </div>
            <p className="page-footnote" id="footnote">
              {info.footnote}
            </p>
          </div>
        </main>
      </div>
      <RecordDetail detail={detail} close={() => setDetail(null)} />
    </>
  );
}
