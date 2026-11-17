import { useEffect, useState } from 'react';
import { t } from '../i18n';
import { preferences, setPreferences } from '../preferences';
import type { SetupBridge, StartupSnapshot, Step } from './types';

const steps: { id: Step; label: string }[] = [
  { id: 'welcome', label: '认识 BoxAgent' }, { id: 'providers', label: '模型服务' },
  { id: 'components', label: '本地组件' }, { id: 'permissions', label: '权限与体验' }, { id: 'ready', label: '开始使用' },
];
const names: Record<string, string> = { core: '基础运行环境', memory: '长期记忆', vision: '本地视觉', engine: '后台服务', qwen: '千问对话', task: '桌面任务模型', desktop: '桌面执行组件', embedding: '记忆模型', 'vision-model': '视觉模型' };
const errors: Record<string, string> = {
  qwen_configuration_missing: '请填写 DashScope Key 和北京地域的百炼 Workspace ID。',
  qwen_auth_failed: '千问鉴权失败，请检查 Key、Workspace ID 和服务开通状态。',
  deepseek_auth_failed: 'DeepSeek 鉴权失败，请检查 Key。',
  provider_network_failed: '暂时无法连接模型服务，配置已保留，请稍后重试。',
  qwen_service_failed: '千问服务暂不可用，请检查服务开通状态后重试。',
  qwen_response_invalid: '千问未返回有效的连接信息，请检查服务配置。',
  credential_store_locked: '系统密钥存储暂不可用，请解锁钥匙串后重试。',
  configuration_unreadable: '配置文件无法读取。请先备份数据，再从诊断日志定位问题。',
  invalid_configuration: '配置格式不正确，请检查输入。', invalid_workspace: 'Workspace ID 只能包含字母、数字和连字符。',
  packaged_inputs_invalid: '安装包资源不完整，请重新下载 BoxAgent。',
  disk_space_low: '磁盘空间不足，准备组件建议保留至少 6 GB。',
  model_hash_mismatch: '模型文件校验失败，请重新下载。',
  engine_start_failed: '后台服务未能启动。引导仍可使用，请重试或打开诊断日志。',
  component_process_failed: '组件准备或检查失败，请重试并查看诊断日志。',
  capabilities_not_ready: '还有核心能力未通过检查，请完成配置与组件准备。',
  development_install_required: '源码模式请先运行仓库安装脚本；独立安装包支持在这里准备组件。',
  cancelled: '已取消，本地配置已保留。', setup_request_failed: '操作未能完成，请重试。',
  desktop_tools_missing: '桌面执行组件尚未安装，文字聊天仍可使用。',
  desktop_tool_invalid: '所选目录不含完整的官方运行组件，请检查安装位置。',
};
const message = (code: string) => t(errors[code] || '暂时未就绪，请检查配置或重试。');

