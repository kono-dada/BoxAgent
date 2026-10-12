import { createRoot, type Root } from 'react-dom/client';
import { ContextConsole } from './ContextConsole';
import { FeaturePage, type FeatureView, type RuntimeState } from './FeaturePages';
import { MemoryExplorer } from './MemoryExplorer';
import type { Bridge, ContextSnapshot, MemoryGraph, OpenDetail, TimelineEvent } from './types';
let root: Root | null = null;
export function disposeComplexPage() { root?.unmount(); root = null; }
export function mountComplexPage(host: HTMLElement, view: 'context' | 'memory' | FeatureView, data: unknown, bridge: Bridge, query: string, show: OpenDetail, runtime: RuntimeState = {}, refresh: (sessionId?: string) => Promise<void> = async () => {}) {
  root ||= createRoot(host);
  if (view === 'context') {
    const payload = data as ContextSnapshot & { timeline: TimelineEvent[] };
    root.render(<ContextConsole key={payload.session_id} state={payload} timeline={payload.timeline || []} bridge={bridge} query={query} show={show}/>);
  } else if (view !== 'memory') root.render(<FeaturePage key={`${view}:${(data as {session_id?:string}).session_id || ''}`} view={view} data={data} bridge={bridge} query={query} runtime={runtime} refresh={refresh}/>);
  else root.render(<MemoryExplorer initial={data as MemoryGraph} bridge={bridge} query={query} show={show}/>);
}
