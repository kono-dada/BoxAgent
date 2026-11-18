import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AudioLines, BrainCircuit, Check, ChevronDown, ChevronLeft, Cpu, Download, ExternalLink, Eye, Hand, HardDrive, MessageCircle, Mic, PackageOpen, ScanEye, ShieldCheck, X } from 'lucide-react';
import { t } from '../i18n';
import { preferences, setPreferences } from '../preferences';
import type { SetupBridge, StartupSnapshot, Step } from './types';

const steps: { id: Step; label: string }[] = [
  { id: 'welcome', label: '认识 BoxAgent' }, { id: 'providers', label: '模型服务' },
  { id: 'components', label: '本地组件' }, { id: 'permissions', label: '权限与体验' },
  { id: 'wechat', label: '接入微信' }, { id: 'ready', label: '开始使用' },
];
const names: Record<string, string> = { core: '基础运行环境', memory: '长期记忆', vision: '本地视觉', engine: '后台服务', qwen: '千问对话', task: 'DeepSeek 桌面任务', desktop: '桌面执行组件', app_server: 'Codex App Server', computer_use: 'Computer Use', embedding: '记忆模型', 'vision-model': '视觉模型', wechat: '微信接入', wechat_reading: '消息读取', wechat_listening: '消息监听', wechat_sending: '后台发送预检' };
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
  process_start_failed: '本地运行程序无法启动，请到“本地组件”修复运行环境。',
  component_runtime_relocated: '应用安装位置已变化，请修复本地运行环境，已下载的模型会复用。',
  wechat_runtime_unavailable: '微信准备需要的本地运行环境不可用，请先修复本地组件，再返回重试。',
  capabilities_not_ready: '还有核心能力未通过检查，请完成配置与组件准备。',
  development_install_required: '源码模式请先运行仓库安装脚本；独立安装包支持在这里准备组件。',
  cancelled: '已取消，本地配置已保留。', setup_request_failed: '操作未能完成，请重试。',
  desktop_tools_missing: '桌面执行组件尚未安装，文字聊天仍可使用。',
  desktop_tool_invalid: '所选目录不含完整的官方运行组件，请检查安装位置。',
  screen_permission_request_unavailable: '当前版本无法请求录屏权限，请更新 BoxAgent 后重试。',
  speech_permission_unavailable: '语音识别组件暂不可用，请更新应用后重试。',
  speech_permission_status_invalid: '语音识别状态未能确认，请重试。',
  task_configuration_missing: '请填写 DeepSeek API Key。',
  task_service_failed: 'DeepSeek 服务暂不可用，请稍后重试。',
  validation_required: '配置已保存，等待验证。',
  restart_required: '准备组件时会重新启动后台服务。',
  wechat_client_missing: '先准备一个供 BoxAgent 使用的微信副本。',
  wechat_keys_missing: '扫码登录后，获取并校验本机读取凭据。',
  wechat_unsupported_build: '需要微信 4.1.15（build 270115），其他构建尚未适配。',
  wechat_source_signature_invalid: '微信组件未通过来源签名校验，请更新 BoxAgent。',
  wechat_archive_unavailable: '安装包中的微信组件不可用，请修复或更新 BoxAgent。',
  wechat_download_manifest_invalid: '微信组件清单无效，请修复或更新 BoxAgent。',
  wechat_download_failed: '微信组件下载失败，请稍后重试。',
  wechat_download_invalid: '微信安装包校验失败，已拒绝使用。',
  wechat_close_replica_first: '请先完全退出 WeChat-BoxAgent，再点击“启动并扫码登录”。不会退出你的主微信。',
  wechat_debugger_missing: '需要 Apple 命令行工具，请先安装后重试。',
  wechat_debug_permission_missing: '系统暂未允许调试微信副本。请完成系统授权后重试。',
  wechat_replica_signature_invalid: '微信副本的签名权限不兼容，请重新准备副本以修复。',
  wechat_client_launch_failed: '微信副本未能启动，尚未进入扫码阶段。请重新准备副本并检查诊断结果。',
  wechat_client_exited: '微信副本在登录检查完成前退出，请重新启动后重试。',
  wechat_permission_denied: '系统未允许访问微信数据。请选择副本的数据文件夹授权后重试。',
  wechat_choose_account: '检测到多个账号，请选择需要接入的 db_storage 文件夹。',
  wechat_databases_missing: '尚未找到完整聊天数据库，请完成登录及同步后重试。',
  wechat_keys_invalid: '读取凭据与当前账号不匹配，请重新登录并获取凭据。',
  wechat_capture_timeout: '等待登录超时。请退出副本后重新开始，不会删除聊天数据。',
  wechat_capture_stopped: '登录检查未完成，已请求释放微信副本，请重试。',
  wechat_setup_failed: '微信检查未通过，请核对版本、系统授权与登录状态。',
  wechat_validation_failed: '微信读取或监听尚未通过校验，暂未启用。',
  wechat_native_check_required: '请保持副本主窗口打开，在后台运行发送预检。',
  wechat_open_main_window_or_clear_draft: '预检未通过，请打开副本主窗口，并确认文件传输助手没有未发送草稿。',
  wechat_dry_run_only: '后台目标定位与输入框检查通过，未发送测试消息。',
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
  const providerComplete = Boolean(workspace.trim() && (dashscope.trim() || state?.credentials.dashscope) && (deepseek.trim() || state?.credentials.deepseek));
  const nextAllowed = step === 'providers' ? ['qwen', 'task'].every(name => state?.capabilities[name]?.state === 'ready') && !providerDirty :
    step === 'components' ? ['core', 'memory', 'vision', 'engine'].every(key => state?.capabilities[key]?.state === 'ready') :
    step === 'wechat' ? !state?.credentials.wechatEnabled || ['reading', 'listening'].every(key => state?.wechat?.[key as 'reading' | 'listening'].state === 'ready') : true;
  // A resumed setup has saved keys but no provider check in this process yet.
  // The complete action validates them before it persists completion.
  const canCheckCompletion = Boolean(state?.credentials.dashscope && state.credentials.deepseek && state.credentials.workspace &&
    ['core', 'memory', 'vision', 'engine'].every(name => state.capabilities[name]?.state === 'ready') &&
    ['qwen', 'task'].every(name => !state.capabilities[name] || state.capabilities[name].code === 'validation_required'));
  const descriptions: Record<Step, string> = {
    welcome: '一个能聊天、记得你，也能帮忙的桌面伙伴。',
    providers: '使用你自己的模型服务，让对话和记忆开始运转。',
    components: '为你的伙伴准备本地记忆与视觉能力。',
    permissions: '按需要授权，让伙伴陪你交流、协作。',
    wechat: '在你授权后，帮你读消息、提醒和回复。',
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
        {state?.progress && <SetupProgress state={state} cancel={() => void action('cancel')} />}
        <div className="setup-feature-grid">
          <ComponentCard state={state} name="memory" />
          <ComponentCard state={state} name="vision" />
        </div>
        <p className="setup-storage-note"><HardDrive size={13} aria-hidden="true" /><span>{t('模型下载需要联网 · 建议保留 6 GB 空间')}</span></p>
        <details className="setup-details setup-component-details"><summary>{t('查看组件状态')}<ChevronDown size={12} aria-hidden="true" /></summary>
          <Status state={state} names={localComponents} />
          <p>{t('组件依赖已随安装包提供，模型下载需要联网。建议保留至少 6 GB 可用空间。')}</p>
          <button className="setup-link setup-centered-action" disabled={busy} onClick={() => void action('refresh')}>{t('重新检查')}</button>
        </details>
      </>}
      {step === 'permissions' && <>
        {state?.permissionRepair && <p className="setup-note" role="status">{t('当前版本需要重新核对系统权限。已有 Key、模型和微信配置已保留；请按需授权，或选择稍后处理。')}</p>}
        <div className="setup-permissions">{(['microphone', 'speech', 'accessibility', 'screen'] as const).map(permission => {
          const value = state?.permissions[permission], granted = value === true || value === 'granted';
          const labels = { microphone: '麦克风', speech: '语音识别', accessibility: '辅助功能', screen: '屏幕录制' };
          const descriptions = { microphone: '用于语音交流。', speech: '用于本机识别唤醒词。', accessibility: '用于读取和操作应用控件。', screen: '用于桌面截图和视觉理解。' };
          const Icon = { microphone: Mic, speech: AudioLines, accessibility: Hand, screen: Eye }[permission];
          return <section className="setup-permission" key={permission}><Icon size={19} aria-hidden="true" /><div><h3>{t(labels[permission])}</h3><p>{t(descriptions[permission])}</p></div>
            <button className={granted ? 'is-granted' : ''} disabled={busy} onClick={() => void action('permission', { permission })}>{granted && <Check size={12} aria-hidden="true" />}{t(granted ? '已授权' : '前往授权')}</button></section>;
        })}</div>
        <p className="setup-note setup-centered">{t('语音权限可稍后开启，文字聊天不受影响。')}</p>
        <details className="setup-details setup-desktop-details"><summary><Cpu size={14} aria-hidden="true" /><span>{t(state?.capabilities.desktop?.state === 'ready' ? '桌面操作已就绪' : '桌面操作待准备')}</span>
          {state?.capabilities.desktop?.state === 'ready' && <Check size={12} className="is-granted" aria-hidden="true" />}<ChevronDown size={12} aria-hidden="true" /></summary>
          <p>{t('自动复用本机的 App Server 与 Computer Use。任务模型仍使用你配置的 DeepSeek API。')}</p>
          <Status state={state} names={['app_server', 'computer_use']} />
          <details className="setup-details setup-tools-repair"><summary>{t('故障排查与手动修复')}<ChevronDown size={12} aria-hidden="true" /></summary>
          <div className="setup-actions"><button disabled={busy} onClick={() => void action('official-tools')}>{t('查看官方组件')}</button>
            <button disabled={busy} onClick={() => void action('choose-tool', { kind: 'codex' })}>{t('选择 Codex 目录')}</button>
            <button disabled={busy} onClick={() => void action('choose-tool', { kind: 'computer-use' })}>{t('选择 Computer Use 应用')}</button></div>
          <p>{t('仅在自动检测失败时手动选择。模型 Key 不会安装本地组件；缺少组件时仍可文字聊天。')}</p>
          <button className="setup-link" disabled={busy} onClick={() => void action('refresh')}>{t('重新检查')}</button>
          </details>
          <p>{t('屏幕录制授权后可能需要重启 BoxAgent。Computer Use 首次使用时可能另需系统授权。')}</p>
        </details>
        <button className="setup-link setup-centered-action" disabled={busy} onClick={() => void action('refresh-permissions')}>{t('刷新权限状态')}</button>
        <button className="setup-link setup-centered-action" disabled={busy} onClick={() => void action('restart-for-permissions')}>{t('重启并检查权限')}</button>
      </>}
      {step === 'wechat' && <>
        <p className="setup-note">{t('BoxAgent 使用独立的微信副本，避免改动你原来的微信安装和聊天数据。请在副本中扫码登录，让 BoxAgent 能读取消息并代你操作。')}</p>
        <p className="setup-note">{t('副本隔离的是安装和本地数据。同账号登录副本可能让主微信下线。')}</p>
        <ol className="setup-wechat-steps">
          <li><details className="setup-wechat-stage" open={state?.wechat?.client.state !== 'ready'}><summary>{t('准备微信副本')}<ChevronDown size={12} aria-hidden="true" /></summary><p>{t('BoxAgent 已为你提供验证过的微信版本，点击后会自动创建独立副本，无需自行下载。')}</p>
            <div className="setup-actions"><button disabled={busy || !state?.wechat?.downloadAvailable} onClick={() => void action('wechat-provision')}>{t('准备微信副本')}</button></div>
            {state?.wechat && !state.wechat.downloadAvailable && <p>{t('安装包缺少微信组件，请修复或更新 BoxAgent。')}</p>}
            <details className="setup-details"><summary>{t('版本要求')}<ChevronDown size={12} aria-hidden="true" /></summary><p>{t('适配版本：4.1.15（build 270115）。原来的微信保持不变。')}</p></details>
          </details></li>
          <li><details className="setup-wechat-stage" open={state?.wechat?.client.state === 'ready' && !state?.credentials.wechatConfigured}><summary>{t('启动并扫码登录')}<ChevronDown size={12} aria-hidden="true" /></summary><p>{t('先完全退出副本，再从这里启动。用手机在微信原生窗口中扫码；本机读取凭据会在登录时获取并加密保存。')}</p>
            <div className="setup-actions"><button disabled={busy || state?.wechat?.client.state !== 'ready'} onClick={() => void action('wechat-capture')}>{t('启动并扫码登录')}</button>
              <button className="setup-link" disabled={busy} onClick={() => void action('wechat-choose-account')}>{t('选择账号数据文件夹')}</button></div>
            <details className="setup-details"><summary>{t('需要安装工具或重新授权？')}<ChevronDown size={12} aria-hidden="true" /></summary>
              <p>{t('首次登录检查和后台发送需要 Apple 命令行工具及系统调试授权。读取数据库还可能需要文件访问授权；辅助功能授权不能代替这些权限。')}</p>
              <button disabled={busy} onClick={() => void action('wechat-developer-tools')}>{t('安装 Apple 命令行工具')}</button></details>
          </details></li>
          <li><details className="setup-wechat-stage" open={Boolean(state?.credentials.wechatConfigured)}><summary>{t('检查并启用')}<ChevronDown size={12} aria-hidden="true" /></summary>
            <div className="setup-actions"><button disabled={busy || !state?.credentials.wechatConfigured} onClick={() => void action('wechat-check')}>{t('检查读取与监听')}</button>
              <button disabled={busy || !state?.credentials.wechatConfigured} onClick={() => void action('wechat-native')}>{t('后台发送预检')}</button></div>
            <p>{t('发送预检只定位文件传输助手和检查空草稿，不会写入或发送消息。副本主窗口需要保持打开。')}</p>
          </details></li>
        </ol>
        {state?.wechat && <Status state={{ ...state, capabilities: {
          wechat_reading: state.wechat.reading, wechat_listening: state.wechat.listening, wechat_sending: state.wechat.sending,
        } }} names={['wechat_reading', 'wechat_listening', 'wechat_sending']} />}
        <p className="setup-security"><ShieldCheck size={15} aria-hidden="true" /><span>{t('读取凭据只加密保存在本机。启用提醒后，相关消息和会话上下文会交给你配置的模型处理；不会自动回复联系人。')}</span></p>
        <button className="setup-link setup-centered-action" disabled={busy || (!state?.credentials.wechatEnabled && !state?.credentials.wechatConfigured)}
          onClick={() => void action(state?.credentials.wechatEnabled ? 'wechat-disable' : 'wechat-enable')}>
          {t(state?.credentials.wechatEnabled ? '停用微信' : '启用微信')}</button>
      </>}
      {step === 'ready' && <>
        <details className="setup-details setup-component-details"><summary>{t('查看能力状态')}<ChevronDown size={12} aria-hidden="true" /></summary>
          <Status state={state} names={['qwen', 'task', 'core', 'engine', 'memory', 'vision']} />
          <h3 className="setup-optional-title">{t('桌面执行组件')}</h3><Status state={state} names={['desktop']} />
          <button className="setup-link setup-centered-action" disabled={busy} onClick={() => void action('refresh')}>{t('重新检查')}</button>
        </details>
      </>}
      {step !== 'components' && state?.progress && <SetupProgress state={state} cancel={() => void action('cancel')} />}
      {(error || state?.error) && <div className="setup-error" role="alert">{message(error || state?.error || '')}
        {['process_start_failed', 'component_runtime_relocated', 'wechat_runtime_unavailable'].includes(error || state?.error || '') &&
          <button className="setup-centered-action" disabled={busy} onClick={() => void action('step', { step: 'components' })}>{t('修复本地组件')}</button>}
      </div>}
      </div>
      </div>
      <footer className="setup-footer">
        <div className="setup-footer-start">{index > 0 && step !== 'ready' && !state?.permissionRepair && <button className="setup-link" disabled={busy} onClick={() => void action('step', { step: steps[index - 1].id })}><ChevronLeft size={14} aria-hidden="true" />{t('上一步')}</button>}</div>
        {step === 'permissions' && state?.permissionRepair ? <button className="setup-primary" disabled={busy} onClick={() => void action('finish-permission-review')}>{t(Object.values(state.permissions).every(value => value === true || value === 'granted') ? '继续使用' : '稍后授权并继续')}</button> :
          step === 'providers' && !nextAllowed ? <button className="setup-primary setup-provider-submit" type="submit" form="setup-provider-form" disabled={busy || !state || !providerComplete}>{t(busy ? '正在验证…' : '保存并验证')}</button> :
          step === 'components' && !nextAllowed ? <button className="setup-primary" disabled={busy || !state} onClick={() => void action('install')}>{t(busy ? '正在准备…' : '准备或修复组件')}</button> :
          index < steps.length - 1 ? <button className="setup-primary" disabled={busy || !state || !nextAllowed} onClick={() => void action('step', { step: steps[index + 1].id })}>{t(step === 'wechat' && !state?.credentials.wechatEnabled ? '暂不接入微信' : '下一步')}</button> :
          <button className="setup-primary" disabled={busy || !(state?.canComplete || canCheckCompletion)} onClick={() => void action('complete')}>{t(busy ? '正在检查…' : state?.onboarding.completed ? '完成修复' : canCheckCompletion ? '检查并开始' : '开始使用')}</button>}
        <div className="setup-footer-end">{step !== 'ready' && <button className="setup-link" disabled={busy} onClick={() => void action('open-logs')}>{t('诊断日志')}</button>}</div>
      </footer>
    </main>
  </div>;
}
function SetupProgress({ state, cancel }: { state: StartupSnapshot; cancel: () => void }) {
  const progress = state.progress;
  if (!progress) return null;
  const component = t(names[progress.component] || '本地组件');
  const Icon = ['vision', 'vision-model'].includes(progress.component) ? ScanEye : ['memory', 'jev', 'embedding'].includes(progress.component) ? BrainCircuit :
    progress.component === 'wechat' ? MessageCircle : progress.component === 'core' ? Cpu : PackageOpen;
  const downloading = progress.phase === 'downloading' || progress.bytes !== undefined;
  const percent = progress.bytes !== undefined && progress.total && progress.total > 0 ?
    Math.max(0, Math.min(100, Math.floor(progress.bytes / progress.total * 100))) : null;
  const phase = progress.phase === 'login' ? '请在微信副本窗口扫码登录…' : downloading ? '正在下载' : progress.phase === 'checking' ? '正在检查' : '正在准备';
  return <div className="setup-progress" role="status">
    <div className="setup-progress-heading">
      <span className="setup-progress-icon"><Icon size={21} strokeWidth={1.7} aria-hidden="true" /></span>
      <div className="setup-progress-title"><strong>{component}</strong><span>{t(phase)}</span></div>
      {percent !== null && <span className="setup-progress-percent">{percent}<small>%</small></span>}
    </div>
    <progress aria-label={component} max={progress.total || 1} value={progress.bytes !== undefined && progress.total ? Math.min(progress.bytes, progress.total) : undefined} />
    <div className="setup-progress-footer">
      <span className="setup-progress-bytes">{progress.bytes !== undefined ?
        `${Math.round(progress.bytes / 1024 ** 2)} MB${progress.total ? ` / ${Math.round(progress.total / 1024 ** 2)} MB` : ''}` : t('完成后会自动继续')}</span>
      <button type="button" className="setup-cancel" onClick={cancel}><X size={13} aria-hidden="true" />{t(downloading ? '取消下载' : '取消操作')}</button>
    </div>
  </div>;
}
function ComponentCard({ state, name }: { state: StartupSnapshot | null; name: 'memory' | 'vision' }) {
  const capability = state?.capabilities[name];
  const active = Boolean(state?.progress && (name === 'memory' ? ['memory', 'jev', 'embedding'] : ['vision', 'vision-model']).includes(state.progress.component));
  const ready = !active && capability?.state === 'ready';
  const Icon = name === 'memory' ? BrainCircuit : ScanEye;
  return <section className={`setup-feature-card${active ? ' is-active' : ready ? ' is-ready' : ''}`}>
    <div className="setup-feature-top"><Icon size={21} strokeWidth={1.7} aria-hidden="true" />
      <span className="setup-feature-status">{ready ? <Check size={12} aria-hidden="true" /> : active ? <Download size={12} aria-hidden="true" /> : null}
        {t(active ? '正在准备' : ready ? '已就绪' : capability?.state === 'unavailable' ? '需检查' : '待准备')}</span></div>
    <h3>{t(name === 'memory' ? '长期记忆' : '本地视觉')}</h3>
    <p>{t(name === 'memory' ? '记住重要的人和事' : '理解屏幕与画面')}</p>
  </section>;
}
function Status({ state, names: list }: { state: StartupSnapshot | null; names: string[] }) {
  return <ul className="setup-status">{list.map(name => {
    const capability = state?.capabilities[name], ready = capability?.state === 'ready';
    return <li key={name}><div><strong>{t(names[name])}</strong>{capability?.code && <small>{message(capability.code)}</small>}</div>
      <span className={ready ? 'is-ready' : ''}>{ready && <Check size={12} aria-hidden="true" />}{t(ready ? '已就绪' : capability?.state === 'checking' ? '待验证' : '待准备')}</span></li>;
  })}</ul>;
}
