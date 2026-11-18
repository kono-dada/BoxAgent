const fs = require('node:fs/promises');
const path = require('node:path');
const { run } = require('./process.cjs');
const { readJson } = require('./store.cjs');
const { hashFile } = require('./download.cjs');

const missing = code => ({ state: 'missing', code });
function materialFrom(config) {
  let value;
  try { value = JSON.parse(config.BOXAGENT_WECHAT_KEY_MATERIAL || '{}'); } catch { throw Error('wechat_keys_invalid'); }
  if (!value.keys || Array.isArray(value.keys) || typeof value.keys !== 'object' ||
      !Object.keys(value.keys).length || Object.entries(value.keys).some(([name, key]) =>
        path.isAbsolute(name) || name.split('/').includes('..') || name.includes('\\') ||
        typeof key !== 'string' || !/^[a-f0-9]{64}$/i.test(key))) throw Error('wechat_keys_invalid');
  return value.keys;
}
function publicChecks(result) {
  const checks = {};
  for (const name of ['client', 'reading', 'listening', 'sending']) {
    const item = result[name];
    if (!item) continue;
    if (!['ready', 'missing', 'unavailable'].includes(item.state) || typeof item.code !== 'string' ||
        (item.code && !/^wechat_[a-z_]+$/.test(item.code))) throw Error('wechat_setup_failed');
    checks[name] = { state: item.state, code: item.code };
  }
  return checks;
}
function validateArchive(manifest) {
  if (manifest?.version !== 1 || manifest.clientVersion !== '4.1.15' || manifest.build !== '270115')
    throw Error('wechat_download_manifest_invalid');
  if (manifest.bundledPath !== undefined && manifest.bundledPath !== 'wechat/4.1.15.270115.dmg')
    throw Error('wechat_download_manifest_invalid');
  if (manifest.url === null && manifest.sha256 === null && manifest.size === null && !manifest.bundledPath) return false;
  const bundled = manifest.bundledPath === 'wechat/4.1.15.270115.dmg' && manifest.url === null;
  if ((!bundled && (typeof manifest.url !== 'string' || !manifest.url.startsWith('https://'))) ||
      !/^[a-f0-9]{64}$/.test(manifest.sha256) || !Number.isSafeInteger(manifest.size) ||
      manifest.size < 1024 || manifest.size > 1024 ** 3) throw Error('wechat_download_manifest_invalid');
  return true;
}

