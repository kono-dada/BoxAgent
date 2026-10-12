const {
  BrowserWindow,
  screen,
  Menu,
  Tray,
  nativeImage,
  globalShortcut,
  shell,
  Notification,
} = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const {
  clampFrame,
  petSize,
  validBounds,
  bubbleFrame,
  presentation,
} = require("./layout.cjs");
class DesktopHost {
  constructor({
    app,
    service,
    client,
    data,
    showPanel,
    broadcast,
    registerWindow,
  }) {
    Object.assign(this, {
      app,
      service,
      client,
      data,
      showPanel,
      broadcast,
      registerWindow,
    });
    this.state = {};
    this.snapshot = {};
    this.windows = new Set();
    this.bubbleOpen = false;
    this.bubbleHeight = 270;
    this.drag = null;
    this.current = null;
    this.pending = null;
    this.closed = false;
    this.offset = null;
    this.notified = new Set();
    this.engineReady = false;
    this.bubbleReady = false;
    this.loading = false;
  }
  createWindow(options, page) {
    const window = new BrowserWindow({
      show: false,
      frame: false,
      transparent: true,
      backgroundColor: "#00000000",
      alwaysOnTop: true,
      skipTaskbar: true,
      hasShadow: false,
      resizable: false,
      fullscreenable: false,
      webPreferences: {
        preload: path.join(__dirname, "../preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: false,
      },
      ...options,
    });
    this.registerWindow(window);
    this.windows.add(window);
    window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    window.setAlwaysOnTop(true, "floating");
    window.on("closed", () => this.windows.delete(window));
    window.loadFile(path.join(__dirname, `../dist/${page}.html`));
    return window;
  }
  async start() {
    this.snapshot = await this.service.request("desktop_snapshot");
    this.bubble = this.createWindow(
      {
        width: 440,
        height: 270,
        hasShadow: false,
        acceptFirstMouse: true,
        title: "BoxAgent · 对话",
      },
      "companion",
    );
    this.bubble.on("close", (event) => {
      if (!this.closed) {
        event.preventDefault();
        this.hideBubble();
      }
    });
    this.bubble.webContents.on("did-finish-load", () => {
      this.bubbleReady = true;
      this.sendDesktop();
      if (this.engineReady) void this.recoverNotifications();
    });

    const icon = nativeImage.createFromPath(
      path.join(__dirname, "../public/brand/trayTemplate.png"),
    );
    icon.setTemplateImage(true);
    this.tray = new Tray(icon);
    this.tray.setToolTip(`BoxAgent · ${this.snapshot.name} · ⌃⌥空格切换麦克风`);
    this.menu = Menu.buildFromTemplate([
      { label: "伙伴控制台…", click: this.showPanel },
      {
        label: "新建会话…",
        click: () => {
          this.showBubble();
          this.bubble.webContents.send("desktop:new-session");
        },
      },
      { type: "separator" },
      { label: "退出 BoxAgent", click: () => this.app.quit() },
    ]);
    this.tray.setContextMenu(this.menu);
    this.tray.on("click", () => this.toggleBubble());
    if (
      !globalShortcut.register("Control+Alt+Space", () =>
        this.client
          .request("toggle_voice")
          .catch((error) => this.feedback(error.message)),
      )
    )
      this.feedback("快捷键已被占用，请使用小窗的语音按钮");
    this.pointerTimer = setInterval(() => this.pointer(), 40);
    await this.loadAvatar(this.snapshot.appearance, false);
  }
  get area() {
    return screen.getDisplayNearestPoint(
      this.current?.origin || screen.getCursorScreenPoint(),
    ).workArea;
  }
  sendDesktop() {
    const value = {
      ...this.snapshot,
      bubble_open: this.bubbleOpen,
      appearance_ready: Boolean(this.current?.ready),
      switching: Boolean(this.pending),
      appearance_error: this.error || "",
    };
    this.broadcast("desktop:state", value);
    return value;
  }
  dismissFeedback() {
    this.snapshot.feedback = "";
    this.sendDesktop();
  }
  feedback(message) {
    this.snapshot.feedback = message;
    this.sendDesktop();
    this.showBubble();
  }
  async loadAvatar(identity, persist = true) {
    if (this.pending || this.loading) throw Error("形象仍在加载，请稍后重试");
    this.loading = true;
    let prepared;
    try {
      prepared = await this.service.request("desktop_prepare_avatar", {
        id: identity,
      });
    } finally {
      this.loading = false;
    }
    if (this.closed) {
      await this.service.request("desktop_cancel_avatar", {
        token: prepared.token,
      });
      throw Error("桌宠已关闭");
    }
    let area = this.area,
      viewport = petSize(this.snapshot.scale || 1, area);
    let origin = this.current?.origin || {
      x: area.x + area.width - viewport.width - 26,
      y: area.y + area.height - viewport.height - 30,
    };
    if (!this.current) {
      try {
        const saved = JSON.parse(
          fs.readFileSync(path.join(this.data, "position.json"), "utf8"),
        );
        if (
          Array.isArray(saved) &&
          saved.length === 2 &&
          saved.every(Number.isFinite)
        ) {
          // Existing AppKit coordinates use the lower left of the primary display.
          const primary = screen.getPrimaryDisplay().bounds;
          origin = {
            x: saved[0],
            y: primary.height - saved[1] - viewport.height,
          };
        } else if (
          saved?.version === 2 &&
          Number.isFinite(saved.x) &&
          Number.isFinite(saved.y)
        )
          origin = saved;
      } catch (error) {
        if (error.code !== "ENOENT" && !(error instanceof SyntaxError))
          this.feedback(error.message);
      }
    }
    area = screen.getDisplayNearestPoint({
      x: Math.round(origin.x),
      y: Math.round(origin.y),
    }).workArea;
    viewport = petSize(this.snapshot.scale || 1, area);
    origin = clampFrame({ ...origin, ...viewport }, area);
    const window = this.createWindow({ ...origin, focusable: false }, "pet");
    const candidate = {
      window,
      viewport,
      origin,
      crop: { x: 0, y: 0, ...viewport },
      ready: false,
      ...prepared,
      persist,
    };
    this.pending = candidate;
    this.sendDesktop();
    const loaded = new Promise((resolve, reject) => {
      candidate.resolve = resolve;
      candidate.reject = reject;
    });
    candidate.timer = setTimeout(
      () => this.failAvatar(candidate, "模型加载超时"),
      30000,
    );
    window.webContents.on("did-finish-load", () => this.sendPet(candidate));
    window.webContents.on("render-process-gone", () =>
      this.failAvatar(candidate, "角色渲染进程已退出"),
    );
    return loaded;
  }
  sendPet(pet) {
    if (!pet.window.isDestroyed())
      pet.window.webContents.send("desktop:pet", {
        url: pet.url,
        viewport: pet.viewport,
        crop: pet.crop,
        state: presentation(this.state),
        pointer: this.pointerValue || [0, 0],
      });
  }
  async report(sender, message) {
    const pet = [this.pending, this.current].find(
      (p) => p?.window.webContents.id === sender.id,
    );
    if (!pet || !message || typeof message !== "object") return;
    if (message.type === "bounds") {
      const bounds = validBounds(message, pet.viewport);
      if (!bounds || this.drag?.pet) return;
      pet.crop = {
        x: bounds.x,
        y: pet.viewport.height - bounds.y - bounds.height,
        width: bounds.width,
        height: bounds.height,
      };
      pet.window.setBounds({
        x: Math.round(pet.origin.x + pet.crop.x),
        y: Math.round(pet.origin.y + pet.crop.y),
        width: Math.round(pet.crop.width),
        height: Math.round(pet.crop.height),
      });
      this.sendPet(pet);
    } else if (message.type === "ready" && pet === this.pending) {
      try {
        const snapshot = await this.service.request("desktop_commit_avatar", {
          token: pet.token,
          persist: pet.persist,
        });
        if (this.closed || this.pending !== pet) return;
        clearTimeout(pet.timer);
        const previous = this.current;
        this.current = pet;
        this.pending = null;
        pet.ready = true;
        this.snapshot = { ...this.snapshot, ...snapshot };
        if (this.snapshot.feedback === this.error) this.snapshot.feedback = "";
        this.error = "";
        pet.window.showInactive();
        previous?.window.destroy();
        this.positionBubble();
        this.sendDesktop();
        pet.resolve();
      } catch (error) {
        this.failAvatar(pet, error.message);
      }
    } else if (message.type === "error")
      this.failAvatar(pet, String(message.message || "模型加载失败"));
  }
  failAvatar(pet, message) {
    if (pet !== this.pending && pet !== this.current) return;
    const feedback = this.avatarFailure(
      message,
      pet === this.pending && Boolean(this.current?.ready),
    );
    clearTimeout(pet.timer);
    if (pet === this.pending) {
      this.pending = null;
      pet.window.destroy();
      this.service
        .request("desktop_cancel_avatar", { token: pet.token })
        .catch(() => {});
      pet.reject(Error(feedback));
    }
    this.feedback(feedback);
  }
  avatarFailure(detail, preserved) {
    try {
      const directory = path.join(this.data, "logs");
      fs.mkdirSync(directory, { recursive: true });
      fs.appendFileSync(
        path.join(directory, "desktop-service.log"),
        JSON.stringify({
          timestamp: new Date().toISOString(),
          event: "avatar.load_failed",
          detail,
        }) + "\n",
      );
    } catch (error) {
      console.error("Avatar load failed:", detail, error);
    }
    this.error = preserved
      ? "形象加载失败，已保留当前形象。请检查模型文件或选择其他形象。"
      : "形象加载失败，请在设置中选择其他形象。";
    return this.error;
  }
  rendererReady(sender) {
    const pet = [this.pending, this.current].find(
      (p) => p?.window.webContents.id === sender.id,
    );
    if (pet) this.sendPet(pet);
  }
  pointer() {
    if (!this.current?.ready) return;
    const point = screen.getCursorScreenPoint(),
      pet = this.current;
    this.pointerValue = [
      Math.round(point.x - pet.origin.x - pet.viewport.width / 2),
      Math.round(pet.origin.y + pet.viewport.height / 2 - point.y),
    ];
    this.sendPet(pet);
  }
  positionBubble(preserveTop = false) {
    if (!this.bubble || !this.current) return;
    const frame = bubbleFrame(
      { ...this.current.origin, ...this.current.viewport },
      this.area,
      this.offset,
      this.bubbleHeight,
    );
    // Animation crop is not a placement anchor; content growth keeps the header still.
    if (preserveTop) {
      const previous = this.bubble.getBounds();
      frame.x = previous.x;
      frame.y = previous.y;
    }
    this.bubble.setBounds(
      clampFrame(frame, {
        x: this.area.x + 8,
        y: this.area.y + 8,
        width: this.area.width - 16,
        height: this.area.height - 16,
      }),
    );
  }
  resizeBubble(sender, height) {
    if (this.bubble?.webContents.id !== sender.id || !Number.isFinite(height))
      return;
    const requested = Math.round(Math.max(228, Math.min(600, height)));
    if (Math.abs(requested - this.bubbleHeight) < 2) return;
    this.bubbleHeight = requested;
    this.positionBubble(this.bubbleOpen);
  }
  showBubble() {
    if (!this.bubble) return;
    this.bubbleOpen = true;
    this.positionBubble();
    this.bubble.showInactive();
    this.sendDesktop();
  }
  hideBubble() {
    this.bubbleOpen = false;
    this.bubble?.hide();
    this.sendDesktop();
  }
  toggleBubble() {
    this.bubbleOpen ? this.hideBubble() : this.showBubble();
  }
  dragWindow(sender, phase) {
    const pet = this.current?.window.webContents.id === sender.id,
      bubble = this.bubble?.webContents.id === sender.id;
    if (!pet && !bubble) return;
    const point = screen.getCursorScreenPoint(),
      window = pet ? this.current.window : this.bubble;
    if (phase === "start") {
      this.drag = {
        pet,
        start: point,
        frame: window.getBounds(),
        origin: this.current.origin,
        last: point,
        time: Date.now(),
        moved: false,
      };
      return;
    }
    const drag = this.drag;
    if (!drag || drag.pet !== pet) return;
    const dx = point.x - drag.start.x,
      dy = point.y - drag.start.y;
    if (phase === "move") {
      if (Math.abs(dx) + Math.abs(dy) < 3 && !drag.moved) return;
      if (pet && !drag.moved)
        window.webContents.send("desktop:interact", {
          kind: "drag-start",
          velocity: [0, 0],
        });
      drag.moved = true;
      window.setPosition(
        Math.round(drag.frame.x + dx),
        Math.round(drag.frame.y + dy),
      );
      if (pet) {
        this.current.origin = { x: drag.origin.x + dx, y: drag.origin.y + dy };
        const now = Date.now(),
          dt = Math.max(1, now - drag.time) / 1000;
        window.webContents.send("desktop:interact", {
          kind: "drag-move",
          velocity: [
            (point.x - drag.last.x) / dt,
            -(point.y - drag.last.y) / dt,
          ],
        });
        drag.last = point;
        drag.time = now;
        this.positionBubble();
      }
    } else if (phase === "end") {
      this.drag = null;
      if (pet) {
        window.webContents.send("desktop:interact", {
          kind: drag.moved ? "drag-end" : "tap",
          velocity: [0, 0],
        });
        if (!drag.moved) this.toggleBubble();
        const frame = clampFrame(window.getBounds(), this.area);
        window.setBounds(frame);
        this.current.origin = {
          x: frame.x - this.current.crop.x,
          y: frame.y - this.current.crop.y,
        };
        this.savePosition();
        this.positionBubble();
      } else {
        const defaultFrame = bubbleFrame(
          { ...this.current.origin, ...this.current.viewport },
          this.area,
          null,
          this.bubbleHeight,
        );
        const actual = window.getBounds();
        this.offset = {
          x: actual.x - defaultFrame.x,
          y: actual.y - defaultFrame.y,
        };
      }
    }
  }
  savePosition() {
    const value = {
      version: 2,
      x: this.current.origin.x,
      y: this.current.origin.y,
    };
    fs.mkdirSync(this.data, { recursive: true });
    const file = path.join(this.data, "position.json");
    fs.writeFileSync(file + ".tmp", JSON.stringify(value));
    fs.renameSync(file + ".tmp", file);
  }
  stateChanged(state, event) {
    if (/Engine (?:未连接|已断开)|^connect (?:ENOENT|ECONNREFUSED)/.test(this.snapshot.feedback || "")) {
      this.snapshot.feedback = "";
      this.sendDesktop();
    }
    this.state = { ...this.state, ...state };
    if (this.current) this.sendPet(this.current);
    if (event?.type === "engine.ready") {
      this.engineReady = true;
      if (this.bubbleReady) void this.recoverNotifications();
    }
    if (
      [
        "front.user_text",
        "front.assistant_text",
        "task.approval",
        "task.succeeded",
        "task.failed",
        "task.blocked",
        "voice.error",
        "notification.pending",
        "notification.system_requested",
      ].includes(event?.type)
    )
      this.showBubble();
    if (event?.type === "notification.system_requested")
      this.notify(state.notification_id, state.notification_text);
  }
  async recoverNotifications() {
    try {
      for (const row of await this.client.request("list_notifications", {
        pending_only: true,
      }))
        this.notify(row.notification_id, row.summary);
    } catch (error) {
      this.feedback(error.message);
    }
  }
  notify(id, text) {
    this.showBubble();
    if (!id || !this.bubbleReady || this.notified.has(id)) return;
    this.notified.add(id);
    let channel = "ui";
    if (Notification.isSupported()) {
      new Notification({
        title: `${this.snapshot.name} 任务提醒`,
        body: text || "后台任务已经结束",
      }).show();
      channel = "system";
    }
    this.client
      .request("acknowledge_notification", {
        notification_id: id,
        channel,
        receipt: channel === "system" ? "submitted" : "presented",
      })
      .catch((error) => {
        this.notified.delete(id);
        this.feedback(error.message);
      });
  }
  async request(method, params = {}) {
    if (method === "desktop_snapshot") {
      this.snapshot = {
        ...this.snapshot,
        ...(await this.service.request(method)),
      };
      return this.sendDesktop();
    }
    if (method === "desktop_avatars")
      return { ...(await this.service.request(method)), ...this.sendDesktop() };
    if (method === "desktop_select_avatar") {
      try {
        await this.loadAvatar(params.id);
      } catch (error) {
        if (error.message === this.error || this.pending || this.loading)
          throw error;
        const feedback = this.avatarFailure(
          error.message,
          Boolean(this.current?.ready),
        );
        this.feedback(feedback);
        throw Error(feedback);
      }
      return this.sendDesktop();
    }
    if (method === "desktop_toggle_bubble") {
      this.toggleBubble();
      return this.sendDesktop();
    }
    if (method === "desktop_quit") {
      setTimeout(() => this.app.quit(), 100);
      return { status: "stopping" };
    }
    if (method === "desktop_open_logs" || method === "desktop_open_run") {
      const result = await this.service.request(
        method === "desktop_open_logs"
          ? "desktop_log_directory"
          : "desktop_run_directory",
        params,
      );
      const error = await shell.openPath(result.path);
      if (error) throw Error(error);
      return { status: "succeeded" };
    }
    if (method === "desktop_set_pet_scale" && (this.pending || this.loading))
      throw Error("形象仍在加载，请稍后调整大小");
    const result = await this.service.request(method, params);
    if (method === "desktop_set_pet_scale") {
      this.snapshot = { ...this.snapshot, ...result };
      const pet = this.current;
      if (pet) {
        const size = petSize(result.scale, this.area);
        pet.origin.x += (pet.viewport.width - size.width) / 2;
        pet.origin.y += pet.viewport.height - size.height;
        pet.viewport = size;
        pet.crop = { x: 0, y: 0, ...size };
        pet.origin = clampFrame({ ...pet.origin, ...size }, this.area);
        pet.window.setBounds(pet.origin);
        this.sendPet(pet);
        this.savePosition();
        this.positionBubble();
      }
      return this.sendDesktop();
    }
    if (method === "desktop_save_persona") {
      this.snapshot.name = result.name;
      this.sendDesktop();
    }
    return result;
  }
  close() {
    this.closed = true;
    clearInterval(this.pointerTimer);
    globalShortcut.unregisterAll();
    this.tray?.destroy();
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.reject(Error("桌宠已关闭"));
    }
    for (const window of this.windows)
      if (!window.isDestroyed()) window.destroy();
  }
}
module.exports = { DesktopHost };
