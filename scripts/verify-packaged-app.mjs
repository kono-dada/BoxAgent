import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createReadStream } from 'node:fs';
const bundle = process.argv[2];
if (!bundle) throw Error('Usage: node scripts/verify-packaged-app.mjs /path/to/BoxAgent.app');
const resources = path.join(bundle, 'Contents/Resources');
const manifest = JSON.parse(fs.readFileSync(path.join(resources, 'runtime-manifest.json'), 'utf8'));
if (manifest.version !== 1 || !/^3\.12\.\d+$/.test(manifest.pythonVersion) || manifest.platform !== 'macos-arm64') throw Error('Invalid manifest');
for (const group of ['core', 'jev', 'vision']) if (!manifest.groups[group]) throw Error(`Missing ${group} environment`);
for (const entry of manifest.files) {
  if (path.isAbsolute(entry.path) || entry.path.split('/').includes('..')) throw Error('Invalid manifest path');
  const file = path.join(resources, entry.path);
  const hash = crypto.createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  if (hash.digest('hex') !== entry.sha256) throw Error(`Resource mismatch: ${entry.path}`);
}
for (const file of ['python/bin/python3', 'uv', 'boxagent/boxagent/entrypoints/desktop_service.py', 'boxagent/assets/vrm/viewer.js',
  'boxagent/boxagent/entrypoints/wechat_setup.py', 'wechat/manifest.json', 'wechat/4.1.15.270115.dmg', 'app.asar'])
  if (!fs.existsSync(path.join(resources, file))) throw Error(`Missing resource: ${file}`);
const wechat = JSON.parse(fs.readFileSync(path.join(resources, 'wechat/manifest.json'), 'utf8'));
const wechatImage = manifest.files.find(entry => entry.path === 'wechat/4.1.15.270115.dmg');
if (wechat.version !== 1 || wechat.clientVersion !== '4.1.15' || wechat.build !== '270115' ||
    wechat.bundledPath !== 'wechat/4.1.15.270115.dmg' || wechat.url !== null ||
    !wechatImage || wechatImage.sha256 !== wechat.sha256 ||
    fs.statSync(path.join(resources, wechat.bundledPath)).size !== wechat.size) throw Error('Invalid pinned WeChat distribution');
for (const entry of manifest.files) if (/(^|\/)(\.env(?:\.|$)|auth\.json$|__pycache__|node_modules|\.runtime|\.venv)/.test(entry.path)) throw Error(`Private/cache resource: ${entry.path}`);
console.log(`Verified ${manifest.files.length} resources, Python ${manifest.pythonVersion}, core/Jev/vision, WeChat 4.1.15 build 270115.`);
