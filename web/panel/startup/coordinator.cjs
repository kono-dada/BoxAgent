const path = require('node:path');
const fs = require('node:fs/promises');
const { STEPS, defaults, readState, atomicWrite } = require('./store.cjs');
const { validateProviders } = require('./providers.cjs');
const { PackagedRuntime } = require('./runtime.cjs');
const { detectDesktopTools } = require('./desktop-tools.cjs');

function canComplete(snapshot) {
  return snapshot.credentials?.dashscope === true && snapshot.credentials.deepseek === true &&
    Boolean(snapshot.credentials.workspace) && snapshot.credentials.provider === 'deepseek' &&
    ['core', 'memory', 'vision', 'engine', 'qwen', 'task'].every(name => snapshot.capabilities[name]?.state === 'ready');
}
class StartupCoordinator {
  constructor({ paths, credentials, permissions, ensureEngine, stopEngine, enterDesktop, broadcast, validate = validateProviders, runtime }) {
    Object.assign(this, { paths, credentials, permissions, ensureEngine, stopEngine, enterDesktop, broadcast, validate });
    this.stateFile = path.join(paths.data, 'state/onboarding.json');
    this.state = defaults(); this.capabilities = {}; this.busy = false; this.error = ''; this.progress = null;
    this.mode = 'checking'; this.abort = new AbortController(); this.closed = false;
    this.runtime = runtime || new PackagedRuntime(paths, { progress: value => { this.progress = value; this.emit(); } });
    this.summary = { dashscope: false, deepseek: false, workspace: '', provider: 'deepseek' };
  }
  snapshot() {
    return { mode: this.mode, onboarding: this.state, credentials: this.summary, capabilities: this.capabilities,
      permissions: this.permissions.snapshot(), busy: this.busy, error: this.error, progress: this.progress,
      canComplete: canComplete({ capabilities: this.capabilities, credentials: this.summary }), packaged: this.paths.packaged };
  }
  emit() { if (!this.closed) this.broadcast(this.snapshot()); }
  async operation(action) {
    if (this.closed) throw Error('cancelled');
    if (this.busy) throw Error('setup_busy');
    this.busy = true; this.error = ''; this.abort = new AbortController(); this.emit();
    try { await action(this.abort.signal); }
    catch (error) { this.error = this.abort.signal.aborted ? 'cancelled' : error.message; }
    finally { this.busy = false; this.progress = null; this.emit(); }
    return this.snapshot();
  }
  async start() {
    return this.operation(async signal => {
      this.state = await readState(this.stateFile);
      this.summary = await this.credentials.summary();
      this.mode = this.state.completed ? 'repair' : 'needs_setup';
      const configured = this.summary.dashscope && this.summary.deepseek && this.summary.workspace && this.summary.provider === 'deepseek';
      if (this.state.completed && !configured) this.state = { ...this.state, step: 'providers' };
      // Returning users already passed the complete gate. Start their companion
      // after core/Engine health; model initialization is a setup/repair check.
      await this.refreshLocal(signal, this.state.completed ? ['core'] : undefined);
      // No API calls or permission requests until the user chooses to validate.
      if (this.state.completed && configured && this.capabilities.engine?.state === 'ready') {
        this.mode = 'degraded'; await this.enterDesktop();
      }
    });
  }
  async refreshLocal(signal, components) {
    const config = await this.credentials.load();
    this.capabilities = { ...this.capabilities, ...await this.runtime.localStatus(config, signal, components) };
    if (signal.aborted) return;
    if (this.capabilities.core?.state === 'ready') {
      try { await this.ensureEngine(this.runtime.environment(config)); this.capabilities.engine = { state: 'ready', code: '' }; }
      catch { this.capabilities.engine = { state: 'unavailable', code: 'engine_start_failed' }; }
    } else this.capabilities.engine = { state: 'missing', code: 'core_missing' };
    if (!config.DASHSCOPE_API_KEY || !config.BOXAGENT_DASHSCOPE_WORKSPACE_ID)
      this.capabilities.qwen = { state: 'missing', code: 'qwen_configuration_missing' };
    this.summary = await this.credentials.summary();
    this.emit();
    const tools = await detectDesktopTools(this.paths.root, config);
    this.capabilities.desktop = { state: tools.codex && tools.client ? 'ready' : 'missing', code: tools.codex && tools.client ? '' : 'desktop_tools_missing' };
  }
  async request(method, params = {}) {
    if (method === 'snapshot') return this.snapshot();
    if (method === 'cancel') { this.abort.abort(); return this.snapshot(); }
    return this.operation(async signal => {
      if (method === 'step') {
        if (!STEPS.includes(params.step)) throw Error('invalid_step');
        this.state = { ...this.state, step: params.step }; await atomicWrite(this.stateFile, this.state);
      } else if (method === 'save') {
        await this.credentials.save(params);
        this.capabilities.qwen = { state: 'checking', code: 'validation_required' };
        this.capabilities.task = { state: 'checking', code: 'validation_required' };
        this.summary = await this.credentials.summary();
        await this.stopEngine();
        this.capabilities.engine = { state: 'missing', code: 'restart_required' };
      } else if (method === 'validate') {
        const config = await this.credentials.load();
        this.capabilities = { ...this.capabilities, ...await this.validate(config, { signal }) };
      } else if (method === 'install') {
        if (!this.paths.packaged) throw Error('development_install_required');
        await this.stopEngine();
        const config = await this.credentials.load();
        await this.runtime.install(config, signal);
        await this.refreshLocal(signal);
      } else if (method === 'refresh') {
        await this.refreshLocal(signal);
      } else if (method === 'permission') {
        await this.permissions.request(params.permission);
      } else if (method === 'complete') {
        await this.refreshLocal(signal);
        this.capabilities = { ...this.capabilities, ...await this.validate(await this.credentials.load(), { signal }) };
        if (signal.aborted || !canComplete({ capabilities: this.capabilities, credentials: this.summary })) throw Error('capabilities_not_ready');
        // Persist completion only after the desktop itself successfully opens.
        await this.enterDesktop();
        this.state = { ...this.state, completed: true, step: 'ready', completedAt: new Date().toISOString() };
        await atomicWrite(this.stateFile, this.state);
        this.mode = this.capabilities.task?.state === 'ready' && this.capabilities.desktop?.state === 'ready' ? 'ready' : 'degraded';
      } else if (method === 'repair') {
        this.mode = 'repair';
      } else if (method === 'open-logs') {
        await fs.mkdir(path.join(this.paths.data, 'logs'), { recursive: true });
      } else throw Error('invalid_setup_method');
    });
  }
  close() { this.closed = true; this.abort.abort(); }
}
module.exports = { StartupCoordinator, canComplete };
