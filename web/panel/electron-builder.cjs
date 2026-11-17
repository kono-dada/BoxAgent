module.exports = {
  appId: 'com.boxagent.desktop', productName: 'BoxAgent', asar: true,
  electronDist: require('node:path').join(__dirname, 'node_modules/electron/dist'),
  directories: { output: '../../dist/mac' },
  files: ['main.cjs', 'preload.cjs', 'engine-client.cjs', 'desktop/**/*.cjs', 'camera/**/*.cjs',
    'startup/**/*.cjs', 'dist/**/*', 'public/brand/**/*', '!**/*.test.*'],
  extraResources: [{ from: '../../.runtime/package-resources', to: '.', filter: ['**/*'] }],
  mac: { target: [{ target: 'dir', arch: ['arm64'] }], icon: 'public/brand/boxagent.icns',
    category: 'public.app-category.productivity', minimumSystemVersion: '14.0',
    hardenedRuntime: true, entitlements: 'packaging/entitlements.mac.plist', entitlementsInherit: 'packaging/entitlements.mac.plist',
    extendInfo: {
      NSMicrophoneUsageDescription: 'BoxAgent uses the microphone when you enable voice conversation.',
      NSScreenCaptureUsageDescription: 'BoxAgent reads screen content when you enable desktop vision or request a desktop task.',
      NSAccessibilityUsageDescription: 'BoxAgent controls apps for desktop tasks you request.',
      NSCameraUsageDescription: 'BoxAgent shares camera frames with Qwen only while you enable video conversation.',
    },
  },
};
