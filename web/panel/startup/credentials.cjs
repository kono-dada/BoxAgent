const { readJson, atomicWrite } = require('./store.cjs');
const KEYS = ['DASHSCOPE_API_KEY', 'DEEPSEEK_API_KEY', 'BOXAGENT_WECHAT_KEY_MATERIAL'];
const SETTINGS = ['BOXAGENT_DASHSCOPE_WORKSPACE_ID', 'BOXAGENT_TASK_PROVIDER', 'BOXAGENT_CODEX_BIN', 'BOXAGENT_COMPUTER_USE_CLIENT',
  'BOXAGENT_WECHAT_ENABLED', 'BOXAGENT_WECHAT_NOTIFY_ALL', 'BOXAGENT_WECHAT_APP', 'BOXAGENT_WECHAT_DB_ROOT', 'BOXAGENT_WECHAT_LLDB'];

class CredentialStore {
  constructor(file, encryption) { this.file = file; this.encryption = encryption; this.queue = Promise.resolve(); this.cache = null; }
  async load() {
    const saved = await readJson(this.file, { version: 1, keys: {}, settings: {} });
    if (saved?.version !== 1 || !saved.keys || !saved.settings || Array.isArray(saved.keys) || Array.isArray(saved.settings) ||
        typeof saved.keys !== 'object' || typeof saved.settings !== 'object') throw Error('configuration_unreadable');
    const fingerprint = JSON.stringify(saved);
    if (this.cache?.fingerprint === fingerprint) return { ...this.cache.env };
    this.cache = null;
    const env = {};
    if (Object.keys(saved.keys).length && !this.encryption.isEncryptionAvailable()) throw Error('credential_store_locked');
    for (const key of KEYS) {
      if (saved.keys[key]) {
        if (typeof saved.keys[key] !== 'string') throw Error('configuration_unreadable');
        try { env[key] = this.encryption.decryptString(Buffer.from(saved.keys[key], 'base64')); }
        catch { throw Error('credential_store_locked'); }
      }
    }
    for (const key of SETTINGS) if (typeof saved.settings[key] === 'string') env[key] = saved.settings[key];
    this.cache = { fingerprint, env };
    return { ...env };
  }
  save(patch) {
    const operation = this.queue.then(async () => {
      if (!patch || typeof patch !== 'object' || Array.isArray(patch) ||
          Object.keys(patch).some(key => ![...KEYS, ...SETTINGS].includes(key))) throw Error('invalid_configuration');
      if (!this.encryption.isEncryptionAvailable()) throw Error('credential_store_locked');
      const current = await this.load();
      for (const [key, value] of Object.entries(patch)) {
        if (typeof value !== 'string' || value.length > (key === 'BOXAGENT_WECHAT_KEY_MATERIAL' ? 512 * 1024 : 4096) || /[\r\n\0]/.test(value)) throw Error('invalid_configuration');
        current[key] = value.trim();
      }
      const workspace = current.BOXAGENT_DASHSCOPE_WORKSPACE_ID;
      if (workspace && !/^[a-zA-Z0-9-]{1,128}$/.test(workspace)) throw Error('invalid_workspace');
      if (current.BOXAGENT_TASK_PROVIDER && !['deepseek', 'codex'].includes(current.BOXAGENT_TASK_PROVIDER)) throw Error('invalid_configuration');
      if (current.BOXAGENT_WECHAT_ENABLED && !['0', '1'].includes(current.BOXAGENT_WECHAT_ENABLED)) throw Error('invalid_configuration');
      if (current.BOXAGENT_WECHAT_NOTIFY_ALL && !['0', '1'].includes(current.BOXAGENT_WECHAT_NOTIFY_ALL)) throw Error('invalid_configuration');
      const keys = {}, settings = {};
      for (const key of KEYS) if (current[key]) keys[key] = this.encryption.encryptString(current[key]).toString('base64');
      for (const key of SETTINGS) if (current[key]) settings[key] = current[key];
      await atomicWrite(this.file, { version: 1, keys, settings });
    });
    this.queue = operation.catch(() => {});
    return operation;
  }
  async summary() {
    const env = await this.load();
    return { dashscope: Boolean(env.DASHSCOPE_API_KEY), deepseek: Boolean(env.DEEPSEEK_API_KEY),
      workspace: env.BOXAGENT_DASHSCOPE_WORKSPACE_ID || '', provider: env.BOXAGENT_TASK_PROVIDER || 'deepseek',
      wechatEnabled: env.BOXAGENT_WECHAT_ENABLED === '1', wechatConfigured: Boolean(env.BOXAGENT_WECHAT_KEY_MATERIAL && env.BOXAGENT_WECHAT_DB_ROOT) };
  }
}
module.exports = { CredentialStore, KEYS, SETTINGS };
