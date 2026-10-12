// Full production Electron host + real VRM/resource service, isolated fake Engine.
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");
const root = path.resolve(__dirname, "../../..");
const fixture =
  process.env.BOXAGENT_DESKTOP_FIXTURE ||
  fs.mkdtempSync(path.join(os.tmpdir(), "boxagent-desktop-"));
const second = process.argv.includes("--second-open");
fs.mkdirSync(path.join(fixture, "data"), { recursive: true });
const data = fs.realpathSync(path.join(fixture, "data"));
process.env.BOXAGENT_DATA_DIR = data;
process.env.BOXAGENT_ELECTRON_USER_DATA = path.join(fixture, "profile");
const python = path.join(fixture, "python");
if (!second) {
  fs.writeFileSync(
    python,
    `#!/bin/sh\nexec '${root}/.venv/bin/python3' '${root}/tests/fixtures/electron_desktop.py'\n`,
    { mode: 0o700 },
  );
}
process.env.BOXAGENT_DESKTOP_PYTHON = python;
process.env.BOXAGENT_DESKTOP_ARGS = "[]";
if (!process.argv.includes("--panel-first")) process.argv.push("--desktop");
if (second) {
  require("../main.cjs");
  return;
}
const calls = [],
  sockets = new Set();
const state = {
  session_id: "ses_one",
  voice: "off",
  task: "idle",
  user_text: "",
  assistant_text: "",
  task_text: "",
  approval: "",
};
let events = Array.from({ length: 32 }, (_, index) => ({
  event_id: `row${index}`,
  sequence: index + 1,
  type: "message.final",
  role: index % 2 ? "assistant" : "user",
  content: `${index} ${index % 2 ? "**答复**\n\n- 可以聊天\n- 可以执行任务" : "历史问题 ".repeat(8)}`,
  occurred_at: 172800 + index,
}));
const send = (socket, row) => socket.write(JSON.stringify(row) + "\n");
const emit = (type, patch = {}) => {
  Object.assign(state, patch);
  for (const socket of sockets)
    send(socket, { type: "event", event: { type, state: { ...state } } });
};
const server = net.createServer((socket) => {
  sockets.add(socket);
  socket.setEncoding("utf8");
  socket.on("close", () => sockets.delete(socket));
  send(socket, {
    type: "event",
    event: { type: "engine.ready", state: { ...state } },
  });
  let buffer = "";
  socket.on("data", (chunk) => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const request = JSON.parse(buffer.slice(0, end));
      buffer = buffer.slice(end + 1);
      calls.push(request);
      let result;
      const params = request.params || {};
      switch (request.method) {
        case "state":
          result = { ...state };
          break;
        case "list_notifications":
        case "list_sessions":
          result = [];
          break;
        case "session_events":
          result = events;
          break;
        case "submit_text":
          result = { status: "accepted" };
          events.push({
            event_id: "submitted",
            sequence: 100,
            type: "message.final",
            role: "user",
            content: params.goal,
          });
          emit("conversation.updated", { user_text: params.goal });
          break;
        case "toggle_voice":
          result = { status: "succeeded" };
          emit("voice.ready", {
            voice: state.voice === "off" ? "ready" : "off",
          });
          break;
        case "answer_approval":
          result = { status: "succeeded" };
          emit("task.running", { task: "running", approval: "" });
          break;
        case "cancel_task":
          result = { status: "succeeded" };
          emit("task.cancelled", { task: "cancelled" });
          break;
        case "create_session":
          result = { session_id: "ses_two" };
          events = [];
          emit("session.ready", {
            session_id: "ses_two",
            user_text: "",
            assistant_text: "",
            task: "idle",
            approval: "",
          });
          break;
        default:
          result = { status: "succeeded" };
      }
      send(socket, { id: request.id, ok: true, result });
    }
  });
});
const timeout = setTimeout(() => {
  console.error("desktop fixture timed out");
  app.exit(1);
}, 60000);
const wait = async (condition, label) => {
  for (let i = 0; i < 300; i++) {
    if (await condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw Error(label);
};
const js = (window, source) => window.webContents.executeJavaScript(source);
server.listen(path.join(data, "engine.sock"), () => {
  require("../main.cjs");
  app.whenReady().then(async () => {
    try {
      if (process.argv.includes("--panel-first")) {
        await wait(
          () =>
            BrowserWindow.getAllWindows().some((w) =>
              w.webContents.getURL().endsWith("/index.html"),
            ),
          "standalone panel first",
        );
        const converted = new Promise((resolve) =>
          app.once("second-instance", resolve),
        );
        const child = spawn(process.execPath, [__filename, "--second-open"], {
          env: { ...process.env, BOXAGENT_DESKTOP_FIXTURE: fixture },
          stdio: "ignore",
        });
        await converted;
        child.on("exit", () => {});
        const standalone = BrowserWindow.getAllWindows().find((w) =>
          w.webContents.getURL().endsWith("/index.html"),
        );
        standalone.close();
      }

      await wait(
        () =>
          BrowserWindow.getAllWindows().some((w) =>
            w.webContents.getURL().endsWith("/pet.html"),
          ),
        "pet window",
      );
      const pet = BrowserWindow.getAllWindows().find((w) =>
        w.webContents.getURL().endsWith("/pet.html"),
      );
      const bubble = BrowserWindow.getAllWindows().find((w) =>
        w.webContents.getURL().endsWith("/companion.html"),
      );
      await wait(() => pet.isVisible(), "VRM ready and committed");
      const initial = await js(bubble, "boxagent.request('desktop_snapshot')");
      assert(initial.appearance_ready);
      assert(pet.getBounds().width < 252, "crop removes transparent area");
      assert(bubble.getBounds().height <= 600, "long history stays bounded");
      assert.equal(bubble.getBounds().width, 440);
      assert.equal(app.getName(), "BoxAgent");
      assert.equal(
        BrowserWindow.getAllWindows().length,
        2,
        "console stays lazy",
      );
      await js(bubble, "boxagent.request('desktop_toggle_bubble')");
      await wait(() => bubble.isVisible(), "bubble visible");
      const fixedFrame = bubble.getBounds();
      for (const x of [12, 40, 20]) {
        await js(
          pet,
          `boxagent.desktopAction('pet-report',{type:'bounds',viewport:[252,182],bounds:{x:${x},y:18,width:170,height:150}})`,
        );
        await new Promise((r) => setTimeout(r, 70));
        assert.equal(
          bubble.getBounds().x,
          fixedFrame.x,
          "avatar animation keeps chat x",
        );
        assert.equal(
          bubble.getBounds().y,
          fixedFrame.y,
          "avatar animation keeps chat y",
        );
      }
      await wait(
        () =>
          js(
            bubble,
            "document.querySelectorAll('.companion-message').length===32",
          ),
        "history",
      );
      assert(
        await js(
          bubble,
          "Boolean(document.querySelector('.companion-markdown strong'))",
        ),
      );
      bubble.webContents.send('engine:diagnostic', 'Engine 已断开');
      await wait(() => js(bubble, "document.querySelector('.companion-feedback')?.textContent.includes('Engine 已断开')"), 'disconnect feedback');
      emit('engine.ready');
      await wait(() => js(bubble, "!document.querySelector('.companion-feedback')"), 'recovered connection clears stale error');
      bubble.webContents.send('engine:diagnostic', '操作验收失败');
      await wait(() => js(bubble, "document.querySelector('.companion-feedback')?.textContent.includes('操作验收失败')"), 'operation failure visible');
      emit('engine.ready');
      assert(await js(bubble, "document.querySelector('.companion-feedback')?.textContent.includes('操作验收失败')"), 'recovery preserves unrelated operation error');
      bubble.webContents.send('engine:diagnostic', 'Engine 已断开');
      await wait(() => js(bubble, "document.querySelector('.companion-feedback')?.textContent.includes('Engine 已断开')"), 'disconnect feedback again');
      emit('engine.ready');
      await wait(() => js(bubble, "!document.querySelector('.companion-feedback')"), 'connection recovered again');
      await js(
        bubble,
        "document.querySelector('.companion-history').scrollTop=0",
      );
      await wait(
        () =>
          js(bubble, "Boolean(document.querySelector('.companion-latest'))"),
        "latest control",
      );
      await new Promise((r) => setTimeout(r, 150));
      assert(
        await js(
          bubble,
          "document.querySelector('.companion-latest').getBoundingClientRect().top >= document.querySelector('.companion-history').getBoundingClientRect().bottom",
        ),
        "latest control does not cover messages",
      );
      fs.mkdirSync(path.join(root, "artifacts/electron-desktop"), {
        recursive: true,
      });
      fs.writeFileSync(
        path.join(root, "artifacts/electron-desktop/history-reading.png"),
        (await bubble.webContents.capturePage()).toPNG(),
      );
      await js(bubble, "document.querySelector('.companion-latest').click()");
      assert(
        await js(
          bubble,
          "document.querySelector('.companion-history').scrollTop>0",
        ),
      );
      const input = async (value) => {
        await js(
          bubble,
          `(()=>{const el=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event('input',{bubbles:true}));})()`,
        );
        await new Promise((r) => setTimeout(r, 80));
      };
      await input("一个隔离的测试问题");
      await js(
        bubble,
        "document.querySelector('.companion-composer button').click()",
      );
      await wait(
        () => calls.some((r) => r.method === "submit_text"),
        "submit Engine request",
      );
      emit("task.approval", {
        task: "awaiting_approval",
        task_text: "正在执行测试任务",
        approval: "允许写入测试文件？",
        task_started_at: Date.now() / 1000 - 20,
        task_activity_at: Date.now() / 1000 - 18,
      });
      await wait(
        () => js(bubble, "Boolean(document.querySelector('.approval-card'))"),
        "approval",
      );
      await js(
        bubble,
        "document.querySelector('.approval-card button').click()",
      );
      await wait(
        () =>
          calls.some((r) => r.method === "answer_approval" && r.params.allowed),
        "approval sent",
      );
      await js(
        bubble,
        "document.querySelector('.task-status .feature-actions button').click()",
      );
      await wait(
        () => calls.some((r) => r.method === "cancel_task"),
        "cancel sent",
      );
      await js(bubble, "document.querySelector('.companion-voice').click()");
      await wait(
        () =>
          js(
            bubble,
            "document.querySelector('.companion-voice').getAttribute('aria-pressed')==='true'",
          ),
        "voice toggles",
      );
      await js(bubble, "boxagent.desktopAction('show-panel')");
      await wait(() => BrowserWindow.getAllWindows().length === 3, "console");
      const panel = BrowserWindow.getAllWindows().find(
        (w) => w !== pet && w !== bubble,
      );
      await wait(
        () =>
          js(
            panel,
            "Boolean(document.querySelector('[data-view=appearance]')) || Boolean(document.querySelector('[data-view=settings]'))",
          ),
        "console load",
      );
      await js(
        panel,
        "localStorage.setItem('boxagent.interface-preferences',JSON.stringify({theme:'dark',language:'en'}));window.dispatchEvent(new StorageEvent('storage',{key:'boxagent.interface-preferences'}))",
      );
      await wait(
        () =>
          js(
            bubble,
            "document.documentElement.dataset.theme==='dark' && document.documentElement.lang==='en'",
          ),
        "shared preferences",
      );
      assert.equal(await js(panel, "getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim()"), '#171717', 'console neutral dark background');
      assert.equal(await js(bubble, "getComputedStyle(document.querySelector('.companion-shell')).getPropertyValue('--canvas').trim()"), '#192231', 'mini chat retains its existing dark palette');
      fs.mkdirSync(path.join(root, "artifacts/electron-desktop"), {
        recursive: true,
      });
      await new Promise((r) => setTimeout(r, 250));
      assert(
        await js(
          bubble,
          "document.querySelector('.companion-history').clientHeight >= 240",
        ),
        "long history has reading space",
      );
      assert(
        await js(
          bubble,
          "document.querySelector('.companion-composer button').getBoundingClientRect().bottom < innerHeight && document.querySelector('.companion-footer').getBoundingClientRect().bottom <= innerHeight && document.querySelector('.companion-session-button').getBoundingClientRect().right <= innerWidth - 8 && document.querySelector('.companion-shortcut').getBoundingClientRect().right <= document.querySelector('.companion-session-button').getBoundingClientRect().left",
        ),
        "composer and voice fit",
      );
      fs.writeFileSync(
        path.join(root, "artifacts/electron-desktop/companion-dark.png"),
        (await bubble.webContents.capturePage()).toPNG(),
      );
      panel.close();
      assert(!pet.isDestroyed());
      assert(bubble.isVisible());
      const scaled = await js(
        bubble,
        "boxagent.request('desktop_set_pet_scale',{scale:2.37})",
      );
      assert.equal(scaled.scale, 2.37);
      await wait(() => pet.getBounds().height > 182, "scaled VRM crop");
      const before = await js(bubble, "boxagent.request('desktop_snapshot')");
      const failed = await js(
        bubble,
        `boxagent.request('desktop_select_avatar',{id:${JSON.stringify(path.join(data, "catalog/broken"))}}).then(()=>'',error=>error.message)`,
      );
      assert.match(failed, /形象加载失败，已保留当前形象/);
      assert(
        fs
          .readFileSync(path.join(data, "logs/desktop-service.log"), "utf8")
          .includes("deliberate"),
        "avatar parser detail stays in diagnostics",
      );
      await wait(
        () =>
          js(
            bubble,
            "document.querySelector('.companion-feedback')?.textContent.includes('Your current avatar is unchanged')",
          ),
        "readable localized avatar failure",
      );
      assert(
        await js(
          bubble,
          "!document.querySelector('.companion-feedback').textContent.includes('SyntaxError')",
        ),
        "bubble excludes raw parser errors",
      );
      fs.writeFileSync(
        path.join(root, "artifacts/electron-desktop/avatar-failure.png"),
        (await bubble.webContents.capturePage()).toPNG(),
      );
      assert(pet.isVisible());
      assert.equal(
        (await js(bubble, "boxagent.request('desktop_snapshot')")).appearance,
        before.appearance,
      );
      assert(
        !fs.existsSync(path.join(data, "catalog/selected.json")),
        "failed switch did not persist",
      );
      await input("保留会话草稿");
      emit("session.ready", {
        session_id: "ses_two",
        user_text: "",
        assistant_text: "",
        task: "idle",
        approval: "",
      });
      await wait(
        () => js(bubble, "document.querySelector('textarea').value===''"),
        "new session draft clear",
      );
      emit("session.ready", { session_id: "ses_one" });
      await wait(
        () =>
          js(
            bubble,
            "document.querySelector('textarea').value==='保留会话草稿'",
          ),
        "session draft restoration",
      );
      await js(
        bubble,
        "document.querySelector('.companion-session-button').click()",
      );
      await js(
        bubble,
        "document.querySelector('.companion-new-session button').click()",
      );
      await wait(
        () => calls.some((row) => row.method === "create_session"),
        "new session request",
      );
      await wait(
        () =>
          js(
            bubble,
            "document.querySelectorAll('.companion-message').length===0",
          ),
        "new session excludes old history",
      );
      await js(bubble, "boxagent.desktopAction('dismiss-feedback')");
      await wait(() => bubble.getBounds().height < 340, "empty chat shrinks");
      assert(
        await js(
          bubble,
          "document.querySelector('.companion-ambient') && getComputedStyle(document.querySelector('.companion-ambient i')).animationName==='ambient-drift'",
        ),
        "ambient layer",
      );
      fs.writeFileSync(
        path.join(root, "artifacts/electron-desktop/layout.json"),
        JSON.stringify({
          compact: bubble.getBounds().height,
          maximum: 600,
          width: bubble.getBounds().width,
        }),
      );

      fs.writeFileSync(
        path.join(
          root,
          "artifacts/electron-desktop/companion-compact-dark.png",
        ),
        (await bubble.webContents.capturePage()).toPNG(),
      );
      await js(
        bubble,
        "localStorage.setItem('boxagent.interface-preferences',JSON.stringify({theme:'light',language:'zh-CN'}));window.dispatchEvent(new StorageEvent('storage',{key:'boxagent.interface-preferences'}))",
      );
      await new Promise((r) => setTimeout(r, 200));
      assert(
        await js(
          bubble,
          "document.querySelector('.companion-session-button').getBoundingClientRect().right <= innerWidth - 8 && document.querySelector('.companion-shortcut').getBoundingClientRect().right <= document.querySelector('.companion-session-button').getBoundingClientRect().left",
        ),
        "Chinese footer buttons fit",
      );
      fs.writeFileSync(
        path.join(
          root,
          "artifacts/electron-desktop/companion-compact-light.png",
        ),
        (await bubble.webContents.capturePage()).toPNG(),
      );
      await js(bubble, "document.querySelector('textarea').focus()");
      assert(
        await js(bubble, "document.activeElement.tagName==='TEXTAREA'"),
        "input focus",
      );
      // Drag uses real host dispatch; move the cursor through Electron's test stub.
      const electronScreen = require("electron").screen;
      const originalCursor = electronScreen.getCursorScreenPoint;
      let point = { x: 200, y: 200 };
      electronScreen.getCursorScreenPoint = () => point;
      const originalFrame = pet.getBounds();
      await js(pet, "boxagent.desktopAction('drag','start')");
      await new Promise((r) => setTimeout(r, 50));
      point = { x: 230, y: 215 };
      await js(pet, "boxagent.desktopAction('drag','move')");
      await new Promise((r) => setTimeout(r, 50));
      assert.equal(pet.getBounds().x - originalFrame.x, 30);
      assert.equal(pet.getBounds().y - originalFrame.y, 15);
      await js(pet, "boxagent.desktopAction('drag','end')");
      await new Promise((r) => setTimeout(r, 50));
      electronScreen.getCursorScreenPoint = originalCursor;
      assert.equal(
        JSON.parse(fs.readFileSync(path.join(data, "position.json"))).version,
        2,
      );
      bubble.close();
      assert(!bubble.isVisible());
      pet.webContents.sendInputEvent({
        type: "mouseDown",
        x: 20,
        y: 20,
        button: "left",
        clickCount: 1,
      });
      pet.webContents.sendInputEvent({
        type: "mouseUp",
        x: 20,
        y: 20,
        button: "left",
        clickCount: 1,
      });
      await wait(() => bubble.isVisible(), "pet tap toggles bubble");
      bubble.hide();
      const reopened = new Promise((resolve) =>
        app.once("second-instance", resolve),
      );
      const child = spawn(process.execPath, [__filename, "--second-open"], {
        env: { ...process.env, BOXAGENT_DESKTOP_FIXTURE: fixture },
        stdio: "ignore",
      });
      await reopened;
      assert(bubble.isVisible());
      assert.equal(BrowserWindow.getAllWindows().length, 2);
      child.on("exit", () => {});
      console.log(
        "Electron desktop: real VRM ready/crop/scale, failed avatar rollback, lazy console, shared theme/language, history/Markdown/latest, text/approval/cancel/voice IPC, bubble close/tap/reopen, single instance passed",
      );
      clearTimeout(timeout);
      app.quit();
    } catch (error) {
      console.error(error);
      clearTimeout(timeout);
      app.exit(1);
    }
  });
});
app.on("will-quit", () => {
  assert(
    fs.existsSync(path.join(data, "service-closed")),
    "service EOF cleanup",
  );
  for (const socket of sockets) socket.destroy();
  server.close();
  fs.rmSync(fixture, { recursive: true, force: true });
});
