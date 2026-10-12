import { t } from '../i18n';
import { preferences, setPreferences, type Language, type Theme } from '../preferences';
import { useState } from 'react';
import type { RuntimeState } from '../types';
import type { View } from '../pages';
export function SettingsOverview({ runtime, navigate }: { runtime: RuntimeState; navigate: (view: View) => void }) {
  const categories: { id: View; symbol: string; title: string; description: string; caption: string }[] = [
    {
      id: 'interface',
      symbol: 'settings',
      title: t('界面偏好'),
      description: t('选择你习惯的语言和界面明暗。'),
      caption: t('语言与主题'),
    },
    {
      id: 'appearance',
      symbol: 'spark',
      title: t('3D 形象'),
      description: t('预览本地形象，选择陪在桌面上的伙伴。'),
      caption: t('实时预览 · 应用形象'),
    },
    {
      id: 'persona',
      symbol: 'chat',
      title: t('角色设定'),
      description: t('调整名字、性格、语气与互动方式。'),
      caption: t('名称 · SOUL.md'),
    },
    {
      id: 'controls',
      symbol: 'settings',
      title: t('语音与桌面'),
      description: t('管理麦克风、屏幕总结、小窗和退出。'),
      caption: t(
        '语音{0} · 屏幕总结{1}',
        runtime.voice === 'off' ? t('关闭') : t('开启'),
        runtime.context_enabled ? t('开启') : t('关闭'),
      ),
    },
    {
      id: 'errors',
      symbol: 'errors',
      title: t('诊断日志'),
      description: t('遇到问题时查看最近的日志和完整记录。'),
      caption: t('级别 · 时间 · 原始详情'),
    },
  ];
  return (
    <div className="settings-overview">
      <div className="settings-intro">
        <svg className="icon">
          <use href="#i-settings" />
        </svg>
        <div>
          <strong>{t('让伙伴更适合你')}</strong>
          <p>{t('日常对话、记忆探索和任务留在主页面；配置与排障集中在这里。')}</p>
        </div>
      </div>
      <div className="settings-category-grid">
        {categories.map((row) => (
          <button className="settings-category" key={row.id} data-view={row.id} onClick={() => navigate(row.id)}>
            <svg className="icon">
              <use href={`#i-${row.symbol}`} />
            </svg>
            <div>
              <strong>{row.title}</strong>
              <p>{row.description}</p>
              <small>{row.caption}</small>
            </div>
            <span aria-hidden="true">›</span>
          </button>
        ))}
      </div>
      <p className="metadata">{t('常用的麦克风和小窗开关也在对话页；任务授权和停止操作在任务页。')}</p>
    </div>
  );
}

export function InterfacePreferences() {
  const [value, setValue] = useState(preferences());
  function update(patch: { language?: Language; theme?: Theme }) {
    setPreferences(patch);
    setValue(preferences());
  }
  return (
    <section className="feature-card interface-preferences">
      <div className="preference-row">
        <div>
          <h2>{t('界面语言')}</h2>
          <p>{t('界面语言仅影响控制台文字，不修改对话、记忆或角色内容。')}</p>
        </div>
        <div className="segmented preference-options" role="group" aria-label={t('界面语言')}>
          <button
            data-language="zh-CN"
            aria-pressed={value.language === 'zh-CN'}
            onClick={() => update({ language: 'zh-CN' })}
          >
            中文
          </button>
          <button data-language="en" aria-pressed={value.language === 'en'} onClick={() => update({ language: 'en' })}>
            English
          </button>
        </div>
      </div>
      <div className="preference-row">
        <div>
          <h2>{t('主题外观')}</h2>
          <p>{t('跟随系统会自动响应系统外观变化。')}</p>
        </div>
        <div className="segmented preference-options" role="group" aria-label={t('主题外观')}>
          {(['light', 'dark', 'system'] as const).map((theme, index) => (
            <button
              key={theme}
              data-theme-option={theme}
              aria-pressed={value.theme === theme}
              onClick={() => update({ theme })}
            >
              {t(['浅色', '深色', '跟随系统'][index])}
            </button>
          ))}
        </div>
      </div>
      <p className="metadata">{t('设置会自动保存，下次打开时继续使用。')}</p>
    </section>
  );
}
