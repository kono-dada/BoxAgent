const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
// Electron virtualizes app.asar as a directory. Resource integrity needs its raw bytes.
const { createReadStream } = process.versions.electron ? require('original-fs') : require('node:fs');
const BASE = 'https://modelscope.cn/models/mlx-community/Qwen3.5-0.8B-4bit/resolve/master';

async function hashFile(file) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}
function parseManifest(raw) {
  const entries = raw.trim().split('\n').map(line => line.trim().split(/\s+/));
  if (!entries.length || entries.some(([file, digest, extra]) => !file || path.isAbsolute(file) ||
      file.split('/').some(part => !part || part === '..') || file.includes('\\') ||
      !/^[a-f0-9]{64}$/.test(digest) || extra)) throw Error('invalid_model_manifest');
  return entries;
}
async function verifyModel(manifest, directory) {
  for (const [file, digest] of parseManifest(manifest)) {
    try {
      if ((await fs.lstat(path.join(directory, file))).isSymbolicLink() || await hashFile(path.join(directory, file)) !== digest) return false;
    } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  }
  return true;
}
async function downloadModel(manifest, directory, { signal, progress, fetcher = fetch }) {
  await fs.mkdir(directory, { recursive: true });
  if ((await fs.statfs(directory)).bavail * (await fs.statfs(directory)).bsize < 2 * 1024 ** 3) throw Error('disk_space_low');
  const entries = parseManifest(manifest);
  for (const [index, [file, digest]] of entries.entries()) {
    signal.throwIfAborted();
    const target = path.join(directory, file);
    try { if (await hashFile(target) === digest) continue; } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const controller = AbortSignal.any([signal, AbortSignal.timeout(10 * 60 * 1000)]);
    const response = await fetcher(`${BASE}/${encodeURIComponent(file)}`, { signal: controller });
    if (!response.ok || !response.body) throw Error('model_download_failed');
    const temporary = `${target}.part`;
    await fs.mkdir(path.dirname(target), { recursive: true });
    const handle = await fs.open(temporary, 'w', 0o600);
    let bytes = 0;
    try {
      for await (const chunk of response.body) {
        bytes += chunk.length;
        if (bytes > 3 * 1024 ** 3) throw Error('model_size_limit');
        await handle.write(chunk);
        progress({ component: 'vision-model', file, bytes, total: Number(response.headers.get('content-length')) || null, index, count: entries.length });
      }
      await handle.sync();
    } finally { await handle.close(); }
    if (await hashFile(temporary) !== digest) throw Error('model_hash_mismatch');
    await fs.rename(temporary, target);
  }
}
module.exports = { hashFile, parseManifest, verifyModel, downloadModel };
