import { t } from '../i18n';
import { useEffect, useState } from 'react';
import { Camera } from 'lucide-react';
import type { AvatarSnapshot } from '../types';
import { run, runtimeLabel, type PageProps } from '../hooks/useAction';
export function Controls(props: PageProps<AvatarSnapshot & { unavailable?: string }>) {
  const { data, runtime, busy, act } = props;
  const [desktop, setDesktop] = useState<AvatarSnapshot>(data);
  useEffect(() => window.boxagent.onDesktop(setDesktop), []);
  return (
    <>
      <section className="feature-card control-settings">
        <div>
          <section><h2><label htmlFor="pet-display">{t('小人显示屏')}</label></h2></section>
          <select
            id="pet-display"
            className="pet-display-select"
            value={desktop.separate_windows ? desktop.pet_display_id : 'together'}
            disabled={busy || Boolean(data.unavailable) || !desktop.appearance_ready || desktop.switching}
            onChange={(event) => run(act<AvatarSnapshot>('desktop_set_pet_display', {
              id: event.target.value === 'together' ? null : event.target.value,
            }).then(setDesktop))}
          >
            <option value="together">{t('小人与聊天同屏')}</option>
            {desktop.displays?.map(display => (
              <option key={display.id} value={display.id}>
                {t(display.primary ? '主屏' : '副屏')} · {display.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <section><h2>{t('摄像头互动')}</h2></section>
          <button className="button" disabled={busy} onClick={() => run(act('desktop_open_camera'))}>
            <Camera size={16} aria-hidden="true" />{t('打开摄像头窗口')}
          </button>
        </div>
        <div>
          <section>
            <h2>{t('桌面对话小窗')}</h2>
            <p>{t('显示或收起原来的桌宠对话窗口。')}</p>
          </section>
          <button
            className="button"
            disabled={busy || Boolean(data.unavailable)}
            onClick={() => run(act('desktop_toggle_bubble'))}
          >
            {t('显示 / 收起')}
          </button>
        </div>
        <div>
          <section>
            <h2>{t('麦克风与语音')}</h2>
            <p>
              {t('当前')}
              {runtimeLabel(runtime.voice)}
              {t('· 快捷键 ⌃⌥空格')}
            </p>
          </section>
          <button className="button" disabled={busy} onClick={() => run(act('toggle_voice'))}>
            {runtime.voice === 'off' ? t('开启麦克风') : t('关闭麦克风')}
          </button>
        </div>
        <div>
          <section>
            <h2>{t('屏幕总结')}</h2>
            <p>
              {runtime.context_enabled ? t('已开启') : t('已关闭')} · {runtime.context_status || t('前台窗口摘要')}
            </p>
          </section>
          <button className="button" disabled={busy} onClick={() => run(act('toggle_context'))}>
            {runtime.context_enabled ? t('关闭屏幕总结') : t('开启屏幕总结')}
          </button>
        </div>
        {runtime.context_text && <p className="context-summary">{runtime.context_text}</p>}
        <div>
          <section>
            <h2>{t('诊断日志目录')}</h2>
            <p>{t('打开原来的本机日志与诊断文件。')}</p>
          </section>
          <button
            className="button"
            disabled={busy || Boolean(data.unavailable)}
            onClick={() => run(act('desktop_open_logs'))}
          >
            {t('打开目录')}
          </button>
        </div>
        {data.unavailable && <p className="feature-status">{t('桌宠未连接，请先启动桌宠。Engine 控制仍可使用。')}</p>}
      </section>
      <section className="feature-card control-settings">
        <div>
          <section>
            <h2>{t('退出伙伴')}</h2>
            <p>{t('结束桌宠与后台 Engine。')}</p>
          </section>
          <button
            className="button danger"
            disabled={busy || Boolean(data.unavailable)}
            onClick={() => {
              if (confirm(t('退出 BoxAgent 桌宠和后台 Engine？'))) run(act('desktop_quit'));
            }}
          >
            {t('退出 BoxAgent')}
          </button>
        </div>
      </section>
    </>
  );
}