class WeChatSetup {
  constructor(paths, credentials, { execute = run, progress = () => {}, fetcher = fetch } = {}) {
    Object.assign(this, { paths, credentials, execute, progress, fetcher });
    this.app = path.join(paths.data, 'integrations/wechat/WeChat-4.1.15-BoxAgent.app');
    this.checks = { client: missing('wechat_client_missing'), reading: missing('wechat_keys_missing'),
      listening: missing('wechat_keys_missing'), sending: missing('wechat_native_check_required') };
    this.downloadAvailable = false;
  }
  snapshot() { return { ...this.checks, downloadAvailable: this.downloadAvailable }; }
  async manifest() {
    const bundled = this.paths.resources ? await readJson(path.join(this.paths.resources, 'wechat/manifest.json'), null) : null;
    const manifest = bundled || await readJson(path.join(this.paths.root, 'scripts/wechat-clients/4.1.15.270115.json'), null);
    this.downloadAvailable = validateArchive(manifest);
    return manifest;
  }
  async call(action, config, signal, extra = {}) {
    const request = { action, app: config.BOXAGENT_WECHAT_APP || this.app,
      root: config.BOXAGENT_WECHAT_DB_ROOT || '', lldb: config.BOXAGENT_WECHAT_LLDB || '/usr/bin/lldb', ...extra };
    if (['verify', 'native'].includes(action)) request.material = materialFrom(config);
    const inherited = { ...process.env };
    for (const key of Object.keys(inherited)) {
      if (key.startsWith('BOXAGENT_WECHAT_') || /(?:API_KEY|API_TOKEN|SECRET)$/.test(key) ||
          ['PYTHONHOME', 'PYTHONPATH'].includes(key)) delete inherited[key];
    }
    const output = await this.execute(this.paths.python, ['-m', 'boxagent.entrypoints.wechat_setup'],
      { cwd: this.paths.root, signal, timeout: action === 'capture' ? 360000 : 240000, killAfter: 10000,
        env: { ...inherited, BOXAGENT_PACKAGED: '1', BOXAGENT_DATA_DIR: this.paths.data, PYTHONUNBUFFERED: '1',
          PYTHONPYCACHEPREFIX: path.join(this.paths.runtime, 'pycache') }, input: JSON.stringify(request) });
    let response;
    try { response = JSON.parse(output); } catch { throw Error('wechat_setup_failed'); }
    if (!response.ok) throw Error(typeof response.code === 'string' && /^(wechat_[a-z_]+|cancelled)$/.test(response.code) ? response.code : 'wechat_setup_failed');
    if (!response.result || typeof response.result !== 'object') throw Error('wechat_setup_failed');
    this.checks = { ...this.checks, ...publicChecks(response.result) };
    return response.result;
  }
  async refresh(config, signal, { deep = false } = {}) {
    await this.manifest();
    if (!config.BOXAGENT_WECHAT_APP) return;
    try { await this.call(deep && config.BOXAGENT_WECHAT_KEY_MATERIAL ? 'verify' : 'client', config, signal); }
    catch (error) {
      const code = /^wechat_[a-z_]+$/.test(error.message) ? error.message : 'wechat_setup_failed';
      this.checks = Object.fromEntries(['client', 'reading', 'listening', 'sending'].map(name => [name, { state: 'unavailable', code }]));
      if (signal.aborted) throw error;
    }
  }
  async prepare(source, signal) {
    this.checks = { client: missing('wechat_client_missing'), reading: missing('wechat_keys_missing'),
      listening: missing('wechat_keys_missing'), sending: missing('wechat_native_check_required') };
    // The source can be an official app or a read-only mounted DMG. Source is never modified.
    const prepare = async app => this.call('prepare', { BOXAGENT_WECHAT_APP: this.app }, signal, { source: app });
    let result;
    if (source.endsWith('.dmg')) {
      const parent = path.join(this.paths.data, 'integrations/wechat');
      await fs.mkdir(parent, { recursive: true, mode: 0o700 });
      const mount = await fs.mkdtemp(path.join(parent, '.mount-'));
      try {
        await this.execute('/usr/bin/hdiutil', ['attach', '-readonly', '-nobrowse', '-noautoopen', '-mountpoint', mount, source], { signal });
        const apps = (await fs.readdir(mount)).filter(name => name.endsWith('.app'));
        if (apps.length !== 1) throw Error('wechat_client_missing');
        result = await prepare(path.join(mount, apps[0]));
      } finally {
        // Cleanup has no aborted signal. A failed detach never recursively deletes a mounted app.
        await this.execute('/usr/bin/hdiutil', ['detach', mount], { timeout: 30000 });
        await fs.rmdir(mount);
      }
    } else result = await prepare(source);
    if (result.app !== this.app) throw Error('wechat_setup_failed');
    signal.throwIfAborted();
    const current = await this.credentials.load();
    if (current.BOXAGENT_WECHAT_APP !== this.app) {
      await this.credentials.save({ BOXAGENT_WECHAT_APP: this.app, BOXAGENT_WECHAT_ENABLED: '0',
        BOXAGENT_WECHAT_KEY_MATERIAL: '', BOXAGENT_WECHAT_DB_ROOT: '' });
    }
  }
  async download(signal) {
    const manifest = await this.manifest();
    if (!this.downloadAvailable) throw Error('wechat_archive_unavailable');
    if (manifest.bundledPath) return this.provision(signal);
    const directory = path.join(this.paths.data, 'integrations/wechat/downloads');
    await fs.mkdir(directory, { recursive: true, mode: 0o700 });
    const file = path.join(directory, '4.1.15.270115.dmg'), partial = `${file}.part`;
    try { if ((await fs.stat(file)).size === manifest.size && await hashFile(file) === manifest.sha256) return this.prepare(file, signal); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    const response = await this.fetcher(manifest.url, { signal: AbortSignal.any([signal, AbortSignal.timeout(600000)]) });
    if (!response.ok || !response.body || (response.url && !response.url.startsWith('https://'))) throw Error('wechat_download_failed');
    const handle = await fs.open(partial, 'w', 0o600);
    let bytes = 0;
    try {
      for await (const chunk of response.body) {
        signal.throwIfAborted(); bytes += chunk.length;
        if (bytes > manifest.size) throw Error('wechat_download_invalid');
        await handle.write(chunk);
        this.progress({ component: 'wechat', phase: 'downloading', bytes, total: manifest.size });
      }
      await handle.sync();
    } catch (error) { await fs.rm(partial, { force: true }); throw error; }
    finally { await handle.close(); }
    if (bytes !== manifest.size || await hashFile(partial) !== manifest.sha256) {
      await fs.rm(partial, { force: true }); throw Error('wechat_download_invalid');
    }
    await fs.rename(partial, file);
    await this.prepare(file, signal);
  }
  async provision(signal) {
    const manifest = await this.manifest();
    if (!this.downloadAvailable) throw Error('wechat_archive_unavailable');
    if (!manifest.bundledPath) return this.download(signal);
    const file = path.join(this.paths.resources, manifest.bundledPath);
    const relative = path.relative(await fs.realpath(this.paths.resources), await fs.realpath(file));
    if ((await fs.lstat(file)).isSymbolicLink() || relative.startsWith('..') || path.isAbsolute(relative) ||
        (await fs.stat(file)).size !== manifest.size || await hashFile(file) !== manifest.sha256)
      throw Error('wechat_download_invalid');
    signal.throwIfAborted();
    this.progress({ component: 'wechat', phase: 'installing' });
    await this.prepare(file, signal);
  }
  async capture(signal) {
    const config = await this.credentials.load();
    this.progress({ component: 'wechat', phase: 'login' });
    const result = await this.call('capture', config, signal);
    if (typeof result.root !== 'string' || !path.isAbsolute(result.root)) throw Error('wechat_setup_failed');
    const encoded = JSON.stringify({ keys: result.material });
    materialFrom({ BOXAGENT_WECHAT_KEY_MATERIAL: encoded });
    // A cancelled capture cannot replace the account's previously valid material.
    signal.throwIfAborted();
    await this.credentials.save({ BOXAGENT_WECHAT_KEY_MATERIAL: encoded, BOXAGENT_WECHAT_DB_ROOT: result.root });
  }
  async verify(signal, native = false) {
    const config = await this.credentials.load();
    await this.call(native ? 'native' : 'verify', config, signal);
  }
}
module.exports = { WeChatSetup, materialFrom, validateArchive, publicChecks };
