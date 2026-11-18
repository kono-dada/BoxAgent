const { app, BrowserWindow, ipcMain, Menu, protocol, safeStorage, systemPreferences, shell, dialog, desktopCapturer } = require("electron");
const { CameraWindow } = require("./camera/window.cjs");
protocol.registerSchemesAsPrivileged([{ scheme: 'boxagent-camera', privileges: {
  standard: true, secure: true, supportFetchAPI: true, corsEnabled: true,
} }]);
const fs = require("node:fs");
const path = require("node:path");
const { EngineClient } = require("./engine-client.cjs");
const { ServiceClient } = require("./desktop/service-client.cjs");
const { DesktopHost } = require("./desktop/host.cjs");
const { publicSourceUrl } = require('./desktop/web-links.cjs');
const { resolvePaths } = require('./startup/paths.cjs');
const packaged = app.isPackaged && !process.defaultApp;
const paths = resolvePaths({ packaged, panel: __dirname, appData: app.getPath('appData') });
const root = paths.root;
app.setName("BoxAgent");
const productIcon = path.join(__dirname, "public/brand/boxagent-icon.png");
const data = paths.data;
const setupEnabled = packaged || process.argv.includes('--setup');
let companionMode = packaged || process.argv.includes("--desktop");
const smoke = process.argv.includes("--smoke");
const methods = new Set([
  "state",
  "list_sessions",
  "context_snapshot",
  "context_entry",
  "session_timeline",
  "session_events",
  "memory_snapshot",
  "memory_evidence",
  "delete_memory_node",
  "create_session",
  "activate_session",
  "archive_session",
  "toggle_voice",
  "toggle_context",
  "submit_text",
  "cancel_task",
  "cancel_all_tasks",
  "answer_approval",
  "list_skills",
  "create_skill",
  "update_skill",
  "set_skill_enabled",
  "delete_skill",
]);
const desktopMethods = new Set([
  "desktop_snapshot",
  "desktop_set_pet_scale",
  "desktop_set_pet_display",
  "desktop_toggle_bubble",
  "desktop_avatars",
  "desktop_preview_avatar",
  "desktop_select_avatar",
  "desktop_persona",
  "desktop_save_persona",
  "desktop_runs",
  "desktop_open_run",
  "desktop_open_logs",
  "desktop_open_game_news",
  "desktop_quit",
]);
let client,
  camera,
  service,
  host,
  panel,
  disposed = false,
  reconnect,
  shuttingDown = false;
let startup, setupWindow, serviceEnvironment = process.env;
const windows = new Set();
const broadcast = (channel, value) => {
  for (const window of windows)
    if (!window.isDestroyed()) window.webContents.send(channel, value);
};
function registerWindow(window) {
  windows.add(window);
  window.on("closed", () => windows.delete(window));
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
}
function showPanel() {
  if (panel && !panel.isDestroyed()) {
    panel.show();
    panel.focus();
    return panel;
  }
  panel = new BrowserWindow({
    width: 1200,
    height: 820,
    minWidth: 740,
    minHeight: 560,
    title: "BoxAgent · 伙伴控制台",
    icon: productIcon,
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 16, y: 15 },
    backgroundColor: "#ffffff",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: setupEnabled ? ['--boxagent-setup'] : [],
    },
  });
  registerWindow(panel);
  panel.on("closed", () => {
    panel = null;
  });
  panel.loadFile(path.join(__dirname, "dist/index.html"));
  return panel;
}
let startingDesktop;
function startService() {
  if (service && service.child.exitCode === null && service.child.signalCode === null) return;
  service = undefined;
  fs.mkdirSync(path.join(data, 'logs'), { recursive: true });
  const log = fs.openSync(path.join(data, 'logs/desktop-service.log'), 'a');
  try {
    const args = setupEnabled ? ['--task-provider', serviceEnvironment.BOXAGENT_TASK_PROVIDER || 'deepseek'] :
      JSON.parse(process.env.BOXAGENT_DESKTOP_ARGS || '[]');
    service = new ServiceClient({ root, data, log, args, python: setupEnabled ? paths.python : undefined, environment: serviceEnvironment });
  } finally { fs.closeSync(log); }
  service.on('diagnostic', message => { broadcast('engine:diagnostic', message); host?.feedback(message); });
}
async function stopService() {
  host?.close(); host = undefined; startingDesktop = undefined;
  const previous = service; service = undefined;
  await previous?.close();
}
function startDesktop() {
  if (startingDesktop) return startingDesktop;
  companionMode = true;
  startingDesktop = (async () => {
    startService();
    host = new DesktopHost({
      app,
      service,
      client,
      data,
      showPanel,
      showCamera: () => camera.show(),
      broadcast,
      registerWindow,
      wakeAllowed: () => {
        if (!setupEnabled) return true;
        const permissions = startup?.permissions.snapshot();
        const signature = JSON.stringify(permissions);
        if (host && host.lastWakePermissions !== signature) {
          host.lastWakePermissions = signature;
          try {
            fs.appendFileSync(path.join(data, 'logs/wake-permissions.jsonl'),
              JSON.stringify({ at: new Date().toISOString(), permissions }) + '\n', { mode: 0o600 });
          } catch { /* Diagnostics must not prevent voice startup. */ }
        }
        return permissions?.speech === 'granted' && permissions.microphone === 'granted';
      },
    });
    await host.start();
    await app.dock?.show();
  })().catch(error => { startingDesktop = undefined; throw error; });
  return startingDesktop;
}
// Lock identity includes data directory so isolated fixtures cannot steal the live app.
app.setPath(
  "userData",
  process.env.BOXAGENT_ELECTRON_USER_DATA ||
    path.join(data, "runtimes/electron"),
);
if (
  !smoke &&
  !app.requestSingleInstanceLock({
    mode: companionMode ? "desktop" : "panel",
    python: process.env.BOXAGENT_DESKTOP_PYTHON,
    args: process.env.BOXAGENT_DESKTOP_ARGS,
  })
)
  app.quit();
