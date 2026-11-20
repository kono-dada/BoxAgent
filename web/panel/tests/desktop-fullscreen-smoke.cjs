// Verify the floating desktop windows over a separate native full-screen app.
const assert = require('node:assert/strict');
const { execFileSync, spawn } = require('node:child_process');
const { once } = require('node:events');

function inspectDesktop(pids, restorePid = 0) {
  return JSON.parse(execFileSync('/usr/bin/swift', ['-e', `
    import AppKit
    import CoreGraphics
    let pids = CommandLine.arguments.dropFirst().dropLast().compactMap(Int.init)
    let restore = pid_t(CommandLine.arguments.last!)!
    if restore != 0 { NSRunningApplication(processIdentifier: restore)?.activate() }
    let front = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []
    let selected = windows.filter { pids.contains($0[kCGWindowOwnerPID as String] as? Int ?? 0) }
    print(String(data: try! JSONSerialization.data(withJSONObject: ["front": front, "windows": selected]), encoding: .utf8)!)`,
  ...pids.map(String), String(restorePid)], { encoding: 'utf8' }));
}

exports.run = async ({ pet, stage, bubble, js, wait }) => {
  assert.equal(process.platform, 'darwin');
  const previous = inspectDesktop([]).front;
  const fullscreen = spawn('/usr/bin/swift', ['-e', `
    import AppKit
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    let screen = NSScreen.screens.first!
    let window = NSWindow(contentRect: screen.visibleFrame.insetBy(dx: 100, dy: 100),
      styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false, screen: screen)
    window.title = "BoxAgent — Full-screen test"
    window.collectionBehavior = [.fullScreenPrimary]
    let observer = NotificationCenter.default.addObserver(forName: NSWindow.didEnterFullScreenNotification,
      object: window, queue: .main) { _ in
        app.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        print("ready"); fflush(stdout)
      }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
      window.makeKeyAndOrderFront(nil)
      app.activate(ignoringOtherApps: true)
      DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { window.toggleFullScreen(nil) }
    }
    app.run()`], { stdio: ['ignore', 'pipe', 'inherit'] });
  try {
    let ready = false;
    fullscreen.stdout.on('data', chunk => { if (String(chunk).includes('ready')) ready = true; });
    await wait(() => ready || fullscreen.exitCode !== null, 'native app entered full screen');
    assert(ready, 'The native full-screen fixture exited before opening');
    let first = inspectDesktop([fullscreen.pid]);
    if (first.front !== fullscreen.pid) {
      inspectDesktop([], fullscreen.pid);
      first = inspectDesktop([fullscreen.pid]);
    }
    assert.equal(first.front, fullscreen.pid, 'native app has foreground');
    await js(bubble, "boxagent.desktopAction('hide-bubble')");
    for (const phase of ['start', 'end']) {
      const point = { x: pet.getBounds().x + 20, y: pet.getBounds().y + 20 };
      await js(pet, `boxagent.desktopAction('drag', ${JSON.stringify({phase, buttons: phase === 'start' ? 1 : 0, point})})`);
    }
    await wait(() => bubble.isVisible(), 'pet tap opens chat');
    const assertSameSpace = () => {
      const { front, windows } = inspectDesktop([fullscreen.pid, process.pid]);
      assert.equal(front, fullscreen.pid, 'BoxAgent leaves the full-screen app in the foreground');
      for (const window of [stage, pet, bubble]) {
        const {x, y, width, height} = window.getBounds();
        assert(windows.some(row => row.kCGWindowOwnerPID === process.pid && row.kCGWindowBounds.X === x &&
          row.kCGWindowBounds.Y === y && row.kCGWindowBounds.Width === width && row.kCGWindowBounds.Height === height),
        `companion window stays on the active space: ${window.webContents.getURL()}`);
      }
      assert(windows.some(row => row.kCGWindowOwnerPID === fullscreen.pid), 'native full-screen window remains on screen');
    };
    assertSameSpace();
    bubble.focus();
    await js(bubble, "document.querySelector('textarea').focus()");
    await wait(() => bubble.isFocused(), 'chat accepts keyboard focus');
    await bubble.webContents.insertText('fullscreen');
    assert.equal(await js(bubble, "document.querySelector('textarea').value"), 'fullscreen');
    assertSameSpace();
    console.log('Full-screen desktop pet, chat and keyboard focus stay over the foreground app');
  } finally {
    if (fullscreen.exitCode === null && fullscreen.signalCode === null) {
      const closed = once(fullscreen, 'exit');
      fullscreen.kill();
      await closed;
    }
    if (previous > 0) inspectDesktop([], previous);
  }
};
