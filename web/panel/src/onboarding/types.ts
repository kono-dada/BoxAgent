export type Step = 'welcome' | 'providers' | 'components' | 'permissions' | 'ready';
export interface Capability { state: 'checking' | 'ready' | 'missing' | 'invalid' | 'unavailable'; code: string }
export interface StartupSnapshot {
  mode: 'checking' | 'needs_setup' | 'repair' | 'ready' | 'degraded';
  onboarding: { version: number; completed: boolean; step: Step; completedAt: string | null };
  credentials: { dashscope: boolean; deepseek: boolean; workspace: string; provider: 'deepseek' | 'codex' };
  capabilities: Record<string, Capability>;
  permissions: { microphone: string; screen: string; accessibility: boolean };
  busy: boolean; error: string; canComplete: boolean; packaged: boolean;
  progress: { component: string; phase?: string; file?: string; bytes?: number; total?: number | null } | null;
}
export interface SetupBridge {
  request(method: string, params?: Record<string, string>): Promise<StartupSnapshot>;
  subscribe(callback: (snapshot: StartupSnapshot) => void): () => void;
}
declare global { interface Window { boxagentSetup: SetupBridge } }
