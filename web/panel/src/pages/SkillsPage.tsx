import { t } from '../i18n';
import { useState } from 'react';
import { run, type PageProps } from '../hooks/useAction';
import type { Skill } from '../types';
export function Skills({ data, busy, act, query }: PageProps<Skill[]>) {
  const [items, setItems] = useState(data),
    [selected, setSelected] = useState<Skill | null>(data[0] || null),
    [draft, setDraft] = useState<Skill>(data[0] || { skill_id: '', name: '', description: '', instructions: '' });
  const creating = selected === null,
    editable = creating || selected.source === 'user';
  async function reload(id: string) {
    const rows = await act<Skill[]>('list_skills');
    setItems(rows);
    const row = rows.find((item) => item.skill_id === id) || rows[0] || null;
    setSelected(row);
    setDraft(row || { skill_id: '', name: '', description: '', instructions: '' });
  }
  return (
    <div className="skill-layout">
      <section className="feature-card">
        <header>
          <h2>{t('已安装 Skill')}</h2>
          <button
            className="button"
            disabled={busy}
            onClick={() => {
              setSelected(null);
              setDraft({ skill_id: '', name: '', description: '', instructions: '' });
            }}
          >
            {t('新建')}
          </button>
        </header>
        {items
          .filter(
            (row) =>
              !query || `${row.name} ${row.description} ${row.skill_id}`.toLowerCase().includes(query.toLowerCase()),
          )
          .map((row) => (
            <button
              key={row.skill_id}
              className={`avatar-choice ${selected?.skill_id === row.skill_id ? 'active' : ''}`}
              onClick={() => {
                setSelected(row);
                setDraft(row);
              }}
            >
              <strong>{row.name}</strong>
              <span>
                {row.source === 'builtin' ? t('内置') : t('用户')} · {row.enabled ? t('已启用') : t('已停用')}
              </span>
            </button>
          ))}
      </section>
      <form
        className="feature-card feature-form"
        onSubmit={(e) => {
          e.preventDefault();
          run(act<Skill>(creating ? 'create_skill' : 'update_skill', draft).then((value) => reload(value.skill_id)));
        }}
      >
        <h2>{creating ? t('新建 Skill') : draft.name}</h2>
        {(['skill_id', 'name', 'description'] as const).map((key, index) => (
          <label key={key}>
            {['ID', t('名称'), t('描述')][index]}
            <input
              aria-label={`Skill ${key}`}
              value={draft[key]}
              disabled={!editable || (key === 'skill_id' && !creating)}
              onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
            />
          </label>
        ))}
        <label>
          {t('说明')}
          <textarea
            className="code-editor"
            aria-label={t('Skill 说明')}
            value={draft.instructions}
            disabled={!editable}
            onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
          />
        </label>
        {selected && (
          <label className="switch-line">
            <input
              type="checkbox"
              aria-label={t('启用此 Skill')}
              checked={selected.enabled}
              disabled={busy}
              onChange={(e) =>
                run(
                  act('set_skill_enabled', { skill_id: selected.skill_id, enabled: e.target.checked }).then(() =>
                    reload(selected.skill_id),
                  ),
                )
              }
            />
            {t('启用此 Skill')}
          </label>
        )}
        <div className="feature-actions">
          <span className="metadata">
            {!editable ? t('内置 Skill 只读，可启停') : draft.path || t('保存到用户 Skill 目录')}
          </span>
          {editable && (
            <button className="button primary" disabled={busy}>
              {t('保存')}
            </button>
          )}
          {selected?.source === 'user' && (
            <button
              className="button danger"
              type="button"
              disabled={busy}
              onClick={() => {
                if (confirm(t('删除 Skill「{0}」？本地文件将被删除。', selected.name)))
                  run(act('delete_skill', { skill_id: selected.skill_id }).then(() => reload('')));
              }}
            >
              {t('删除')}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
