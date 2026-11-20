const { execFileSync } = require('node:child_process');
const path = require('node:path');

module.exports = {
  appId: 'com.boxagent.desktop', productName: 'BoxAgent', asar: true,
  electronDist: require('node:path').join(__dirname, 'node_modules/electron/dist'),
  directories: { output: '../../dist/mac' },
  files: ['main.cjs', 'preload.cjs', 'engine-client.cjs', 'desktop/**/*.cjs', 'camera/**/*.cjs',
    'startup/**/*.cjs', 'dist/**/*', 'public/brand/**/*', '!**/*.test.*'],
  extraResources: [{ from: '../../.runtime/package-resources', to: '.', filter: ['**/*'] }],
  afterPack({ electronPlatformName, appOutDir, packager }) {
    if (electronPlatformName !== 'darwin') return;
    const bundle = `${packager.appInfo.productFilename}.app`;
    execFileSync('/usr/bin/swiftc', ['-target', 'arm64-apple-macos14.0', path.join(__dirname, 'scripts/wake-listener.swift'),
      '-o', path.join(appOutDir, bundle, 'Contents/MacOS/BoxAgentWakeListener')]);
  },
  mac: { target: [{ target: 'dir', arch: ['arm64'] }], icon: 'public/brand/boxagent.icns',
    category: 'public.app-category.productivity', minimumSystemVersion: '14.0',
    hardenedRuntime: true, entitlements: 'packaging/entitlements.mac.plist', entitlementsInherit: 'packaging/entitlements.mac.plist',
    extendInfo: {
      NSMicrophoneUsageDescription: 'BoxAgent uses the microphone for voice wake and conversation.',
      NSSpeechRecognitionUsageDescription: 'BoxAgent recognizes your companion’s name on this Mac for hands-free voice wake.',
      NSScreenCaptureUsageDescription: 'BoxAgent reads screen content when you enable desktop vision or request a desktop task.',
      NSAccessibilityUsageDescription: 'BoxAgent controls apps for desktop tasks you request.',
      NSAppleEventsUsageDescription: 'BoxAgent communicates with the desktop execution component to operate apps for tasks you request.',
      NSCameraUsageDescription: 'BoxAgent shares camera frames with Qwen only while you enable video conversation.',
    },
  },
};
