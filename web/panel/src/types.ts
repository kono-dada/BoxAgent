export type RecordData = Record<string, unknown>;
export interface Entry {
  seq: number;
  category: string;
  label: string;
  role?: string;
  tokens: number;
  preview: string;
  time?: number;
}
export interface Composition {
  key: string;
  label: string;
  tokens: number;
  count: number;
  percent: number;
}
export interface RequestSnapshot {
  seq: number;
  time: number;
  entries: number[];
  added: number[];
  removed: number[];
}
export interface Turn {
  index: number;
  started_at: number;
  user: string;
  response_ms: number | null;
  assistant_ms: number | null;
  tool_calls: string[];
  memory: { status: string; late?: boolean } | null;
}
export interface ContextSnapshot {
  status: string;
  message?: string;
  session_id: string;
  model: string;
  runtime: string;
  generation: number;
  generation_started_at?: number;
  totals: { tokens: number; entries: number };
  entries: Entry[];
  provider_entries: Entry[];
  composition: Composition[];
  requests: RequestSnapshot[];
  turns: Turn[];
  delete_ack_count: number;
  expired_count: number;
}
export interface TimelineEvent {
  seq: number;
  type: string;
  time: number;
  data: RecordData;
}
export interface MemoryNode {
  id: string;
  type: string;
  content: string;
  timestamp?: string;
  source?: string;
  metadata?: RecordData;
}
export interface MemoryEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  subtype?: string;
}
export interface MemoryGraph {
  nodes: MemoryNode[];
  edges: MemoryEdge[];
  statistics?: { node_count: number; edge_count: number; matched_count?: number };
}
export interface Bridge {
  request<T = unknown>(method: string, params?: RecordData): Promise<T>;
}
export type OpenDetail = (value: unknown, title: string, meta?: string, trigger?: HTMLElement) => void;

export interface Session {
  session_id: string;
  title: string;
  updated_at: number;
  archived?: boolean;
}
export interface ProductMessage {
  type: string;
  sequence: number;
  event_id?: string;
  role?: string;
  content?: string;
  occurred_at?: number;
}
export interface SessionMessages {
  session_id: string;
  sessions: Session[];
  events: ProductMessage[];
}
export interface RuntimeState {
  session_id?: string;
  voice?: string;
  task?: string;
  task_text?: string;
  approval?: string;
  error?: string;
  context_enabled?: boolean;
  context_status?: string;
  context_text?: string;
}
export interface Avatar {
  id: string;
  name: string;
  description?: string;
}
export interface AvatarSnapshot {
  appearance?: string;
  appearance_ready?: boolean;
  appearance_error?: string;
  switching?: boolean;
  feedback?: string;
  scale?: number;
  min_scale?: number;
  max_scale?: number;
}
export interface AvatarData extends AvatarSnapshot {
  items: Avatar[];
}
export interface PersonaData {
  name: string;
  content: string;
  path?: string;
}
export interface Skill extends RecordData {
  skill_id: string;
  name: string;
  description: string;
  instructions: string;
  source?: string;
  enabled?: boolean;
  path?: string;
}
export interface RunRecord {
  id: string;
  manifest: RecordData;
  files: string[];
}
export interface RunData {
  session_id: string;
  items: RunRecord[];
}
export interface PanelBridge extends Bridge {
  errors(): Promise<RecordData[]>;
  onState(callback: (state: RuntimeState) => void): () => void;
  onDiagnostic(callback: (message: string) => void): () => void;
}
declare global {
  interface Window {
    boxagent: PanelBridge;
  }
}
