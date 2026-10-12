export type RecordData = Record<string, unknown>;
export interface Entry { seq: number; category: string; label: string; role?: string; tokens: number; preview: string; time?: number; }
export interface Composition { key: string; label: string; tokens: number; count: number; percent: number; }
export interface RequestSnapshot { seq: number; time: number; entries: number[]; added: number[]; removed: number[]; }
export interface Turn { index: number; started_at: number; user: string; response_ms: number | null; assistant_ms: number | null; tool_calls: string[]; memory: { status: string; late?: boolean } | null; }
export interface ContextSnapshot { status: string; message?: string; session_id: string; model: string; runtime: string; generation: number; generation_started_at?: number; totals: { tokens: number; entries: number }; entries: Entry[]; provider_entries: Entry[]; composition: Composition[]; requests: RequestSnapshot[]; turns: Turn[]; delete_ack_count: number; expired_count: number; }
export interface TimelineEvent { seq: number; type: string; time: number; data: RecordData; }
export interface MemoryNode { id: string; type: string; content: string; timestamp?: string; source?: string; metadata?: RecordData; }
export interface MemoryEdge { id: string; source: string; target: string; type: string; subtype?: string; }
export interface MemoryGraph { nodes: MemoryNode[]; edges: MemoryEdge[]; statistics?: { node_count: number; edge_count: number; matched_count?: number }; }
export interface Bridge { request(method: string, params?: RecordData): Promise<unknown>; }
export type OpenDetail = (value: unknown, title: string, meta?: string, trigger?: HTMLElement) => void;
