import { t } from '../i18n';
import { useState } from 'react';
import { run, type PageProps } from '../hooks/useAction';
import type { PersonaData } from '../types';
export function Persona({ data, busy, act }: PageProps<PersonaData>) {
  const [name, setName] = useState(data.name),
    [content, setContent] = useState(data.content);
  return (
    <form
      className="feature-card feature-form"
      onSubmit={(e) => {
        e.preventDefault();
        run(
          act<PersonaData>('desktop_save_persona', { name, content }).then((value) => {
            setName(value.name);
            setContent(value.content);
          }),
        );
      }}
    >
      <label>
        {t('角色名称')}
        <input aria-label={t('角色名称')} value={name} maxLength={24} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        {t('性格与互动方式 · SOUL.md')}
        <textarea
          aria-label={t('角色设定内容')}
          className="code-editor"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      </label>
      <div className="feature-actions">
        <span className="metadata">{data.path}</span>
        <button className="button primary" disabled={busy || !name.trim()}>
          {t('保存并应用')}
        </button>
      </div>
    </form>
  );
}
