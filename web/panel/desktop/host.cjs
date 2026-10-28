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
const { targetDisplay, petAnchor, petPlacement, constrainPetOrigin, primaryBubbleFrame } = require('./displays.cjs');
const {
  clampFrame,
  petSize,
  validBounds,
  bubbleFrame,
  presentation,
  taskFrame,
  linkedFrames,
  clampFrames,
} = require("./layout.cjs");
class DesktopHost {
  constructor({
    app,
    service,
    client,
    data,
    showPanel,
    showCamera,
    broadcast,
    registerWindow,
  }) {
    Object.assign(this, {
      app,
      service,
      client,
      data,
      showPanel,
      showCamera,
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
    this.displayTarget = null;
    try {
      const saved = JSON.parse(fs.readFileSync(path.join(data, 'display.json'), 'utf8'));
      if (saved?.version === 1 && typeof saved.target === 'string' &&
        (saved.target === 'primary' || /^-?\d+$/.test(saved.target))) this.displayTarget = saved.target;
    } catch (error) {
      if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) console.error('Display preference:', error.message);
    }
    this.displayChanged = () => this.reconcileDisplays();
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
    this.updateMenu();
    for (const event of ['display-added', 'display-removed', 'display-metrics-changed'])
      screen.on(event, this.displayChanged);
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
  displayState() {
    const primary = screen.getPrimaryDisplay();
    const displays = screen.getAllDisplays();
    const active = this.displayTarget ? targetDisplay(displays, primary, this.displayTarget)
      : this.current ? screen.getDisplayMatching({ ...this.current.origin, ...this.current.viewport }) : primary;
    return {
      displays: displays.map(display => ({ id: String(display.id), label: display.label || String(display.id), primary: display.id === primary.id })),
      pet_display_id: String(active.id),
      separate_windows: Boolean(this.displayTarget),
    };
  }
  displayMenuItems() {
    const state = this.displayState();
    const enabled = Boolean(this.current?.ready) && !this.pending && !this.loading;
    return [
      { label: '小人与聊天同屏', type: 'radio', checked: !state.separate_windows, enabled,
        click: () => this.changeDisplayFromMenu(null) },
      ...state.displays.map(display => ({
        label: `${display.primary ? '主屏' : '副屏'} · ${display.label}`,
        type: 'radio', checked: state.separate_windows && state.pet_display_id === display.id, enabled,
        click: () => this.changeDisplayFromMenu(display.id),
      })),
      ...(state.displays.length < 2 ? [{ type: 'separator' }, { label: '未检测到扩展副屏', enabled: false }] : []),
    ];
  }
  changeDisplayFromMenu(id) {
    try { this.setPetDisplay(id); } catch (error) { this.feedback(error.message); }
  }
  showDisplayMenu(sender) {
    Menu.buildFromTemplate(this.displayMenuItems()).popup({ window: BrowserWindow.fromWebContents(sender) });
  }
  updateMenu() {
    if (!this.tray) return;
    const displayItems = this.displayMenuItems();
    const signature = JSON.stringify(displayItems);
    if (signature === this.menuSignature) return;
    this.menuSignature = signature;
    this.menu = Menu.buildFromTemplate([
      { label: "伙伴控制台…", click: this.showPanel },
      { label: "摄像头互动…", click: this.showCamera },
      { label: '小人显示屏', submenu: displayItems },
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
  }
  placePet(pet, display, center = false) {
    const placement = petPlacement(pet, this.snapshot.scale || 1, display.workArea, center, Boolean(this.displayTarget));
    if (placement.viewport.width !== pet.viewport.width || placement.viewport.height !== pet.viewport.height) {
      if (pet.dockInsetY !== undefined) pet.dockInsetY *= placement.viewport.height / pet.viewport.height;
    }
    Object.assign(pet, placement);
    pet.window.setBounds({ x: Math.round(pet.origin.x + pet.crop.x), y: Math.round(pet.origin.y + pet.crop.y),
      width: pet.crop.width, height: pet.crop.height });
    this.sendPet(pet);
  }
  setPetDisplay(id) {
    if (!this.current?.ready || this.pending || this.loading) throw Error('形象仍在加载，请稍后切换屏幕');
    const primary = screen.getPrimaryDisplay();
    const display = id === null ? primary : screen.getAllDisplays().find(item => String(item.id) === id);
    if (!display) throw Error('该显示器已断开，请重新选择');
    const target = id === null ? null : display.id === primary.id ? 'primary' : id;
    fs.mkdirSync(this.data, { recursive: true });
    const file = path.join(this.data, 'display.json');
    fs.writeFileSync(file + '.tmp', JSON.stringify({ version: 1, target }));
    fs.renameSync(file + '.tmp', file);
    this.cancelDisplayDrag();
    this.displayTarget = target;
    this.offset = null;
    this.taskSide = null;
    this.placePet(this.current, display, true);
    this.positionBubble();
    this.savePosition();
    return this.sendDesktop();
  }
  cancelDisplayDrag() {
    if (this.drag?.pet && this.current)
      this.current.window.webContents.send('desktop:interact', { kind: 'drag-end', velocity: [0, 0] });
    this.drag = null;
  }
  reconcileDisplays() {
    if (this.closed) return;
    this.cancelDisplayDrag();
    const primary = screen.getPrimaryDisplay(), displays = screen.getAllDisplays();
    for (const pet of [this.current, this.pending].filter(Boolean)) {
      const frame = pet.window.getBounds();
      const previous = screen.getDisplayMatching(frame);
      const display = this.displayTarget ? targetDisplay(displays, primary, this.displayTarget) : previous;
      this.placePet(pet, display, display.id !== previous.id);
    }
    this.taskSide = null;
    this.positionBubble();
    this.sendDesktop();
  }
  get area() {
    if (this.displayTarget)
      return targetDisplay(screen.getAllDisplays(), screen.getPrimaryDisplay(), this.displayTarget).workArea;
    return screen.getDisplayNearestPoint(
      this.current?.origin || screen.getCursorScreenPoint(),
    ).workArea;
  }
  sendDesktop() {
    const value = {
      ...this.snapshot,
      ...this.displayState(),
      bubble_open: this.bubbleOpen,
      tasks_expanded: Boolean(this.tasksExpanded),
      tasks_pinned: Boolean(this.tasksPinned),
      appearance_ready: Boolean(this.current?.ready),
      switching: Boolean(this.pending),
      appearance_error: this.error || "",
    };
    this.broadcast("desktop:state", value);
    this.updateMenu();
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
    let restoreAnchor = this.displayTarget && this.current?.hasBounds ? petAnchor(this.current) : null;
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
        ) {
          origin = saved;
          if (this.displayTarget && Number.isFinite(saved.anchor?.x) && Number.isFinite(saved.anchor?.y))
            restoreAnchor = saved.anchor;
        }
      } catch (error) {
        if (error.code !== "ENOENT" && !(error instanceof SyntaxError))
          this.feedback(error.message);
      }
    }
    const previousDisplay = screen.getDisplayNearestPoint({
      x: Math.round(origin.x),
      y: Math.round(origin.y),
    });
    area = this.displayTarget ? this.area : previousDisplay.workArea;
    viewport = petSize(this.snapshot.scale || 1, area);
    if (this.displayTarget) {
      const placement = petPlacement({ origin }, this.snapshot.scale || 1, area,
        previousDisplay.id !== targetDisplay(screen.getAllDisplays(), screen.getPrimaryDisplay(), this.displayTarget).id);
      origin = placement.origin;
      viewport = placement.viewport;
    }
    origin = clampFrame({ ...origin, ...viewport }, area);
    const window = this.createWindow({ ...origin, focusable: false }, "pet");
    const candidate = {
      window,
      viewport,
      origin,
      crop: { x: 0, y: 0, ...viewport },
      restoreAnchor,
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
      if (!bounds || this.drag) return;
      const firstBounds = !pet.hasBounds;
      pet.crop = {
        x: bounds.x,
        y: pet.viewport.height - bounds.y - bounds.height,
        width: bounds.width,
        height: bounds.height,
      };
      pet.hasBounds = true;
      // Capture layout padding once; animated crop changes must not move the chat.
      pet.dockInsetY ??= pet.crop.y;
      if (this.displayTarget) {
        const display = targetDisplay(screen.getAllDisplays(), screen.getPrimaryDisplay(), this.displayTarget);
        const area = display.workArea;
        if (firstBounds && pet.restoreAnchor) {
          pet.origin = { x: pet.restoreAnchor.x - pet.crop.x - pet.crop.width / 2,
            y: pet.restoreAnchor.y - pet.crop.y - pet.crop.height };
          pet.restoreAnchor = null;
        }
        // Calibrate once, then only shrink on overflow. Idle animation must not continuously change zoom.
        if (firstBounds || pet.crop.width > area.width - 16 || pet.crop.height > area.height - 16) {
          this.placePet(pet, display);
          if (pet === this.current) this.savePosition();
          return;
        }
        pet.origin = constrainPetOrigin(pet.origin, pet.crop, area);
      }
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
        if (this.displayTarget && pet.hasBounds) this.savePosition();
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
    if (this.displayTarget) {
      this.bubble.setBounds(primaryBubbleFrame(this.bubble.getBounds(), screen.getPrimaryDisplay().workArea, this.bubbleHeight));
      if (this.tasks?.isVisible()) this.positionTasks();
      return;
    }
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
    if (this.tasks?.isVisible()) this.positionTasks();
    else this.dockPetBelowBubble();
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
    this.syncTasksVisibility();
    this.positionBubble();
    this.bubble.showInactive();
    this.sendDesktop();
  }
  hideBubble() {
    this.bubbleOpen = false;
    this.bubble?.hide();
    if (!this.tasks && this.hasTaskStatus()) this.showTasks(false);
    else this.syncTasksVisibility();
    this.sendDesktop();
  }
  toggleBubble() {
    this.bubbleOpen ? this.hideBubble() : this.showBubble();
  }
  showTasks(expanded = true) {
    const changed = Boolean(this.tasksExpanded) !== expanded;
    this.tasksExpanded = expanded;
    if (!this.tasks || this.tasks.isDestroyed()) {
      this.tasks = this.createWindow({ width: 248, height: 54,
        title: "BoxAgent · 任务", acceptFirstMouse: true }, "tasks");
      this.tasks.webContents.on("did-finish-load", () => {
        this.tasks.webContents.send("engine:state", this.state);
        this.sendDesktop();
      });
      this.tasks.on("close", (event) => {
        if (!this.closed) { event.preventDefault(); this.collapseTasks(); }
      });
      this.tasks.on("blur", () => {
        setImmediate(() => {
          if (!this.closed && this.tasksExpanded && !this.tasksPinned && BrowserWindow.getFocusedWindow() !== this.bubble)
            this.collapseTasks();
        });
      });
    }
    this.syncTasksVisibility(changed);
    this.sendDesktop();
  }
  hasTaskStatus() {
    return Boolean((this.state.task && this.state.task !== "idle") || this.state.task_queue?.length);
  }
  syncTasksVisibility(reposition = false) {
    if (!this.tasks || this.tasks.isDestroyed()) return;
    if (this.tasksExpanded || (!this.bubbleOpen && this.hasTaskStatus())) {
      if (reposition || !this.tasks.isVisible()) this.positionTasks();
      this.tasks.showInactive();
    } else this.tasks.hide();
  }
  positionTasks() {
    const anchor = this.bubble
      ? this.bubble.getBounds()
      : this.current ? { ...this.current.origin, ...this.current.viewport }
      : { x: this.area.x + this.area.width - 280, y: this.area.y + this.area.height - 210, width: 252, height: 182 };
    const area = screen.getDisplayMatching(anchor).workArea;
    const frame = taskFrame(anchor, area, this.tasksExpanded);
    this.taskSide ||= anchor.x - frame.width - 12 >= area.x + 8 ? "left" : "right";
    frame.x = this.taskSide === "left" ? anchor.x - frame.width - 12 : anchor.x + anchor.width + 12;
    frame.y = anchor.y;
    this.placeCompanionWindows(anchor, frame, area);
  }
  placeCompanionWindows(bubble, tasks, area) {
    const frames = linkedFrames(bubble, tasks, {
      x: area.x + 8, y: area.y + 8, width: area.width - 16, height: area.height - 16,
    });
    this.bubble.setBounds(frames.bubble);
    this.tasks.setBounds(frames.tasks);
    if (this.current && !this.displayTarget) {
      const dx = frames.bubble.x - bubble.x, dy = frames.bubble.y - bubble.y;
      this.current.origin.x += dx;
      this.current.origin.y += dy;
      if (dx || dy) this.current.window.setPosition(
        Math.round(this.current.origin.x + this.current.crop.x),
        Math.round(this.current.origin.y + this.current.crop.y),
      );
      const base = bubbleFrame({ ...this.current.origin, ...this.current.viewport }, this.area, null, this.bubbleHeight);
      this.offset = { x: frames.bubble.x - base.x, y: frames.bubble.y - base.y };
    }
    this.dockPetBelowBubble();
  }
  dockPetBelowBubble() {
    if (this.displayTarget || !this.bubbleOpen || !this.current || this.drag) return;
    const pet = this.current, bubble = this.bubble.getBounds();
    const area = screen.getDisplayMatching(bubble).workArea;
    const origin = { ...pet.origin, ...pet.viewport,
      y: bubble.y + bubble.height + 12 - (pet.dockInsetY || 0) };
    const tasks = this.tasks?.isVisible() ? this.tasks.getBounds() : null;
    const frames = clampFrames([origin, bubble, ...(tasks ? [tasks] : [])],
      { x: area.x + 8, y: area.y + 8, width: area.width - 16, height: area.height - 16 });
    pet.origin = { x: frames[0].x, y: frames[0].y };
    pet.window.setPosition(Math.round(pet.origin.x + pet.crop.x), Math.round(pet.origin.y + pet.crop.y));
    this.bubble.setBounds(frames[1]);
    if (tasks) this.tasks.setBounds(frames[2]);
    const base = bubbleFrame({ ...pet.origin, ...pet.viewport }, this.area, null, this.bubbleHeight);
    this.offset = { x: frames[1].x - base.x, y: frames[1].y - base.y };
  }
  collapseTasks() {
    if (!this.tasks || this.tasks.isDestroyed()) return;
    this.tasksPinned = false;
    this.tasksExpanded = false;
    this.syncTasksVisibility(true);
    this.sendDesktop();
  }
  pinTasks() {
    this.tasksPinned = !this.tasksPinned;
    this.sendDesktop();
  }
  dragWindow(sender, message) {
    if (!this.current || !message || typeof message !== "object") return;
    let { phase } = message;
    const { point, buttons } = message;
    let windows = [this.current.window, this.bubble, this.tasks].filter(window => window && !window.isDestroyed());
    if (!windows.some(window => window.webContents.id === sender.id)) return;
    if (!["start", "move", "end", "cancel"].includes(phase)) return;
    if (phase === "move" && !(Number.isInteger(buttons) && (buttons & 1))) phase = "cancel";
    if (["start", "move"].includes(phase) && (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y))) return;
    const pet = this.current.window.webContents.id === sender.id;
    if (this.displayTarget) windows = windows.filter(window => pet ? window === this.current.window : window !== this.current.window);
    if (phase === "start") {
      if (!(Number.isInteger(buttons) && (buttons & 1))) return;
      this.drag = {
        sender: sender.id, pet,
        start: point,
        frames: windows.map(window => ({ window, frame: window.getBounds(), visible: window.isVisible(),
          limit: window === this.current.window && !this.displayTarget
            ? { ...this.current.origin, ...this.current.viewport } : window.getBounds() })),
        origin: { ...this.current.origin },
        last: point,
        time: Date.now(),
        moved: false,
      };
      return;
    }
    const drag = this.drag;
    if (!drag || drag.sender !== sender.id) return;
    if (phase === "move") {
      const dx = point.x - drag.start.x, dy = point.y - drag.start.y;
      if (Math.abs(dx) + Math.abs(dy) < 3 && !drag.moved) return;
      if (pet && !drag.moved)
        this.current.window.webContents.send("desktop:interact", {
          kind: "drag-start",
          velocity: [0, 0],
        });
      drag.moved = true;
      const visible = drag.frames.filter(item => item.visible);
      if (!visible.length) return;
      const area = this.displayTarget ? (pet ? this.area : screen.getPrimaryDisplay().workArea) : screen.getDisplayNearestPoint(point).workArea;
      const clamped = clampFrames(visible.map(item => ({ ...item.limit, x: item.limit.x + dx, y: item.limit.y + dy })),
        { x: area.x + 8, y: area.y + 8, width: area.width - 16, height: area.height - 16 });
      const moveX = clamped[0].x - visible[0].limit.x, moveY = clamped[0].y - visible[0].limit.y;
      if (pet || !this.displayTarget) this.current.origin = { x: drag.origin.x + moveX, y: drag.origin.y + moveY };
      for (const item of drag.frames) if (!item.window.isDestroyed())
        item.window.setPosition(Math.round(item.frame.x + moveX), Math.round(item.frame.y + moveY));
      if (pet) {
        const now = Date.now(),
          dt = Math.max(1, now - drag.time) / 1000;
        this.current.window.webContents.send("desktop:interact", {
          kind: "drag-move",
          velocity: [
            (point.x - drag.last.x) / dt,
            -(point.y - drag.last.y) / dt,
          ],
        });
        drag.last = point;
        drag.time = now;
      }
    } else {
      this.drag = null;
      if (pet) {
        this.current.window.webContents.send("desktop:interact", {
          kind: drag.moved || phase === "cancel" ? "drag-end" : "tap",
          velocity: [0, 0],
        });
        if (!drag.moved && phase === "end") this.toggleBubble();
      }
      if (drag.moved) {
        if (this.displayTarget) {
          if (pet) this.savePosition();
          return;
        }
        this.savePosition();
        const defaultFrame = bubbleFrame(
          { ...this.current.origin, ...this.current.viewport },
          this.area,
          null,
          this.bubbleHeight,
        );
        const actual = this.bubble.getBounds();
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
      ...(this.displayTarget && this.current.hasBounds ? { anchor: petAnchor(this.current) } : {}),
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
    if (!this.tasks && this.hasTaskStatus() && !this.bubbleOpen) this.showTasks(false);
    else this.syncTasksVisibility();
    if (event?.type === "task.approval") this.showTasks(true);
    if (this.current) this.sendPet(this.current);
    if (event?.type === "engine.ready") {
      this.engineReady = true;
      if (this.bubbleReady) void this.recoverNotifications();
    }
    if (
      [
        "front.user_text",
        "front.assistant_text",
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
    if (method === 'desktop_set_pet_display') return this.setPetDisplay(params.id);
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
        if (this.displayTarget) {
          this.placePet(pet, targetDisplay(screen.getAllDisplays(), screen.getPrimaryDisplay(), this.displayTarget));
          this.savePosition();
          this.positionBubble();
          return this.sendDesktop();
        }
        const size = petSize(result.scale, this.area);
        if (pet.dockInsetY !== undefined) pet.dockInsetY *= size.height / pet.viewport.height;
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
    for (const event of ['display-added', 'display-removed', 'display-metrics-changed'])
      screen.removeListener(event, this.displayChanged);
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
