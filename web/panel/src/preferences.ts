export type Language = 'zh-CN' | 'en';
export type Theme = 'light' | 'dark' | 'system';
export interface Preferences { language: Language; theme: Theme; }
const key = 'boxagent.interface-preferences';
export function readPreferences(): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem(key) || '{}');
    return { language: saved.language === 'en' ? 'en' : 'zh-CN', theme: ['light', 'dark'].includes(saved.theme) ? saved.theme : 'system' };
  } catch { return { language: 'zh-CN', theme: 'system' }; }
}
let current = readPreferences();
export function preferences(): Preferences { return current; }
export function resolvedTheme(theme: Theme, systemDark: boolean): 'light' | 'dark' { return theme === 'system' ? systemDark ? 'dark' : 'light' : theme; }
export function applyPreferences(): void {
  document.documentElement.lang = current.language;
  document.documentElement.dataset.theme = resolvedTheme(current.theme, matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.themePreference = current.theme;
}
export function setPreferences(patch: Partial<Preferences>): void {
  current = { ...current, ...patch };
  localStorage.setItem(key, JSON.stringify(current));
  applyPreferences();
  document.dispatchEvent(new CustomEvent('panel-preferences', { detail: current }));
}
export function startPreferences(): void {
  applyPreferences();
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    applyPreferences();
    document.dispatchEvent(new CustomEvent('panel-theme'));
  });
}
export function locale(): string { return current.language; }