export function OnboardingPage({ bridge }: { bridge: SetupBridge }) {
  const [state, setState] = useState<StartupSnapshot | null>(null);
  const [error, setError] = useState(''), [pending, setPending] = useState(false);
  const [dashscope, setDashscope] = useState(''), [deepseek, setDeepseek] = useState(''), [workspace, setWorkspace] = useState('');
  const [language, setLanguage] = useState(preferences().language);
  const step = state?.onboarding.step || 'welcome', index = steps.findIndex(item => item.id === step);
  const busy = pending || Boolean(state?.busy);
  useEffect(() => {
    let live = true;
    const receive = (value: StartupSnapshot) => { if (live) setState(value); };
    const stop = bridge.subscribe(receive);
    void bridge.request('snapshot').then(receive).catch(() => setError('setup_request_failed'));
    return () => { live = false; stop(); };
  }, [bridge]);
  useEffect(() => { if (state) setWorkspace(state.credentials.workspace); }, [state?.credentials.workspace]);
  async function action(method: string, params?: Record<string, string>) {
    setPending(true); setError('');
    try { const next = await bridge.request(method, params); setState(next); return next; }
    catch { setError('setup_request_failed'); return null; }
    finally { setPending(false); }
  }
  async function save() {
    const patch: Record<string, string> = { BOXAGENT_DASHSCOPE_WORKSPACE_ID: workspace, BOXAGENT_TASK_PROVIDER: 'deepseek' };
    if (dashscope.trim()) patch.DASHSCOPE_API_KEY = dashscope;
    if (deepseek.trim()) patch.DEEPSEEK_API_KEY = deepseek;
    const saved = await action('save', patch);
    if (!saved?.error) { setDashscope(''); setDeepseek(''); await action('validate'); }
  }
  const nextAllowed = step === 'providers' ? state?.capabilities.qwen?.state === 'ready' :
    step === 'components' ? ['core', 'memory', 'vision', 'engine'].every(key => state?.capabilities[key]?.state === 'ready') : true;
  return <div className="setup-shell" data-language={language}>
    <aside className="setup-sidebar">
      <div className="setup-brand"><img src="brand/boxagent-128.png" alt="" /><strong>BoxAgent</strong></div>
      <p>{t(state?.onboarding.completed ? '配置与修复' : '让伙伴来到你的桌面')}</p>
      <ol>{steps.map((item, i) => <li key={item.id}><button disabled={busy || (!state?.onboarding.completed && i > index)}
        aria-current={item.id === step ? 'step' : undefined} onClick={() => void action('step', { step: item.id })}>
        <span>{i < index ? '✓' : i + 1}</span>{t(item.label)}</button></li>)}</ol>
      <div className="setup-language"><button onClick={() => { setPreferences({ language: 'zh-CN' }); setLanguage('zh-CN'); }} aria-pressed={language === 'zh-CN'}>中文</button>
        <button onClick={() => { setPreferences({ language: 'en' }); setLanguage('en'); }} aria-pressed={language === 'en'}>English</button></div>
    </aside>
    <main className="setup-main">
      <header><small>{t(state?.onboarding.completed ? '随时调整，继续相处' : '几步准备，开始相处')}</small><h1>{t(steps[index]?.label || '认识 BoxAgent')}</h1></header>
      <div className="setup-content">
      {step === 'welcome' && <>
        <div className="setup-welcome"><img src="brand/boxagent-icon.png" alt="BoxAgent" /><h2>{t('一个能聊天、记得你，也能帮忙的桌面伙伴。')}</h2></div>
        <div className="setup-features">{[['自然对话', '用文字或语音交流，耗时任务交给后台。'], ['长期记忆', '保留值得记住的偏好，让下次交流更连贯。'], ['桌面协作', '授权后操作应用，任务完成后回来告诉你。']].map(([title, body]) => <section key={title}><h3>{t(title)}</h3><p>{t(body)}</p></section>)}</div>
        <p className="setup-note">{t('你需要自己的模型服务 Key。对话与记忆决策会调用云服务；本地视觉模型在电脑上运行。')}</p>
      </>}
      {step === 'providers' && <>
        <p>{t('填写你自己的 Key，BoxAgent 不提供共享密钥。默认记忆服务复用千问配置。')}</p>
        <p className="setup-note">{t('在阿里云百炼选择北京地域，复制 API Key 与对应工作空间的 ID。请确认该工作空间已开通所需模型服务。')}</p>
        <div className="setup-actions"><button className="setup-link" disabled={busy} onClick={() => void action('provider-help', { kind: 'dashscope' })}>{t('打开百炼控制台')}</button>
          <button className="setup-link" disabled={busy} onClick={() => void action('provider-help', { kind: 'deepseek' })}>{t('获取 DeepSeek Key')}</button></div>
        <form onSubmit={event => { event.preventDefault(); void save(); }}>
          <label>DashScope API Key <span>{t('必填')}</span><input type="password" autoComplete="off" spellCheck={false} value={dashscope} onChange={event => setDashscope(event.target.value)} placeholder={state?.credentials.dashscope ? t('已保存；留空保留') : 'sk-…'} disabled={busy} /></label>
          {state?.credentials.dashscope && <button type="button" className="setup-link" disabled={busy} onClick={() => void action('save', { DASHSCOPE_API_KEY: '' })}>{t('删除已保存的 DashScope Key')}</button>}
          <label>{t('百炼 Workspace ID')} <span>{t('北京地域')}</span><input value={workspace} autoComplete="off" spellCheck={false} disabled={busy} onChange={event => setWorkspace(event.target.value)} /></label>
          <label>DeepSeek API Key <span>{t('桌面任务使用，可稍后配置')}</span><input type="password" autoComplete="off" spellCheck={false} value={deepseek} onChange={event => setDeepseek(event.target.value)} placeholder={state?.credentials.deepseek ? t('已保存；留空保留') : 'sk-…'} disabled={busy} /></label>
          {state?.credentials.deepseek && <button type="button" className="setup-link" disabled={busy} onClick={() => void action('save', { DEEPSEEK_API_KEY: '' })}>{t('删除已保存的 DeepSeek Key')}</button>}
          <button className="setup-primary" type="submit" disabled={busy || !workspace.trim() || (!dashscope.trim() && !state?.credentials.dashscope)}>{t('保存并验证')}</button>
          <p className="setup-note">{t('Key 经系统钥匙串保护后加密保存，不写入日志、会话或安装包。修改配置会重启后台服务。')}</p>
        </form>
        <Status state={state} names={['qwen', 'task']} />
      </>}
      {step === 'components' && <>
        <p>{t('准备独立的运行环境、记忆索引和默认视觉模型。组件依赖随安装包提供，模型准备需要联网。')}</p>
        <Status state={state} names={['core', 'memory', 'vision', 'engine']} />
        <p className="setup-note">{t('建议至少保留 6 GB 可用空间。首次必须完成记忆与视觉组件；日常启动会复用已准备的环境。')}</p>
        <div className="setup-actions"><button className="setup-primary" disabled={busy} onClick={() => void action('install')}>{t('准备或修复组件')}</button>
          <button disabled={busy} onClick={() => void action('refresh')}>{t('重新检查')}</button></div>
      </>}
      {step === 'permissions' && <>
        <p>{t('按需要授权。麦克风可以稍后开启，未授权时仍可使用文字。')}</p>
        {(['microphone', 'accessibility', 'screen'] as const).map(permission => {
          const value = state?.permissions[permission], granted = value === true || value === 'granted';
          const labels = { microphone: '麦克风', accessibility: '辅助功能', screen: '屏幕录制' };
          const descriptions = { microphone: '用于语音交流。', accessibility: '用于读取和操作应用控件。', screen: '用于桌面截图和视觉理解。' };
          return <section className="setup-permission" key={permission}><div><h3>{t(labels[permission])}</h3><p>{t(descriptions[permission])}</p></div>
            <button disabled={busy} onClick={() => void action('permission', { permission })}>{t(granted ? '已授权' : '前往授权')}</button></section>;
        })}
        <p className="setup-note">{t('桌面任务还需要官方 Codex 与 Computer Use 组件。权限未齐或组件缺失时，相关能力会受限。摄像头在首次使用时单独授权。')}</p>
        <Status state={state} names={['desktop']} />
        <div className="setup-actions"><button disabled={busy} onClick={() => void action('official-tools')}>{t('官方组件下载')}</button>
          <button disabled={busy} onClick={() => void action('choose-tool', { kind: 'codex' })}>{t('选择 Codex 目录')}</button>
          <button disabled={busy} onClick={() => void action('choose-tool', { kind: 'computer-use' })}>{t('选择 Computer Use 应用')}</button></div>
        <button disabled={busy} onClick={() => void action('snapshot')}>{t('刷新权限状态')}</button>
      </>}
      {step === 'ready' && <>
        <h2>{t('准备好和你的伙伴见面了吗？')}</h2><Status state={state} names={['qwen', 'core', 'engine', 'memory', 'vision', 'task', 'desktop']} />
        <p className="setup-note">{t('开始使用前会重新检查核心能力。语音和桌面任务按实际授权与组件状态启用。')}</p>
        <button disabled={busy} onClick={() => void action('refresh')}>{t('重新检查')}</button>
      </>}
      {state?.progress && <div className="setup-progress" role="status"><strong>{t(names[state.progress.component] || '本地组件')}</strong>
        <p>{t(state.progress.phase === 'downloading' || state.progress.bytes !== undefined ? '正在下载…' : state.progress.phase === 'checking' ? '正在检查…' : '正在安装…')}
          {state.progress.bytes !== undefined && ` ${Math.round(state.progress.bytes / 1024 ** 2)} MB${state.progress.total ? ` / ${Math.round(state.progress.total / 1024 ** 2)} MB` : ''}`}</p>
        <button onClick={() => void action('cancel')}>{t('取消当前操作')}</button></div>}
      {(error || state?.error) && <div className="setup-error" role="alert">{message(error || state?.error || '')}</div>}
      </div>
      <footer><button className="setup-link" disabled={busy} onClick={() => void action('open-logs')}>{t('诊断日志')}</button><div>
        {index > 0 && <button disabled={busy} onClick={() => void action('step', { step: steps[index - 1].id })}>{t('上一步')}</button>}
        {index < steps.length - 1 ? <button className="setup-primary" disabled={busy || !state || !nextAllowed} onClick={() => void action('step', { step: steps[index + 1].id })}>{t('下一步')}</button> :
          <button className="setup-primary" disabled={busy || !state?.canComplete} onClick={() => void action('complete')}>{t(state?.onboarding.completed ? '完成修复' : '开始使用')}</button>}
      </div></footer>
    </main>
  </div>;
}
function Status({ state, names: list }: { state: StartupSnapshot | null; names: string[] }) {
  return <ul className="setup-status">{list.map(name => {
    const capability = state?.capabilities[name], ready = capability?.state === 'ready';
    return <li key={name}><div><strong>{t(names[name])}</strong>{capability?.code && <small>{message(capability.code)}</small>}</div>
      <span className={ready ? 'is-ready' : ''}>{t(ready ? '已就绪' : capability?.state === 'checking' ? '待验证' : '待准备')}</span></li>;
  })}</ul>;
}
