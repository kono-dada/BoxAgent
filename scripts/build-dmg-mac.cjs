// Build a Finder drag-to-install window from the already signed application.
const path = require('node:path');
const fs = require('node:fs/promises');
const { spawnSync } = require('node:child_process');
const { build, Platform, Arch } = require('../web/panel/node_modules/electron-builder');
const root = path.resolve(__dirname, '..');

async function main() {
  const [, , app, output] = process.argv;
  if (!app || !output || !app.endsWith('.app') || !output.endsWith('.dmg'))
    throw Error('Usage: build-dmg-mac.cjs SIGNED_APP OUTPUT_DMG');
  const volume = 'BoxAgent Installer';
  // dmg-builder otherwise detaches a matching mounted volume automatically.
  try { await fs.access(path.join('/Volumes', volume)); throw Error('Please eject the previous BoxAgent Installer volume before rebuilding'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const art = path.join(root, '.runtime/installer-art');
  const cache = path.join(root, '.runtime/swift-module-cache');
  await fs.mkdir(cache, { recursive: true });
  const rendered = spawnSync('/usr/bin/swift', ['-module-cache-path', cache,
    path.join(__dirname, 'render-dmg-background.swift'), art], { stdio: 'inherit' });
  if (rendered.status !== 0) throw Error('Installer artwork failed');
  const config = require('../web/panel/electron-builder.cjs');
  await build({ projectDir: path.join(root, 'web/panel'),
    prepackaged: path.resolve(app), targets: Platform.MAC.createTarget('dmg', Arch.arm64), publish: 'never',
    config: { ...config, directories: { ...config.directories, output: path.dirname(path.resolve(output)) },
      dmg: { title: volume, artifactName: path.basename(output),
        background: path.join(art, 'dmg-background.png'), format: 'UDZO',
        iconSize: 80, iconTextSize: 13, writeUpdateInfo: false, sign: false,
        window: { x: 260, y: 180, width: 600, height: 400 },
        contents: [{ x: 165, y: 190 }, { x: 435, y: 190, type: 'link', path: '/Applications' }],
      },
    },
  });
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
