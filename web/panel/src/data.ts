import type { View } from './pages';
import type {
  PanelBridge,
  Session,
  SessionMessages,
  AvatarData,
  PersonaData,
  Skill,
  RunData,
  ContextSnapshot,
  TimelineEvent,
  RecordData,
  MemoryGraph,
  RuntimeState,
} from './types';
import type { EventRecord } from './event-model';

export type ContextData = ContextSnapshot & { timeline: TimelineEvent[] };
interface PageDataMap {
  conversation: SessionMessages;
  memory: MemoryGraph;
  context: ContextData;
  timeline: EventRecord[];
  errors: RecordData[];
  appearance: AvatarData;
  persona: PersonaData;
  skills: Skill[];
  runs: RunData;
  controls: RuntimeState & { unavailable?: string };
  settings: RecordData;
  interface: RecordData;
}
export type LoadedPage = { [K in View]: { view: K; data: PageDataMap[K] } }[View];
// Keep page-specific IPC reads together; widgets receive only their required data.
export async function readPage(
  bridge: PanelBridge,
  view: View,
  selected: string,
  sessions: Session[],
): Promise<LoadedPage> {
  switch (view) {
    case 'settings':
    case 'interface':
      return { view, data: {} };
    case 'errors':
      return { view, data: await bridge.errors() };
    case 'memory':
      return { view, data: await bridge.request<MemoryGraph>('memory_snapshot', { node_limit: 100, edge_limit: 500 }) };
    case 'appearance':
      return { view, data: await bridge.request<AvatarData>('desktop_avatars') };
    case 'persona':
      return { view, data: await bridge.request<PersonaData>('desktop_persona') };
    case 'skills':
      return { view, data: await bridge.request<Skill[]>('list_skills') };
    case 'controls':
      try {
        return { view, data: await bridge.request<RuntimeState>('desktop_snapshot') };
      } catch (error) {
        return { view, data: { unavailable: error instanceof Error ? error.message : String(error) } };
      }
    case 'conversation':
      return {
        view,
        data: {
          session_id: selected,
          sessions,
          events: await bridge.request<SessionMessages['events']>('session_events', {
            session_id: selected,
            limit: 240,
          }),
        },
      };
    case 'runs':
      return {
        view,
        data: {
          session_id: selected,
          ...(await bridge.request<Omit<RunData, 'session_id'>>('desktop_runs', { session_id: selected })),
        },
      };
    case 'context': {
      const [snapshot, timeline] = await Promise.all([
        bridge.request<ContextSnapshot>('context_snapshot', { session_id: selected }),
        bridge.request<TimelineEvent[]>('session_timeline', { session_id: selected, limit: 500 }),
      ]);
      return { view, data: { ...snapshot, timeline } };
    }
    case 'timeline':
      return {
        view,
        data: await bridge.request<EventRecord[]>('session_timeline', { session_id: selected, limit: 150 }),
      };
  }
}
