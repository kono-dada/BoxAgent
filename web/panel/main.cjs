const { app, BrowserWindow, ipcMain, Menu } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { EngineClient } = require("./engine-client.cjs");
const { ServiceClient } = require("./desktop/service-client.cjs");
const { DesktopHost } = require("./desktop/host.cjs");
const root = path.resolve(__dirname, "../..");
app.setName("BoxAgent");
const productIcon = path.join(__dirname, "public/brand/boxagent-icon.png");
const data = path.resolve(
  process.env.BOXAGENT_DATA_DIR || path.join(root, ".runtime/pet"),
);
let companionMode = process.argv.includes("--desktop");
const smoke = process.argv.includes("--smoke");
const methods = new Set([
  "state",
  "list_sessions",
  "context_snapshot",
  "context_entry",
  "session_timeline",
  "session_events",
  "memory_snapshot",
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
  "desktop_toggle_bubble",
  "desktop_avatars",
  "desktop_preview_avatar",
  "desktop_select_avatar",
  "desktop_persona",
  "desktop_save_persona",
  "desktop_runs",
  "desktop_open_run",
  "desktop_open_logs",
  "desktop_quit",
]);
let client,
  service,
  host,
  panel,
  disposed = false,
  reconnect,
  shuttingDown = false;
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
function startDesktop() {
  if (startingDesktop) return startingDesktop;
  companionMode = true;
  app.dock?.hide();
  startingDesktop = (async () => {
    fs.mkdirSync(path.join(data, "logs"), { recursive: true });
    const log = fs.openSync(path.join(data, "logs/desktop-service.log"), "a");
    const args = JSON.parse(process.env.BOXAGENT_DESKTOP_ARGS || "[]");
    service = new ServiceClient({ root, data, log, args });
    fs.closeSync(log);
    service.on("diagnostic", (message) => {
      broadcast("engine:diagnostic", message);
      host?.feedback(message);
    });
    host = new DesktopHost({
      app,
      service,
      client,
      data,
      showPanel,
      broadcast,
      registerWindow,
    });
    await host.start();
  })();
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
  if (companionMode) app.dock?.hide();
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
  client.on("state", (state, event) => {
    broadcast("engine:state", state);
    broadcast("engine:event", event);
    host?.stateChanged(state, event);
  });
  client.on("online", () => broadcast("engine:diagnostic", "Engine 已连接"));
  client.on("diagnostic", (message) => broadcast("engine:diagnostic", message));
  client.on("offline", () => {
    broadcast("engine:diagnostic", "Engine 已断开");
    if (!disposed) reconnect = setTimeout(() => client.connect(), 1000);
  });
  ipcMain.handle("engine:request", (event, method, params) => {
    if (!windows.has(BrowserWindow.fromWebContents(event.sender)))
      throw Error("未知窗口");
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
  ipcMain.on("desktop:action", (event, action, params) => {
    if (!host || !windows.has(BrowserWindow.fromWebContents(event.sender)))
      return;
    if (action === "show-panel") showPanel();
    else if (action === "show-tasks") host.showTasks();
    else if (action === "collapse-tasks") host.collapseTasks();
    else if (action === "pin-tasks") host.pinTasks();
    else if (action === "hide-bubble") host.hideBubble();
    else if (action === "dismiss-feedback") host.dismissFeedback();
    else if (action === "resize-bubble")
      host.resizeBubble(event.sender, params);
    else if (action === "drag") host.dragWindow(event.sender, params);
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
  if (companionMode) {
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
  clearTimeout(reconnect);
  host?.close();
  client?.removeAllListeners();
  client?.close();
  Promise.resolve(service?.close())
    .catch((error) => console.error(error))
    .finally(() => app.quit());
});
