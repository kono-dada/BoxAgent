// Full production Electron host + real VRM/resource service, isolated fake Engine.
const { app, BrowserWindow, nativeImage } = require("electron");
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
const preview = process.argv.includes("--preview");
fs.mkdirSync(path.join(fixture, "data"), { recursive: true });
const data = fs.realpathSync(path.join(fixture, "data"));
process.env.BOXAGENT_DATA_DIR = data;
process.env.BOXAGENT_ELECTRON_USER_DATA = path.join(fixture, "profile");
const python = path.join(fixture, "python");
if (!second) {
  const runtime = process.env.BOXAGENT_TEST_PYTHON || path.join(root, ".venv/bin/python3");
  fs.writeFileSync(
    python,
    `#!/bin/sh\nexec '${runtime}' '${root}/tests/fixtures/electron_desktop.py'\n`,
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
          result = { status: "cancelled" };
          { const next = state.task_queue?.shift();
            emit(next ? "task.accepted" : "task.cancelled", { task: next ? "running" : "cancelled",
              ...(next ? { task_goal: next.goal, task_id: next.task_id } : {}) }); }
          break;
        case "cancel_all_tasks":
          result = { status: "cancelled" };
          emit("task.cancelled", { task: "cancelled", task_queue: [] });
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
const js = async (window, source) => {
  try { return await window.webContents.executeJavaScript(source); }
  catch (error) { throw new Error(`Renderer script failed (${window.webContents.getURL()}): ${source}`, { cause: error }); }
};
const dragBy = async (window, dx, dy, inspect = () => {}) => {
  const bounds = window.getBounds(), start = { x: bounds.x + 24, y: bounds.y + 24 };
  const point = { x: start.x + dx, y: start.y + dy };
  const sendDrag = message => js(window, `boxagent.desktopAction('drag',${JSON.stringify(message)})`);
  await sendDrag({ phase: 'start', buttons: 1, point: start });
  try {
    await sendDrag({ phase: 'move', buttons: 1, point });
    await js(window, "boxagent.request('desktop_snapshot')");
    await inspect();
  } finally {
    await sendDrag({ phase: 'end', buttons: 0, point });
    await js(window, "boxagent.request('desktop_snapshot')");
  }
};
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
      if (preview) {
        events = [
          {event_id:'preview-user',type:'message.final',role:'user',content:'帮我收藏这首歌，后面再打开计算器。'},
          {event_id:'preview-reply',type:'message.final',role:'assistant',content:'好，我来处理。你可以继续和我聊。'},
        ];
        emit('front.user_text', {task:'running',task_id:'preview-a',task_goal:'示例 · 收藏当前歌曲',
          task_text:'正在确认收藏状态…',task_started_at:Date.now()/1000-12,task_activity_at:Date.now()/1000,
          task_queue:[{task_id:'preview-b',goal:'示例 · 打开计算器'},{task_id:'preview-c',goal:'示例 · 计算二的五次方'}]});
        await js(bubble, "boxagent.desktopAction('show-tasks')");
        await wait(() => BrowserWindow.getAllWindows().some(w => w.webContents.getURL().endsWith('/tasks.html')), 'task preview');
        const tasks = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith('/tasks.html'));
        await wait(() => js(tasks, "Boolean(document.querySelector('.task-float'))"), 'preview renderer');
        await js(tasks, "boxagent.desktopAction('pin-tasks')");
        await js(bubble, "localStorage.setItem('boxagent.interface-preferences',JSON.stringify({theme:'dark',language:'zh-CN'}));window.dispatchEvent(new StorageEvent('storage',{key:'boxagent.interface-preferences'}))");
        const area = require('electron').screen.getPrimaryDisplay().workArea;
        const chatX = area.x + Math.round((area.width - 440) / 2) + 160;
        await dragBy(bubble, chatX - bubble.getBounds().x, area.y + 110 - bubble.getBounds().y);
        await wait(() => tasks.getBounds().y === bubble.getBounds().y, 'linked preview windows');
        bubble.setTitle('BoxAgent · 前端预览');tasks.setTitle('BoxAgent · 任务前端预览');
        await js(tasks, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
        await new Promise(resolve => setTimeout(resolve, 200));
        fs.writeFileSync(path.join(fixture, 'tasks-preview.png'), (await tasks.webContents.capturePage()).toPNG());
        console.log('任务浮层布局：' + JSON.stringify(await js(tasks, `(() => {
          const body = document.querySelector('.task-float-body');
          const panel = body.getBoundingClientRect();
          return { width: innerWidth, height: innerHeight, scrollHeight: body.scrollHeight, visibleHeight: body.clientHeight,
            rowsFit: [...document.querySelectorAll('.task-queue li')].every(row => row.getBoundingClientRect().bottom <= panel.bottom) };
        })()`)));
        await js(tasks, "boxagent.desktopAction('collapse-tasks')");
        await wait(() => !tasks.isVisible(), 'preview summary replaces separate pill');
        const windows = [bubble, pet], frames = windows.map(window => window.getBounds());
        const gap = frames[1].y - frames[0].y - frames[0].height;
        assert(gap >= -20 && gap <= 48, 'preview has no stale gap below chat: ' + gap);
        const x = Math.min(...frames.map(frame => frame.x)), y = Math.min(...frames.map(frame => frame.y));
        const width = Math.max(...frames.map(frame => frame.x + frame.width)) - x;
        const height = Math.max(...frames.map(frame => frame.y + frame.height)) - y;
        const bitmap = Buffer.alloc(width * height * 4);
        for (const [index, window] of windows.entries()) {
          const frame = frames[index];
          const pixels = (await window.webContents.capturePage()).resize({width:frame.width,height:frame.height}).toBitmap();
          for (let row = 0; row < frame.height; row++) pixels.copy(bitmap,
            ((frame.y - y + row) * width + frame.x - x) * 4, row * frame.width * 4, (row + 1) * frame.width * 4);
        }
        fs.writeFileSync(path.join(fixture, 'group-preview.png'), nativeImage.createFromBitmap(bitmap, {width,height}).toPNG());
        fs.writeFileSync(path.join(fixture, 'chat-preview.png'), (await bubble.webContents.capturePage()).toPNG());
        console.log('预览截图目录：' + fixture);
        console.log('窗口位置与角色间距：' + JSON.stringify({chat:frames[0],pet:frames[1],gap}));
        await js(bubble, "boxagent.desktopAction('show-tasks')");
        await wait(() => tasks.isVisible(), 'preview direct task controls');
        clearTimeout(timeout);
        console.log('前端预览已打开：任务操作直接显示在面板上，标题栏保留状态入口；示例任务不执行桌面操作。');
        return;
      }
      const initial = await js(bubble, "boxagent.request('desktop_snapshot')");
      assert(initial.appearance_ready);
      assert(pet.getBounds().width < 252, "crop removes transparent area");
      const fixedPetFrame = pet.getBounds();
      const animation = pet.webContents.mainFrame.frames[0];
      const firstBounds = await animation.executeJavaScript("pet.bounds().bounds");
      assert.equal(fixedPetFrame.width, firstBounds.width, "首帧决定紧凑窗口宽度");
      assert.equal(fixedPetFrame.height, firstBounds.height, "首帧决定紧凑窗口高度");
      const beforeAnimation = await animation.executeJavaScript("pet.debug()");
      await new Promise((r) => setTimeout(r, 2000));
      const afterAnimation = await animation.executeJavaScript("pet.debug()");
      assert(afterAnimation.frames > beforeAnimation.frames, "真实动画仍在播放");
      assert.equal(afterAnimation.windowSizing.checks, beforeAnimation.windowSizing.checks,
        "日常动画不再扫描边界");
      assert.deepEqual(pet.getBounds(), fixedPetFrame, "动画播放期间窗口保持首帧边界");
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
      const dockedPetFrame = pet.getBounds();
      for (const x of [12, 40, 20]) {
        await js(
          pet,
          `boxagent.desktopAction('pet-report',{type:'bounds',viewport:[252,182],bounds:{x:${x},y:18,width:170,height:150}})`,
        );
        await new Promise((r) => setTimeout(r, 70));
        assert.deepEqual(pet.getBounds(), dockedPetFrame, "后续边界报告不能移动或缩放已停靠的桌宠窗口");
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
        task_goal: "当前示例目标",
        task_queue: [{task_id:'b',goal:'第二个目标'},{task_id:'c',goal:'第三个目标'}],
      });
      await wait(
        () => BrowserWindow.getAllWindows().some(w => w.webContents.getURL().endsWith('/tasks.html')),
        "independent task panel",
      );
      const tasks = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith('/tasks.html'));
      await wait(() => js(tasks, "Boolean(document.querySelector('.approval-card'))"), "approval");
      assert.equal(await js(bubble, "Boolean(document.querySelector('.task-status'))"), false);
      assert.equal(await js(tasks, "document.querySelectorAll('.task-queue li').length"), 2);
      assert.equal(tasks.getBounds().width, 320);
      assert.equal(tasks.getBounds().height, 280);
      assert(await js(tasks, `(() => {
        const footer = document.querySelector('.task-float-footer').getBoundingClientRect();
        const actions = document.querySelector('.task-actions').getBoundingClientRect();
        return [...document.querySelectorAll('.approval-card button')]
          .every(button => button.getBoundingClientRect().bottom <= actions.top)
          && [...document.querySelectorAll('.task-actions button')].every(button => {
            const rect = button.getBoundingClientRect();
            return rect.right <= innerWidth - 8 && rect.bottom <= footer.top;
          });
      })()`), 'approval controls remain visible in compact panel');
      assert.equal(await js(bubble, "document.querySelectorAll('.companion-header .icon-button').length"), 2, 'chat keeps console and close controls');
      assert.equal(await js(tasks, "Boolean(document.querySelector('.task-menu'))"), false, 'task controls are direct buttons');
      await js(tasks, "document.querySelector('.task-actions [aria-pressed]').click()");
      await wait(() => js(tasks, "document.querySelector('[aria-pressed]').getAttribute('aria-pressed')==='true'"), 'task pin');
      const relation = () => ({ x: tasks.getBounds().x - bubble.getBounds().x,
        y: tasks.getBounds().y - bubble.getBounds().y });
      const positions = () => [tasks, bubble].map(window => { const {x,y} = window.getBounds(); return {x,y}; });
      const attached = relation();
      assert.equal(attached.y, 0, 'task and chat headers align');
      const area = require('electron').screen.getDisplayMatching(bubble.getBounds()).workArea;
      const groupedWindows = [pet, bubble, tasks];
      const boxes = groupedWindows.map(window => window.getBounds());
      const left = Math.min(...boxes.map(box => box.x)), top = Math.min(...boxes.map(box => box.y));
      const width = Math.max(...boxes.map(box => box.x + box.width)) - left;
      const height = Math.max(...boxes.map(box => box.y + box.height)) - top;
      await dragBy(bubble, area.x + Math.round((area.width - width) / 2) - left,
        area.y + Math.round((area.height - height) / 2) - top);
      for (const driver of [bubble, tasks]) {
        const before = groupedWindows.map(window => window.getBounds());
        await dragBy(driver, -18, -12, () => {
          for (const [index, window] of groupedWindows.entries()) {
            assert.equal(window.getBounds().x - before[index].x, -18, 'drag carries every companion window x');
            assert.equal(window.getBounds().y - before[index].y, -12, 'drag carries every companion window y');
          }
        });
        assert.deepEqual(relation(), attached, 'bidirectional dragging preserves relative position');
      }
      for (const window of [tasks, bubble]) {
        const before = positions();
        window.webContents.sendInputEvent({ type:'mouseMove', x:24, y:24 });
        window.webContents.sendInputEvent({ type:'mouseMove', x:110, y:25 });
        await new Promise(resolve => setTimeout(resolve, 80));
        assert.deepEqual(positions(), before, 'hovering header does not move windows');
      }
      const beforePointerDrag = groupedWindows.map(window => window.getBounds());
      // Electron's injected inputs report screenX/Y as zero; supply their real window-relative coordinates.
      await js(tasks, "for(const type of ['pointerdown','pointermove','pointerup']) document.addEventListener(type,e=>{if(window.fixturePointer) {Object.defineProperty(e,'screenX',{value:fixturePointer.x});Object.defineProperty(e,'screenY',{value:fixturePointer.y});}},true)");
      const pointerInput = async event => {
        const frame = tasks.getBounds();
        await js(tasks, `window.fixturePointer=${JSON.stringify({x:frame.x+event.x,y:frame.y+event.y})}`);
        tasks.webContents.sendInputEvent(event);
        await new Promise(resolve => setTimeout(resolve, 40));
      };
      await pointerInput({ type:'mouseDown', x:24, y:24, button:'left', modifiers:['leftButtonDown'], clickCount:1 });
      await pointerInput({ type:'mouseMove', x:38, y:30, button:'left', modifiers:['leftButtonDown'] });
      await new Promise(resolve => setTimeout(resolve, 80));
      const pointerShift = { x: bubble.getBounds().x - beforePointerDrag[1].x, y: bubble.getBounds().y - beforePointerDrag[1].y };
      assert(pointerShift.x || pointerShift.y, 'renderer pointer gesture moves companion group');
      for (const [index, window] of groupedWindows.entries()) {
        assert.equal(window.getBounds().x - beforePointerDrag[index].x, pointerShift.x, 'pointer drag carries all windows x');
        assert.equal(window.getBounds().y - beforePointerDrag[index].y, pointerShift.y, 'pointer drag carries all windows y');
      }
      await pointerInput({ type:'mouseUp', x:290, y:24, button:'left', clickCount:1 });
      await new Promise(resolve => setTimeout(resolve, 80));
      const released = positions();
      await pointerInput({ type:'mouseMove', x:24, y:24 });
      await pointerInput({ type:'mouseMove', x:100, y:30 });
      await new Promise(resolve => setTimeout(resolve, 80));
      assert.deepEqual(positions(), released, 'release outside original target clears dragging');
      await pointerInput({ type:'mouseDown', x:24, y:24, button:'left', modifiers:['leftButtonDown'], clickCount:1 });
      await new Promise(resolve => setTimeout(resolve, 50));
      await js(tasks, "document.querySelector('.task-float-header').dispatchEvent(new PointerEvent('lostpointercapture',{pointerId:1}))");
      await pointerInput({ type:'mouseUp', x:24, y:24, button:'left', clickCount:1 });
      await new Promise(resolve => setTimeout(resolve, 50));
      const interrupted = positions();
      await pointerInput({ type:'mouseMove', x:120, y:25 });
      await new Promise(resolve => setTimeout(resolve, 80));
      assert.deepEqual(positions(), interrupted, 'lost capture stops dragging');
      const grouped = bubble.getBounds();
      await dragBy(tasks, area.x - 20 - tasks.getBounds().x, area.y - 20 - tasks.getBounds().y);
      await wait(() => bubble.getBounds().x !== grouped.x, 'linked pair reaches screen edge');
      assert.deepEqual(relation(), attached, 'screen clamp preserves relative position');
      for (const window of [bubble, tasks]) {
        const bounds = window.getBounds();
        assert(bounds.x >= area.x + 8 && bounds.y >= area.y + 8 &&
          bounds.x + bounds.width <= area.x + area.width - 8 &&
          bounds.y + bounds.height <= area.y + area.height - 8, 'both windows stay in work area: ' + JSON.stringify({bounds, area}));
      }
      await js(tasks, "boxagent.desktopAction('collapse-tasks')");
      await wait(() => !tasks.isVisible(), 'header summary replaces separate collapsed panel');
      assert(await js(bubble, "document.querySelector('.companion-task-summary-queue').textContent.includes('2')"), 'queue stays visible without floating pill');
      await js(bubble, "boxagent.desktopAction('show-tasks')");
      // 收起只隐藏浮层，尺寸仍是展开值；须等重新显示后再检查停靠位置。
      await wait(() => tasks.isVisible() && tasks.getBounds().height === 280, 'expand attached task panel');
      assert.deepEqual(relation(), attached, 'collapse and expand preserve docking side and spacing: ' + JSON.stringify({
        pet: pet.getBounds(), bubble: bubble.getBounds(), tasks: tasks.getBounds(), area,
      }));
      await js(tasks, "boxagent.desktopAction('pin-tasks')");
      await wait(() => js(tasks, "document.querySelector('[aria-pressed]')?.getAttribute('aria-pressed')==='true'"), 'pin reopened task panel');
      const taskBounds = tasks.getBounds();
      emit('task.progress', {task_text:'正在处理下一步'});
      await new Promise(resolve => setTimeout(resolve, 80));
      assert.deepEqual(tasks.getBounds(),taskBounds,'task progress does not move the floating panel');
      assert.equal(await js(bubble, "document.querySelector('.companion-task-summary-progress').textContent"), '允许写入测试文件？', 'approval takes precedence over progress');
      await js(bubble, "boxagent.desktopAction('hide-bubble')");
      assert(tasks.isVisible(),'task panel survives closing chat');
      await js(
        tasks,
        "document.querySelector('.approval-card button').click()",
      );
      await wait(
        () =>
          calls.some((r) => r.method === "answer_approval" && r.params.allowed),
        "approval sent",
      );
      await wait(() => js(tasks, "!document.querySelector('.approval-card')"), 'approval resolved');
      await wait(() => js(bubble, "document.querySelector('.companion-task-summary-progress').textContent==='正在处理下一步'"), 'live progress shows in header without opening tasks');
      await js(
        tasks,
        "Array.from(document.querySelectorAll('.task-actions button')).find(b=>b.textContent.trim()==='停止当前任务').click()",
      );
      await wait(
        () => calls.some((r) => r.method === "cancel_task"),
        "cancel sent",
      );
      await wait(() => js(tasks, "document.querySelector('.task-goal')?.textContent==='第二个目标' && document.querySelectorAll('.task-queue li').length===1"), 'queue advances in float');
      await js(tasks, "Array.from(document.querySelectorAll('.task-actions button')).find(b=>b.textContent.trim()==='取消全部任务').click()");
      await wait(() => js(tasks, "!document.querySelector('.task-queue')"), 'queue cleared in float');
      await js(tasks, "boxagent.desktopAction('collapse-tasks')");
      await wait(() => js(tasks, "Boolean(document.querySelector('.task-chip'))"), 'task pill');
      assert.equal(tasks.getBounds().height,54);
      const collapsedRelation = relation();
      const closedChat = bubble.getBounds();
      await js(bubble, "boxagent.request('desktop_toggle_bubble')");
      await wait(() => bubble.isVisible(), 'reopen linked chat');
      assert.equal(bubble.getBounds().x, closedChat.x, 'reopen preserves chat x');
      assert.equal(bubble.getBounds().y, closedChat.y, 'reopen preserves chat y');
      assert.deepEqual(relation(), collapsedRelation, 'reopen preserves task attachment');
      assert(!tasks.isVisible(), 'reopened chat removes duplicate floating pill');
      await js(bubble, "document.querySelector('.companion-task-summary').click()");
      await wait(() => tasks.isVisible() && tasks.getBounds().height === 280, 'header summary opens task list');
      await js(bubble, "document.querySelector('.companion-task-summary').click()");
      await wait(() => !tasks.isVisible(), 'header summary toggles list closed');
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
      await wait(() => BrowserWindow.getAllWindows().some(w => w.webContents.getURL().endsWith('/index.html')), "console");
      const panel = BrowserWindow.getAllWindows().find(
        (w) => w.webContents.getURL().endsWith('/index.html'),
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
      assert(await js(bubble, `(() => {
        const heading = document.querySelector('.companion-heading').getBoundingClientRect();
        const summary = document.querySelector('.companion-task-summary').getBoundingClientRect();
        const controls = document.querySelector('.companion-header .icon-button').getBoundingClientRect();
        return heading.right <= summary.left && summary.right <= controls.left && controls.right < innerWidth;
      })()`), 'English task summary fits between companion and controls');
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
      const scaledFrame = pet.getBounds();
      assert(Math.abs(scaledFrame.width - fixedPetFrame.width * 2.37) <= 2,
        "手动缩放保持首帧窗口比例");
      assert(Math.abs(scaledFrame.height - fixedPetFrame.height * 2.37) <= 2,
        "手动缩放保持首帧窗口比例");
      await animation.executeJavaScript("pet.preview('drag-left', .5); pet.preview('release', .5); pet.preview('speaking', 1); pet.resume()");
      await new Promise((r) => setTimeout(r, 100));
      assert.deepEqual(pet.getBounds(), scaledFrame, "拖拽、说话与诊断截图不会改变固定窗口");
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
      await js(bubble, "boxagent.request('desktop_set_pet_scale',{scale:1})");
      await wait(() => pet.getBounds().height < 182, 'restore normal avatar size');
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
      await js(bubble, "boxagent.desktopAction('show-tasks')");
      await wait(() => tasks.isVisible(), 'expanded task list during chat resize');
      const beforeCompact = { chat: bubble.getBounds(), pet: pet.getBounds() };
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
      await new Promise(resolve => setTimeout(resolve, 200));
      const compactChat = bubble.getBounds(), compactPet = pet.getBounds();
      const avatarGap = compactPet.y - compactChat.y - compactChat.height;
      assert(beforeCompact.chat.height - compactChat.height > 100, 'regression covers a substantial chat shrink');
      assert.equal(compactChat.y, beforeCompact.chat.y, 'shrinking chat keeps header in place');
      assert(beforeCompact.pet.y - compactPet.y > 100, 'avatar follows the shorter chat instead of leaving the old height empty');
      assert(avatarGap >= -20 && avatarGap <= 48, 'avatar stays close below compact chat: ' + avatarGap);
      assert.equal(tasks.getBounds().y, compactChat.y, 'task header stays aligned after chat shrinks');
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
      const originalFrame = pet.getBounds();
      await dragBy(pet, 30, 15, () => {
        assert.equal(pet.getBounds().x - originalFrame.x, 30);
        assert.equal(pet.getBounds().y - originalFrame.y, 15);
      });
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
      assert.equal(BrowserWindow.getAllWindows().length, 3);
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
