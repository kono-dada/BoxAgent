const fs = require('node:fs/promises');
const path = require('node:path');
const { run } = require('./process.cjs');
const { hashFile, downloadModel, verifyModel } = require('./download.cjs');
const { readJson, atomicWrite } = require('./store.cjs');
const GROUPS = ['core', 'jev', 'vision'];

async function sameRuntimeBase(previous, current) {
  if (typeof previous !== 'string' || !previous) return false;
  if (previous === current) return true;
  // macOS can report /private/var for an app reached through /var.
  try {
    const [oldPath, newPath] = await Promise.all([fs.realpath(previous), fs.realpath(current)]);
    return oldPath === newPath;
  } catch {
    return false;
  }
}

class PackagedRuntime {
  constructor(paths, { execute = run, progress = () => {} } = {}) { this.paths = paths; this.execute = execute; this.progress = progress; }
  async inputs() {
    const { resources } = this.paths;
    const manifest = await readJson(path.join(resources, 'runtime-manifest.json'), null);
    if (manifest?.version !== 1 || !/^3\.12\.\d+$/.test(manifest.pythonVersion) || !Array.isArray(manifest.files) || !manifest.groups ||
        GROUPS.some(group => typeof manifest.groups[group] !== 'string')) throw Error('packaged_inputs_invalid');
    for (const entry of manifest.files) {
      if (typeof entry.path !== 'string' || path.isAbsolute(entry.path) || entry.path.split('/').includes('..') ||
          !/^[a-f0-9]{64}$/.test(entry.sha256)) throw Error('packaged_inputs_invalid');
      const target = path.join(resources, entry.path);
      if ((await fs.lstat(target)).isSymbolicLink() || await hashFile(target) !== entry.sha256) throw Error('packaged_inputs_invalid');
    }
    return manifest;
  }
  environment(config = {}) {
    const p = this.paths;
    const inherited = { ...process.env };
    for (const name of ['DASHSCOPE_API_KEY', 'DEEPSEEK_API_KEY', 'TYPESAFE_API_KEY', 'OPENAI_API_KEY',
      'PYTHONHOME', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE']) delete inherited[name];
    for (const name of Object.keys(inherited)) if (name.startsWith('BOXAGENT_WECHAT_')) delete inherited[name];
    return { ...inherited, ...config, BOXAGENT_PACKAGED: '1', BOXAGENT_DATA_DIR: p.data,
      BOXAGENT_RESOURCE_ROOT: p.root, BOXAGENT_ENGINE_WATCH: '0', BOXAGENT_JEV_MEM_BACKEND: 'jev',
      PYTHONPATH: p.pythonSource,
      BOXAGENT_WECHAT_ENABLED: config.BOXAGENT_WECHAT_ENABLED === '1' ? '1' : '0',
      BOXAGENT_JEV_MEM_PYTHON: p.jevPython, BOXAGENT_PERCEPTION_PYTHON: p.visionPython,
      BOXAGENT_PERCEPTION_MODEL: p.model, BOXAGENT_EMBEDDING_MODEL: path.join(p.data, 'models/embedding'),
      BOXAGENT_AOQ_SDK_DIR: path.join(p.resources, 'native/aoq'),
      HF_HOME: path.join(p.data, 'models/huggingface'), UV_CACHE_DIR: path.join(p.runtime, 'uv-cache'),
      PYTHONPYCACHEPREFIX: path.join(p.runtime, 'pycache'), PYTHONUNBUFFERED: '1' };
  }
  async probe(python, component, config = {}, signal) {
    await this.execute(python, ['-m', 'boxagent.entrypoints.startup_probe', component,
      '--data', this.paths.data, '--model', this.paths.model],
    { env: this.environment(config), cwd: this.paths.root, signal, timeout: 180000 });
  }
  async install(config, signal) {
    const inputs = await this.inputs(); // Verify all inputs before modifying any environment.
    const p = this.paths;
    await fs.mkdir(p.runtime, { recursive: true });
    const space = await fs.statfs(p.runtime);
    if (space.bavail * space.bsize < 6 * 1024 ** 3) throw Error('disk_space_low');
    for (const group of GROUPS) {
      signal.throwIfAborted();
      const directory = path.join(p.runtime, group), python = path.join(directory, 'bin/python3');
      const marker = path.join(directory, 'boxagent-runtime.json');
      const desired = { version: 1, id: inputs.groups[group], base: path.join(p.resources, 'python/bin/python3') };
      const previous = await readJson(marker, null).catch(() => null);
      if (previous?.id === desired.id && await sameRuntimeBase(previous.base, desired.base)) {
        try {
          await this.probe(python, group === 'jev' ? 'memory' : group, config, signal);
          this.progress({ component: group, phase: 'ready' });
          continue;
        } catch { /* repair below */ }
      }
      this.progress({ component: group, phase: 'installing' });
      // Never delete product data. A partial dependency environment has no success marker.
      await fs.rm(marker, { force: true });
      const uv = path.join(p.resources, 'uv');
      const env = this.environment(config);
      await this.execute(uv, ['venv', '--clear', '--no-config', '--no-python-downloads', '--python', desired.base, directory], { env, signal });
      await this.execute(uv, ['pip', 'sync', '--no-config', '--no-cache', '--no-build', '--python', python,
        '--no-index', '--find-links', path.join(p.resources, 'python-dependencies', group, 'wheels'),
        '--require-hashes', path.join(p.resources, 'python-dependencies', group, 'requirements.lock')],
      { env, signal, timeout: 15 * 60 * 1000, progress: () => this.progress({ component: group, phase: 'installing' }) });
      let checked = false;
      if (group === 'jev') {
        try {
          this.progress({ component: group, phase: 'checking' });
          await this.probe(python, 'memory', config, signal);
          checked = true;
        } catch (error) {
          if (signal.aborted) throw error;
          this.progress({ component: 'embedding', phase: 'downloading' });
          await this.probe(python, 'prepare-memory', config, signal);
        }
      }
      if (group === 'vision') {
        const manifest = await fs.readFile(path.join(p.root, 'scripts/model-manifests/qwen3.5-0.8b-mlx.manifest.tsv'), 'utf8');
        await downloadModel(manifest, p.model, { signal, progress: this.progress });
      }
      this.progress({ component: group, phase: 'checking' });
      if (!checked) await this.probe(python, group === 'jev' ? 'memory' : group, config, signal);
      await atomicWrite(marker, desired);
      this.progress({ component: group, phase: 'ready' });
    }
  }
  async localStatus(config, signal, components = ['core', 'memory', 'vision']) {
    const p = this.paths;
    const result = {};
    for (const [name, python, component] of [['core', p.python, 'core'], ['memory', p.jevPython, 'memory'], ['vision', p.visionPython, 'vision']]) {
      if (!components.includes(name)) continue;
      try {
        if (p.packaged) {
          const group = name === 'memory' ? 'jev' : name;
          const marker = await readJson(path.join(p.runtime, group, 'boxagent-runtime.json'), null);
          if (marker?.base && !await sameRuntimeBase(marker.base, path.join(p.resources, 'python/bin/python3')))
            throw Error('component_runtime_relocated');
        }
        if (name === 'vision') {
          const manifest = await fs.readFile(path.join(p.root, 'scripts/model-manifests/qwen3.5-0.8b-mlx.manifest.tsv'), 'utf8');
          if (!await verifyModel(manifest, p.model)) throw Error('vision_model_missing');
        }
        await this.probe(python, component, config, signal);
        result[name] = { state: 'ready', code: '' };
      } catch (error) { result[name] = { state: 'unavailable', code: error.message }; }
    }
    return result;
  }
}
module.exports = { PackagedRuntime, GROUPS };