app.on("second-instance", (_event, _argv, _cwd, info) => {
  if (info?.mode === "desktop") {
    if (host) host.showBubble();
    else if (info.python) {
      process.env.BOXAGENT_DESKTOP_PYTHON = info.python;
      process.env.BOXAGENT_DESKTOP_ARGS = info.args || "[]";
      void startDesktop().catch((error) => {
        console.error(error);
        showPanel();
      });
    } else showPanel();
  } else showPanel();
});
app.whenReady().then(async () => {
  if (!smoke && !app.hasSingleInstanceLock()) return;
  app.dock?.setIcon(productIcon);
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: "BoxAgent",
        submenu: [
          { label: "关于 BoxAgent", role: "about" },
          { type: "separator" },
          { role: "hide" },
          { role: "hideOthers" },
          { role: "unhide" },
          { type: "separator" },
          { label: "退出 BoxAgent", role: "quit" },
        ],
      },
      {
        label: "编辑",
        submenu: [
          { role: "undo" },
          { role: "redo" },
          { type: "separator" },
          { role: "cut" },
          { role: "copy" },
          { role: "paste" },
          { role: "selectAll" },
        ],
      },
    ]),
  );
  client = new EngineClient(path.join(data, "engine.sock"));
  camera = new CameraWindow({ root, panelRoot: __dirname, client, registerWindow,
    onState: (state) => broadcast('desktop:camera', state) });
  ipcMain.handle('camera:request', (event, method, params) => camera.request(event, method, params));
  client.on("state", (state, event) => {
    broadcast("engine:state", state);
    broadcast("engine:event", event);
    host?.stateChanged(state, event);
  });
  client.on("online", () => broadcast("engine:diagnostic", "Engine 已连接"));
  client.on("diagnostic", (message) => broadcast("engine:diagnostic", message));
  client.on("offline", () => {
    host?.syncWakeWord();
    camera.disconnected();
    broadcast("engine:diagnostic", "Engine 已断开");
    if (!disposed) reconnect = setTimeout(() => client.connect(), 1000);
  });
  ipcMain.handle("engine:request", (event, method, params) => {
    if (!windows.has(BrowserWindow.fromWebContents(event.sender)))
      throw Error("未知窗口");
    if (method === 'desktop_open_web_source') {
      const url = publicSourceUrl(params?.url);
      return shell.openExternal(url).then(() => ({ status: 'opened' }));
    }
    if (method === 'desktop_open_camera') { camera.show(); return { status: 'opened' }; }
    if (method === 'desktop_toggle_camera') return camera.toggle();
    if (method === 'desktop_camera_state') return camera.snapshot();
    if (desktopMethods.has(method))
      return host
        ? host.request(method, params)
        : Promise.reject(Error("请启动完整桌宠以使用此功能"));
    if (!methods.has(method)) throw Error("面板未提供此操作");
    return client.request(method, params);
  });
  ipcMain.handle("engine:errors", async () => {
    try {
      const file = await fs.promises.open(
        path.join(data, "logs/errors.jsonl"),
        "r",
      );
      try {
        const size = (await file.stat()).size,
          buffer = Buffer.alloc(Math.min(size, 128000));
        await file.read(
          buffer,
          0,
          buffer.length,
          Math.max(0, size - buffer.length),
        );
        return buffer
          .toString()
          .split("\n")
          .flatMap((line) => {
            try {
              return [JSON.parse(line)];
            } catch {
              return [];
            }
          })
          .slice(-100);
      } finally {
        await file.close();
      }
    } catch (error) {
      if (error.code === "ENOENT") return [];
      throw error;
    }
  });
  if (setupEnabled) {
    const { StartupCoordinator } = require('./startup/coordinator.cjs');
    const { CredentialStore } = require('./startup/credentials.cjs');
    const { Permissions } = require('./startup/permissions.cjs');
    const { SpeechPermissions } = require('./startup/speech.cjs');
    startup = new StartupCoordinator({ paths,
      credentials: new CredentialStore(path.join(data, 'state/credentials.json'), safeStorage),
      permissions: new Permissions(systemPreferences, shell, {
        speech: new SpeechPermissions(),
        onVoiceGranted: () => { host?.wakeWord?.setEnabled(true); host?.syncWakeWord(); },
        requestScreen: async () => {
        await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false });
      } }),
      ensureEngine: async environment => {
        serviceEnvironment = environment; startService(); client.connect();
        const deadline = Date.now() + 30000;
        while (Date.now() < deadline) {
          try { const health = await client.request('health'); if (health?.status === 'ready') return; }
          catch { /* Bounded, read-only retries while the socket is being created. */ }
          await new Promise(resolve => setTimeout(resolve, 250));
        }
        throw Error('engine_start_failed');
      }, stopEngine: stopService, enterDesktop: startDesktop,
      broadcast: snapshot => setupWindow?.send(snapshot),
    });
    setupWindow = require('./startup/window.cjs').createSetupWindow({ app, BrowserWindow, ipcMain, panelRoot: __dirname, registerWindow, coordinator: startup, shell, dialog });
    ipcMain.on('setup:open', event => {
      if (windows.has(BrowserWindow.fromWebContents(event.sender))) { void startup.request('repair'); setupWindow.show(); }
    });
  }
  ipcMain.on("desktop:action", (event, action, params) => {
    if (!host || !windows.has(BrowserWindow.fromWebContents(event.sender)))
      return;
    if (action === "show-panel") showPanel();
    else if (action === 'display-menu') host.showDisplayMenu(event.sender);
    else if (action === "show-tasks") host.showTasks();
    else if (action === "collapse-tasks") host.collapseTasks();
    else if (action === "pin-tasks") host.pinTasks();
    else if (action === "hide-bubble") host.hideBubble();
    else if (action === "dismiss-feedback") host.dismissFeedback();
    else if (action === "resize-bubble")
      host.resizeBubble(event.sender, params);
    else if (action === "drag") host.dragWindow(event.sender, params);
    else if (action === "pet-perform")
      host.performPet(event.sender, params?.name ?? params);
    else if (action === "pet-ready") {
      host.rendererReady(event.sender);
    } else if (action === "pet-report")
      void host
        .report(event.sender, params)
        .catch((error) => host.feedback(error.message));
    else if (action === "context-menu")
      host.menu?.popup({ window: BrowserWindow.fromWebContents(event.sender) });
  });
  client.connect();
  if (setupEnabled) {
    setupWindow.show();
    void startup.start().then(snapshot => {
      if (snapshot.onboarding.completed && ['ready', 'degraded'].includes(snapshot.mode) &&
          snapshot.capabilities.engine?.state === 'ready' && !snapshot.error) setupWindow.hide();
    });
  } else if (companionMode) {
    try {
      await startDesktop();
    } catch (error) {
      console.error(error);
      host?.feedback(error.message);
      showPanel();
    }
  } else showPanel();
  if (smoke)
    panel.webContents.once("did-finish-load", async () => {
      try {
        await require("./tests/smoke.cjs").runSmoke(panel);
        app.quit();
      } catch (error) {
        console.error(error);
        app.exit(1);
      }
    });
});
app.on("window-all-closed", () => {
  if (!companionMode) app.quit();
});
app.on("before-quit", (event) => {
  if (shuttingDown) return;
  event.preventDefault();
  shuttingDown = true;
  disposed = true;
  startup?.close();
  clearTimeout(reconnect);
  host?.close();
  void camera?.stop();
  client?.removeAllListeners();
  client?.close();
  Promise.resolve(service?.close())
    .catch((error) => console.error(error))
    .finally(() => app.quit());
});
