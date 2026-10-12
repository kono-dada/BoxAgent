import { createRoot, type Root } from 'react-dom/client';
import { ContextConsole } from './ContextConsole';
import { MemoryExplorer } from './MemoryExplorer';
import type { Bridge, ContextSnapshot, MemoryGraph, OpenDetail, TimelineEvent } from './types';
let root: Root | null = null;
export function disposeComplexPage() { root?.unmount(); root = null; }
export function mountComplexPage(host: HTMLElement, view: 'context' | 'memory', data: unknown, bridge: Bridge, query: string, show: OpenDetail) {
  root ||= createRoot(host);
  if (view === 'context') {
    const payload = data as ContextSnapshot & { timeline: TimelineEvent[] };
    root.render(<ContextConsole key={payload.session_id} state={payload} timeline={payload.timeline || []} bridge={bridge} query={query} show={show}/>);
  } else root.render(<MemoryExplorer initial={data as MemoryGraph} bridge={bridge} query={query} show={show}/>);
}
