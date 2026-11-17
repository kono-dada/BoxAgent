import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AudioLines, Check, ChevronDown, ChevronLeft, ExternalLink, Eye, Hand, Mic, ShieldCheck } from 'lucide-react';
import { t } from '../i18n';
import { preferences, setPreferences } from '../preferences';
import type { SetupBridge, StartupSnapshot, Step } from './types';

const steps: { id: Step; label: string }[] = [
  { id: 'welcome', label: '认识 BoxAgent' }, { id: 'providers', label: '模型服务' },
  { id: 'components', label: '本地组件' }, { id: 'permissions', label: '权限与体验' }, { id: 'ready', label: '开始使用' },
];
const names: Record<string, string> = { core: '基础运行环境', memory: '长期记忆', vision: '本地视觉', engine: '后台服务', qwen: '千问对话', task: 'DeepSeek 桌面任务', desktop: '桌面执行组件', embedding: '记忆模型', 'vision-model': '视觉模型' };
const errors: Record<string, string> = {
  qwen_configuration_missing: '请填写 DashScope Key 和该 Key 对应的业务空间 ID。',
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
  task_configuration_missing: '请填写 DeepSeek API Key。',
  task_service_failed: 'DeepSeek 服务暂不可用，请稍后重试。',
  validation_required: '配置已保存，等待验证。',
  restart_required: '准备组件时会重新启动后台服务。',
};
const message = (code: string) => t(errors[code] || '暂时未就绪，请检查配置或重试。');

