import { preferences } from './preferences';
import english from './locales/en.json';
const catalog: Record<string, string> = english;
export function t(text: string, ...values: (string | number)[]): string {
  const template = preferences().language === 'en' ? catalog[text] || text : text;
  return template.replace(/\{(\d+)\}/g, (match, index: string) => String(values[Number(index)] ?? match));
}
