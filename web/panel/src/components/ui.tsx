import { t } from '../i18n';
import { locale } from '../preferences';
export function Icon({ name }: { name: string }) {
  return (
    <svg className="icon" aria-hidden="true">
      <use href={`#i-${name}`} />
    </svg>
  );
}
export function EmptyState({
  title,
  message,
  icon = 'info',
  retry,
}: {
  title: string;
  message: string;
  icon?: string;
  retry?: () => void;
}) {
  return (
    <div className="empty-state">
      <div className="empty-symbol">
        <Icon name={icon} />
      </div>
      <h2>{title}</h2>
      <p>{message}</p>
      {retry && (
        <button className="button" onClick={retry}>
          {t('重新连接 / 刷新')}
        </button>
      )}
    </div>
  );
}
export function number(value: number) {
  return Number(value || 0).toLocaleString(locale());
}
function date(value: unknown): Date | null {
  if (!value || (typeof value !== 'number' && typeof value !== 'string')) return null;
  const result = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(result.getTime()) ? null : result;
}
export function day(value: unknown): string {
  const parsed = date(value);
  if (!parsed) return t('时间未记录');
  const today = new Date(),
    yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (parsed.toDateString() === today.toDateString()) return t('今天');
  if (parsed.toDateString() === yesterday.toDateString()) return t('昨天');
  return parsed.toLocaleDateString(locale(), { year: 'numeric', month: 'long', day: 'numeric' });
}
export function time(value: unknown): string {
  return date(value)?.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', hour12: false }) || '—';
}
export function matches(query: string, ...values: unknown[]): boolean {
  const needle = query.trim().toLocaleLowerCase();
  return (
    !needle ||
    values.some((value) =>
      String(value ?? '')
        .toLocaleLowerCase()
        .includes(needle),
    )
  );
}