export function OnboardingPage({ bridge }: { bridge: SetupBridge }) {
  const [state, setState] = useState<StartupSnapshot | null>(null);
  const [error, setError] = useState(''), [pending, setPending] = useState(false);
  const [dashscope, setDashscope] = useState(''), [deepseek, setDeepseek] = useState(''), [workspace, setWorkspace] = useState('');
  const [language, setLanguage] = useState(preferences().language);
  const content = useRef<HTMLDivElement>(null), stage = useRef<HTMLDivElement>(null);
  const previousIndex = useRef(0);
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
  useLayoutEffect(() => {
    content.current?.scrollTo(0, 0);
    content.current?.querySelector<HTMLHeadingElement>('h1')?.focus({ preventScroll: true });
    const direction = index >= previousIndex.current ? 1 : -1;
    previousIndex.current = index;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const animation = stage.current?.animate([
      { opacity: 0, transform: `translateX(${direction * 8}px)` },
      { opacity: 1, transform: 'translateX(0)' },
    ], { duration: 200, easing: 'cubic-bezier(.2,.8,.2,1)' });
    return () => animation?.cancel();
  }, [index]);
  useEffect(() => {
    const providerProblem = ['qwen', 'task'].some(name => ['invalid', 'unavailable'].includes(state?.capabilities[name]?.state || ''));
    const target = content.current?.querySelector('.setup-error, .setup-progress') ||
      (step === 'providers' && providerProblem ? content.current?.querySelector('.setup-status') : null);
    target?.scrollIntoView({ block: 'nearest' });
  }, [error, state?.error, state?.progress?.component, state?.capabilities.qwen?.state, state?.capabilities.task?.state]);
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
    if (saved && !saved.error) { setDashscope(''); setDeepseek(''); await action('validate'); }
  }
  const providerDirty = Boolean(dashscope.trim() || deepseek.trim() || workspace !== state?.credentials.workspace);
  const localComponents = ['core', 'memory', 'vision', 'engine'];
  const localReady = localComponents.filter(name => state?.capabilities[name]?.state === 'ready').length;
  const providerComplete = Boolean(workspace.trim() && (dashscope.trim() || state?.credentials.dashscope) && (deepseek.trim() || state?.credentials.deepseek));
  const nextAllowed = step === 'providers' ? ['qwen', 'task'].every(name => state?.capabilities[name]?.state === 'ready') && !providerDirty :
    step === 'components' ? ['core', 'memory', 'vision', 'engine'].every(key => state?.capabilities[key]?.state === 'ready') : true;
  const descriptions: Record<Step, string> = {
    welcome: '一个能聊天、记得你，也能帮忙的桌面伙伴。',
    providers: '使用你自己的模型服务，让对话和记忆开始运转。',
    components: '为你的伙伴准备本地记忆与视觉能力。',
    permissions: '按需要授权，让伙伴陪你交流、协作。',
    ready: '准备好和你的伙伴见面了吗？',
  };
  return <div className="setup-shell" data-language={language} data-step={step}>
    <header className="setup-titlebar">
      <nav className="setup-journey" aria-label={t('设置进度')}>
        {steps.map((item, i) => <button key={item.id} disabled={busy || (!state?.onboarding.completed && i > index)}
          title={t(item.label)} aria-label={t('第 {0} 步：{1}', i + 1, t(item.label))}
          aria-current={item.id === step ? 'step' : undefined} className={i < index ? 'is-visited' : ''}
          onClick={() => void action('step', { step: item.id })}><span aria-hidden="true" /></button>)}
      </nav>
      <div className="setup-language" aria-label={t('语言')}><button onClick={() => { setPreferences({ language: 'zh-CN' }); setLanguage('zh-CN'); }} aria-pressed={language === 'zh-CN'}>中文</button>
        <button onClick={() => { setPreferences({ language: 'en' }); setLanguage('en'); }} aria-pressed={language === 'en'}>English</button></div>
    </header>
    <main className="setup-main" ref={stage}>
      <div className="setup-content" ref={content}>
      <div className="setup-page">
      <div className="setup-intro">
        {step === 'welcome' && <div className="setup-avatar"><img src="brand/boxagent-icon.png" alt="" /></div>}
        {step === 'ready' && <div className={`setup-ready-mark${state?.canComplete ? ' is-complete' : ''}`}>{state?.canComplete ? <Check size={16} strokeWidth={1.8} aria-hidden="true" /> : <AudioLines size={16} aria-hidden="true" />}</div>}
        <h1 tabIndex={-1}>{t(step === 'ready' ? state?.canComplete ? '准备好了' : '还需准备' : steps[index]?.label || '认识 BoxAgent')}</h1>
        {step !== 'ready' && <p>{t(descriptions[step])}</p>}
      </div>
      {step === 'providers' && <>
        <form id="setup-provider-form" onSubmit={event => { event.preventDefault(); if (!busy && providerComplete) void save(); }}>
          <label>DashScope API Key <span>{t('必填')}</span><input type="password" autoComplete="off" spellCheck={false} value={dashscope} onChange={event => setDashscope(event.target.value)} placeholder={state?.credentials.dashscope ? t('已保存；留空保留') : 'sk-…'} disabled={busy} /></label>
          {state?.credentials.dashscope && <button type="button" className="setup-link setup-delete" disabled={busy} onClick={() => void action('save', { DASHSCOPE_API_KEY: '' })}>{t('删除已保存的 DashScope Key')}</button>}
          <label>{t('百炼业务空间 ID')} <span>{t('必填')}</span><input value={workspace} placeholder={t('输入 Key 对应的业务空间 ID')} autoComplete="off" spellCheck={false} disabled={busy} onChange={event => setWorkspace(event.target.value)} /></label>
          <label>DeepSeek API Key <span>{t('必填')}</span><input type="password" autoComplete="off" spellCheck={false} value={deepseek} onChange={event => setDeepseek(event.target.value)} placeholder={state?.credentials.deepseek ? t('已保存；留空保留') : 'sk-…'} disabled={busy} /></label>
          {state?.credentials.deepseek && <button type="button" className="setup-link setup-delete" disabled={busy} onClick={() => void action('save', { DEEPSEEK_API_KEY: '' })}>{t('删除已保存的 DeepSeek Key')}</button>}
        </form>
        <div className="setup-actions setup-provider-links"><button className="setup-link" disabled={busy} onClick={() => void action('provider-help', { kind: 'dashscope' })}>{t('打开百炼控制台')}<ExternalLink size={12} aria-hidden="true" /></button>
          <button className="setup-link" disabled={busy} onClick={() => void action('provider-help', { kind: 'deepseek' })}>{t('获取 DeepSeek Key')}<ExternalLink size={12} aria-hidden="true" /></button></div>
        <details className="setup-details"><summary>{t('如何获取 Key 与业务空间 ID？')}<ChevronDown size={12} aria-hidden="true" /></summary><p>{t('在阿里云百炼创建 API Key，复制该 Key 对应业务空间的 ID。请确认所需模型服务已开通。')}</p><p>{t('填写你自己的 Key，BoxAgent 不提供共享密钥。默认记忆服务复用千问配置。')}</p><p>{t('你需要自己的模型服务 Key。对话与记忆决策会调用云服务；本地视觉模型在电脑上运行。')}</p></details>
        <p className="setup-security"><ShieldCheck size={15} aria-hidden="true" /><span>{t('Key 加密保存在本机，由系统钥匙串保护。')}</span></p>
        {state?.credentials.dashscope && !providerDirty && <Status state={state} names={['qwen', 'task']} />}
      </>}
      {step === 'components' && <>
        {!state?.progress && <div className="setup-component-summary">
          <div className="setup-component-track" aria-label={t('本地组件准备进度')} role="progressbar" aria-valuemin={0} aria-valuemax={4} aria-valuenow={localReady}>
            {localComponents.map(name => <span key={name} className={state?.capabilities[name]?.state === 'ready' ? 'is-ready' : ''} />)}
          </div>
          <p>{t('{0} / {1} 项组件已就绪', localReady, localComponents.length)}</p>
        </div>}
        {state?.progress && <SetupProgress state={state} cancel={() => void action('cancel')} />}
        <p className="setup-note setup-centered">{t('组件依赖已随安装包提供，模型下载需要联网。建议保留至少 6 GB 可用空间。')}</p>
        <details className="setup-details setup-component-details"><summary>{t('查看组件状态')}<ChevronDown size={12} aria-hidden="true" /></summary><Status state={state} names={localComponents} /></details>
        <button className="setup-link setup-centered-action" disabled={busy} onClick={() => void action('refresh')}>{t('重新检查')}</button>
      </>}
      {step === 'permissions' && <>
        <div className="setup-permissions">{(['microphone', 'accessibility', 'screen'] as const).map(permission => {
          const value = state?.permissions[permission], granted = value === true || value === 'granted';
          const labels = { microphone: '麦克风', accessibility: '辅助功能', screen: '屏幕录制' };
          const descriptions = { microphone: '用于语音交流。', accessibility: '用于读取和操作应用控件。', screen: '用于桌面截图和视觉理解。' };
          const Icon = { microphone: Mic, accessibility: Hand, screen: Eye }[permission];
          return <section className="setup-permission" key={permission}><Icon size={19} aria-hidden="true" /><div><h3>{t(labels[permission])}</h3><p>{t(descriptions[permission])}</p></div>
            <button className={granted ? 'is-granted' : ''} disabled={busy} onClick={() => void action('permission', { permission })}>{granted && <Check size={12} aria-hidden="true" />}{t(granted ? '已授权' : '前往授权')}</button></section>;
        })}</div>
        <p className="setup-note setup-centered">{t('麦克风可稍后开启，文字聊天不受影响。')}</p>
        <details className="setup-details"><summary>{t('设置桌面执行组件')}<ChevronDown size={12} aria-hidden="true" /></summary>
          <Status state={state} names={['desktop']} />
          <div className="setup-actions"><button disabled={busy} onClick={() => void action('official-tools')}>{t('官方组件下载')}</button>
            <button disabled={busy} onClick={() => void action('choose-tool', { kind: 'codex' })}>{t('选择 Codex 目录')}</button>
            <button disabled={busy} onClick={() => void action('choose-tool', { kind: 'computer-use' })}>{t('选择 Computer Use 应用')}</button></div>
          <p>{t('桌面任务还需要官方 Codex 与 Computer Use 组件。权限未齐或组件缺失时，相关能力会受限。摄像头在首次使用时单独授权。')}</p>
        </details>
        <button className="setup-link setup-centered-action" disabled={busy} onClick={() => void action('snapshot')}>{t('刷新权限状态')}</button>
      </>}
      {step === 'ready' && <>
        <details className="setup-details setup-component-details"><summary>{t('查看能力状态')}<ChevronDown size={12} aria-hidden="true" /></summary>
          <Status state={state} names={['qwen', 'task', 'core', 'engine', 'memory', 'vision']} />
          <h3 className="setup-optional-title">{t('桌面执行组件')}</h3><Status state={state} names={['desktop']} />
          <button className="setup-link setup-centered-action" disabled={busy} onClick={() => void action('refresh')}>{t('重新检查')}</button>
        </details>
      </>}
      {step !== 'components' && state?.progress && <SetupProgress state={state} cancel={() => void action('cancel')} />}
      {(error || state?.error) && <div className="setup-error" role="alert">{message(error || state?.error || '')}</div>}
      </div>
      </div>
      <footer className="setup-footer">
        <div className="setup-footer-start">{index > 0 && step !== 'ready' && <button className="setup-link" disabled={busy} onClick={() => void action('step', { step: steps[index - 1].id })}><ChevronLeft size={14} aria-hidden="true" />{t('上一步')}</button>}</div>
        {step === 'providers' && !nextAllowed ? <button className="setup-primary setup-provider-submit" type="submit" form="setup-provider-form" disabled={busy || !state || !providerComplete}>{t(busy ? '正在验证…' : '保存并验证')}</button> :
          step === 'components' && !nextAllowed ? <button className="setup-primary" disabled={busy || !state} onClick={() => void action('install')}>{t(busy ? '正在准备…' : '准备或修复组件')}</button> :
          index < steps.length - 1 ? <button className="setup-primary" disabled={busy || !state || !nextAllowed} onClick={() => void action('step', { step: steps[index + 1].id })}>{t('下一步')}</button> :
          <button className="setup-primary" disabled={busy || !state?.canComplete} onClick={() => void action('complete')}>{t(busy ? '正在检查…' : state?.onboarding.completed ? '完成修复' : '开始使用')}</button>}
        <div className="setup-footer-end">{step !== 'ready' && <button className="setup-link" disabled={busy} onClick={() => void action('open-logs')}>{t('诊断日志')}</button>}</div>
      </footer>
    </main>
  </div>;
}
function SetupProgress({ state, cancel }: { state: StartupSnapshot; cancel: () => void }) {
  const progress = state.progress;
  if (!progress) return null;
  const component = t(names[progress.component] || '本地组件');
  return <div className="setup-progress" role="status">
    <div className="setup-progress-heading"><AudioLines size={17} aria-hidden="true" /><strong>{component}</strong></div>
    <progress aria-label={component} max={progress.total || 1} value={progress.bytes !== undefined && progress.total ? Math.min(progress.bytes, progress.total) : undefined} />
    <p>{t(progress.phase === 'downloading' || progress.bytes !== undefined ? '正在下载…' : progress.phase === 'checking' ? '正在检查…' : '正在安装…')}
      {progress.bytes !== undefined && ` ${Math.round(progress.bytes / 1024 ** 2)} MB${progress.total ? ` / ${Math.round(progress.total / 1024 ** 2)} MB` : ''}`}</p>
    <button className="setup-link" onClick={cancel}>{t('取消当前操作')}</button>
  </div>;
}
function Status({ state, names: list }: { state: StartupSnapshot | null; names: string[] }) {
  return <ul className="setup-status">{list.map(name => {
    const capability = state?.capabilities[name], ready = capability?.state === 'ready';
    return <li key={name}><div><strong>{t(names[name])}</strong>{capability?.code && <small>{message(capability.code)}</small>}</div>
      <span className={ready ? 'is-ready' : ''}>{ready && <Check size={12} aria-hidden="true" />}{t(ready ? '已就绪' : capability?.state === 'checking' ? '待验证' : '待准备')}</span></li>;
  })}</ul>;
}
