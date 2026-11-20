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
const { decodeDisplayPreference, selectedDisplay, targetDisplay, petAnchor, petPlacement,
  displayBubbleFrame, hardwarePetScale, companionDisplayAreas } = require('./displays.cjs');
const {
  DESKTOP_CHAT_SIZE,
  clampFrame,
  petSize,
  validBounds,
  scalePetCrop,
  bubbleFrame,
  taskFrame,
  linkedFrames,
  clampFrames,
} = require("./layout.cjs");
const { WindowGeometry } = require("./window-geometry.cjs");
const { MusicMonitor } = require("./music-monitor.cjs");
const { WakeWord, wakeReply } = require("./wake-word.cjs");
const { readingTime, messageForEvent, shouldExpand } = require('./companion-presentation.cjs');

function avatarWindowsOpen(pet) {
  return Boolean(pet && !pet.window.isDestroyed() && !pet.stage.isDestroyed());
}

function destroyAvatarWindows(pet) {
  for (const window of [pet?.window, pet?.stage]) {
    if (window && !window.isDestroyed()) window.destroy();
  }
}

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
    wakeAllowed = () => true,
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
      wakeAllowed,
    });
    this.state = {};
    if (process.platform === "darwin") {
      this.wakeWord = new WakeWord({
        onError: (message) => this.feedback(message),
        onWake: () => this.answerWakeWord(),
        onDiagnostic: event => {
          try {
            fs.appendFileSync(path.join(data, 'logs/wake-listener.jsonl'),
              `${JSON.stringify({ at: new Date().toISOString(), hostPid: process.pid, ...event })}\n`, { mode: 0o600 });
          } catch { /* A diagnostics write failure must not change wake behavior. */ }
        },
      });
      let wakeEnabled = false;
      try {
        const saved = JSON.parse(fs.readFileSync(path.join(data, "voice-preferences.json"), "utf8"));
        wakeEnabled = saved?.version === 1 && saved.wakeEnabled === true;
      } catch (error) {
        if (error.code !== "ENOENT" && !(error instanceof SyntaxError))
          console.error("Voice preference:", error.message);
      }
      this.wakeWord.setEnabled(wakeEnabled);
    }
    this.snapshot = {};
    this.windows = new Map();
    this.geometry = new WindowGeometry();
    this.bubbleOpen = false;
    this.bubbleHeight = 300;
    this.chatReadingText = '';
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
    this.displayLabel = '';
    this.displayProfile = 'desktop';
    this.stageBackground = 'purple';
    this.legacyDisplayPreference = false;
    try {
      const saved = decodeDisplayPreference(JSON.parse(fs.readFileSync(path.join(data, 'display.json'), 'utf8')));
      this.displayTarget = saved.target;
      this.displayLabel = saved.label;
      this.displayProfile = saved.profile;
      this.stageBackground = saved.background;
      this.legacyDisplayPreference = saved.legacy;
    } catch (error) {
      if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) console.error('Display preference:', error.message);
    }
    this.displayChanged = () => this.reconcileDisplays();
  }
  createWindow(options, page) {
    const macPanel = process.platform === 'darwin';
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
      // Native panels can take keyboard focus without activating BoxAgent's Space.
      ...(macPanel ? { type: 'panel' } : {}),
      webPreferences: {
        preload: path.join(__dirname, "../preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: true,
      },
      ...options,
    });
    this.geometry.remember(window, options);
    this.registerWindow(window);
    // The chat is above the avatar's transparent hit window where they overlap.
    const level = page === 'stage' ? 0 : page === 'pet' ? 1 : 2;
    this.windows.set(window, level);
    window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: macPanel });
    this.setCompanionLevel(window, level, this.isSceneDisplay());
    window.on("closed", () => this.windows.delete(window));
    window.loadFile(path.join(__dirname, `../dist/${page}.html`));
    return window;
  }
  setCompanionLevel(window, level, scene) {
    if (window === this.gameDock && this.gameShieldOffset) {
      window.setAlwaysOnTop(true, 'screen-saver', this.gameShieldOffset);
      return;
    }
    // In desktop mode the drawing and mouse windows must share a native layer.
    const relativeLevel = !scene && level === 1 ? 0 : level;
    window.setAlwaysOnTop(true, scene ? 'screen-saver' : 'floating', relativeLevel);
  }
  isSceneDisplay() {
    return this.displayProfile === 'hardware' && Boolean(this.displayTarget)
      && this.preferredDisplay().id !== screen.getPrimaryDisplay().id;
  }
  // 指定副屏的场景包含菜单栏区域，角色按真实屏幕坐标绘制。
  stageBounds(pet) {
    const anchor = pet?.hasBounds
      ? petAnchor(pet)
      : { x: pet?.origin?.x ?? 0, y: pet?.origin?.y ?? 0 };
    const display = this.displayTarget
      ? this.preferredDisplay()
      : screen.getDisplayNearestPoint(anchor);
    const area = display.workArea, bounds = display.bounds;
    if (this.isSceneDisplay()) return { ...bounds };
    // 背景铺到屏幕底边，Windows 任务栏一并覆盖；顶部保留 macOS 菜单栏。
    return {
      x: area.x,
      y: area.y,
      width: area.width,
      height: bounds.y + bounds.height - area.y,
    };
  }
  syncStage(pet) {
    const stage = pet?.stage;
    if (!stage || stage.isDestroyed()) return;
    const scene = this.isSceneDisplay();
    if (this.sceneMode !== scene) {
      this.sceneMode = scene;
      for (const [window, level] of this.windows) {
        if (!window.isDestroyed()) this.setCompanionLevel(window, level, scene);
      }
    }
    let bounds = this.stageBounds(pet);
    const canUseSimpleFullScreen = process.platform === "darwin" && stage.setSimpleFullScreen;
    if (scene && canUseSimpleFullScreen) {
      if (!stage.isSimpleFullScreen() || screen.getDisplayMatching(stage.getBounds()).id !== this.preferredDisplay().id) {
        if (stage.isSimpleFullScreen()) stage.setSimpleFullScreen(false);
        if (!avatarWindowsOpen(pet)) return;
        stage.setBounds(bounds);
        if (!avatarWindowsOpen(pet)) return;
        stage.setSimpleFullScreen(true);
      }
      // Simple fullscreen can change the native frame. Use its actual origin
      // when converting the avatar's screen position to the stage canvas.
      if (!avatarWindowsOpen(pet)) return;
      bounds = stage.getBounds();
      this.geometry.remember(stage, bounds);
    } else {
      if (canUseSimpleFullScreen && stage.isSimpleFullScreen()) {
        stage.setSimpleFullScreen(false);
        if (!avatarWindowsOpen(pet)) return;
        this.setCompanionLevel(stage, 0, false);
      }
      const current = stage.getBounds();
      if (
        current.x !== bounds.x ||
        current.y !== bounds.y ||
        current.width !== bounds.width ||
        current.height !== bounds.height
      ) {
        stage.setBounds(bounds);
        this.geometry.remember(stage, bounds);
      }
    }
    pet.stageArea = bounds;
    pet.canvas = {
      x: Math.round(pet.origin.x - bounds.x),
      y: Math.round(pet.origin.y - bounds.y),
    };
  }
  sendInteract(pet, value) {
    if (avatarWindowsOpen(pet)) pet.stage.webContents.send("desktop:interact", value);
  }
  performPet(sender, name) {
    const pet = [this.pending, this.current].find(
      (p) => avatarWindowsOpen(p) && p.window.webContents.id === sender.id,
    ) || this.current;
    if (avatarWindowsOpen(pet)) pet.stage.webContents.send("desktop:perform", name);
  }
  async start() {
    if (this.displayTarget && this.displayTarget !== 'primary' && !this.displayLabel) {
      const display = screen.getAllDisplays().find(item => String(item.id) === this.displayTarget);
      if (display?.label) this.displayLabel = display.label;
    }
    if (this.legacyDisplayPreference) {
      try { this.saveDisplayPreference(this.displayTarget, this.displayLabel); }
      catch (error) { console.error('Display preference:', error.message); }
    }
    this.snapshot = await this.service.request("desktop_snapshot");
    this.bubble = this.createWindow(
      {
        ...DESKTOP_CHAT_SIZE,
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
    this.wakeWord?.setName(this.snapshot.name);
    this.syncWakeWord();
    this.tray.setToolTip(`BoxAgent · ${this.snapshot.name} · ⌃⌥空格切换语音`);
    this.updateMenu();
    for (const event of ['display-added', 'display-removed', 'display-metrics-changed'])
      screen.on(event, this.displayChanged);
    this.tray.on("click", () => this.toggleBubble());
    if (
      !globalShortcut.register("Control+Alt+Space", () => this.quickControl("toggle_voice"))
    )
      this.feedback("快捷键已被占用，请使用小窗的语音按钮");
    this.pointerTimer = setInterval(() => this.pointer(), 100);
    if (process.platform === 'win32') {
      this.musicMonitor = new MusicMonitor(active => {
        this.musicActive = active;
        if (this.current) this.sendPet(this.current);
      });
      this.musicMonitor.start();
    }
    await this.loadAvatar(this.snapshot.appearance, false);
    // Engine can finish connecting before this host is constructed. Hydrate
    // its current state so wake does not wait for a later conversation event.
    if (this.client.online) {
      const state = await this.client.request('state');
      this.stateChanged(state, { type: 'engine.ready' });
    }
  }
  preferredDisplay(displays = screen.getAllDisplays(), primary = screen.getPrimaryDisplay()) {
    return targetDisplay(displays, primary, this.displayTarget, this.displayLabel);
  }
  saveDisplayPreference(target, label = '', profile = this.displayProfile, background = this.stageBackground) {
    fs.mkdirSync(this.data, { recursive: true });
    const file = path.join(this.data, 'display.json');
    fs.writeFileSync(file + '.tmp', JSON.stringify({ version: 2, target, profile,
      ...(label ? { label } : {}), ...(background === 'pink' ? { background } : {}) }));
    fs.renameSync(file + '.tmp', file);
  }
  displayState() {
    const primary = screen.getPrimaryDisplay();
    const displays = screen.getAllDisplays();
    const selected = selectedDisplay(displays, primary, this.displayTarget, this.displayLabel);
    const active = this.displayTarget ? selected || primary
      : this.current ? screen.getDisplayMatching({ ...this.current.origin, ...this.current.viewport }) : primary;
    const hardware = this.displayProfile === 'hardware' && selected && selected.id !== primary.id;
    return {
      displays: displays.map(display => ({ id: String(display.id), label: display.label || String(display.id), primary: display.id === primary.id })),
      pet_display_id: String(active.id),
      selected_display_id: selected ? String(selected.id) : this.displayTarget,
      display_profile: this.displayProfile,
      active_display_profile: hardware ? 'hardware' : 'desktop',
      stage_background: this.stageBackground,
      separate_windows: Boolean(hardware),
    };
  }
  displayMenuItems() {
    const state = this.displayState();
    const enabled = Boolean(this.current?.ready) && !this.pending && !this.loading;
    return [
      { label: '桌宠', type: 'radio', checked: state.display_profile === 'desktop', enabled,
        click: () => this.changeDisplayProfileFromMenu('desktop') },
      { label: '无触屏硬件', type: 'radio', checked: state.display_profile === 'hardware', enabled,
        click: () => this.changeDisplayProfileFromMenu('hardware') },
      ...(state.display_profile === 'hardware' && state.active_display_profile === 'desktop'
        ? [{ label: '选择副屏后启用硬件展示', enabled: false }] : []),
      { type: 'separator' },
      { label: '随拖动切换屏幕', type: 'radio', checked: state.selected_display_id === null, enabled,
        click: () => this.changeDisplayFromMenu(null) },
      ...state.displays.map(display => ({
        label: `${display.primary ? '主屏' : '副屏'} · ${display.label}`,
        type: 'radio', checked: state.selected_display_id === display.id, enabled,
        click: () => this.changeDisplayFromMenu(display.id),
      })),
      ...(state.displays.length < 2 ? [{ type: 'separator' }, { label: '未检测到扩展副屏', enabled: false }] : []),
    ];
  }
  changeDisplayFromMenu(id) {
    try { this.setPetDisplay(id); } catch (error) { this.feedback(error.message); }
  }
  changeDisplayProfileFromMenu(profile) {
    try { this.setDisplayProfile(profile); } catch (error) { this.feedback(error.message); }
  }
  showDisplayMenu(sender) {
    Menu.buildFromTemplate(this.displayMenuItems()).popup({ window: BrowserWindow.fromWebContents(sender) });
  }
  quickControl(method, params) {
    return this.client.request(method, params).catch(error => this.feedback(error.message));
  }
  setWakeEnabled(enabled) {
    if (!this.wakeWord) return;
    try {
      fs.mkdirSync(this.data, { recursive: true });
      const file = path.join(this.data, "voice-preferences.json");
      fs.writeFileSync(file + ".tmp", JSON.stringify({ version: 1, wakeEnabled: enabled }));
      fs.renameSync(file + ".tmp", file);
      this.wakeWord.setEnabled(enabled);
      this.syncWakeWord();
    } catch (error) {
      this.feedback(error.message);
    }
    this.updateMenu();
  }
  updateMenu() {
    if (!this.tray) return;
    const displayItems = this.displayMenuItems();
    const { voice, lol_companion_enabled: lol, game_companion_enabled: wuwa,
      game_companion_available: wuwaAvailable } = this.state;
    const { online } = this.client;
    const signature = JSON.stringify([displayItems, online, voice, lol, wuwa, wuwaAvailable, this.wakeWord?.enabled]);
    if (signature === this.menuSignature) return;
    this.menuSignature = signature;
    const voiceActive = voice === "ready" || voice === "connecting";
    const gameReady = Boolean(online) && !["connecting", "stopping"].includes(voice);
    this.menu = Menu.buildFromTemplate([
      { label: voiceActive ? "结束语音" : "开启语音", enabled: Boolean(online) && voice !== "stopping",
        click: () => this.quickControl("toggle_voice") },
      { label: lol ? "退出 LoL 陪玩" : "开启 LoL 陪玩", enabled: gameReady,
        click: () => this.quickControl("set_companion_session", { game: "lol", enabled: !lol }) },
      ...(wuwaAvailable ? [{ label: wuwa ? "退出鸣潮陪玩" : "开启鸣潮陪玩", enabled: gameReady,
        click: () => this.quickControl("set_companion_session", { game: "wuwa", enabled: !wuwa }) }] : []),
      ...(this.wakeWord ? [{ label: "常驻语音唤醒", type: "checkbox", checked: this.wakeWord.enabled,
        click: item => this.setWakeEnabled(item.checked) }] : []),
      { type: "separator" },
      { label: "伙伴控制台…", click: this.showPanel },
      { label: "摄像头互动…", click: this.showCamera },
      { label: '伙伴与聊天显示屏', submenu: displayItems },
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
    if (this.closed || !avatarWindowsOpen(pet)) return;
    pet.walk = null;
    pet.walkAt = null;
    const scene = this.isSceneDisplay();
    const scale = this.snapshot.scale || 1;
    const area = scene && (this.bubbleOpen || this.tasksExpanded)
      ? companionDisplayAreas(pet, scale, display.workArea, this.bubbleHeight).pet : display.workArea;
    const placement = petPlacement(pet, scene ? hardwarePetScale(pet, scale, display.workArea) : scale,
      area, center, Boolean(this.displayTarget));
    Object.assign(pet, placement);
    this.geometry.setBounds(pet.window, { x: Math.round(pet.origin.x + pet.crop.x), y: Math.round(pet.origin.y + pet.crop.y),
      width: pet.crop.width, height: pet.crop.height });
    this.sendPet(pet);
    this.updatePetInput(pet);
  }
  setPetDisplay(id) {
    if (!this.current?.ready || !avatarWindowsOpen(this.current) || this.pending || this.loading) throw Error('形象仍在加载，请稍后切换屏幕');
    const primary = screen.getPrimaryDisplay();
    const display = id === null ? primary : screen.getAllDisplays().find(item => String(item.id) === id);
    if (!display) throw Error('该显示器已断开，请重新选择');
    const target = id === null ? null : display.id === primary.id ? 'primary' : id;
    const label = target && target !== 'primary' ? display.label || '' : '';
    return this.applyDisplayPreference(target, label, this.displayProfile, display, true);
  }
  setDisplayProfile(profile) {
    if (profile !== 'desktop' && profile !== 'hardware') throw Error('无效的展示形态');
    if (!this.current?.ready || !avatarWindowsOpen(this.current) || this.pending || this.loading) throw Error('形象仍在加载，请稍后切换形态');
    if (profile === this.displayProfile) return this.sendDesktop();
    const display = this.displayTarget ? this.preferredDisplay()
      : screen.getDisplayMatching(this.geometry.getBounds(this.current.window));
    return this.applyDisplayPreference(this.displayTarget, this.displayLabel, profile, display);
  }
  applyDisplayPreference(target, label, profile, display, center = false) {
    const wasScene = this.isSceneDisplay();
    this.saveDisplayPreference(target, label, profile);
    this.cancelDisplayDrag();
    this.displayTarget = target;
    this.displayLabel = label;
    this.displayProfile = profile;
    this.offset = null;
    this.taskSide = null;
    this.placePet(this.current, display, center || (!wasScene && this.isSceneDisplay()));
    this.positionBubble();
    this.savePosition();
    this.updateGameDock();
    this.scheduleChatClose(true);
    return this.sendDesktop();
  }
  setStageBackground(background) {
    if (background !== 'purple' && background !== 'pink') throw Error('无效的副屏背景');
    if (background === this.stageBackground) return this.sendDesktop();
    this.saveDisplayPreference(this.displayTarget, this.displayLabel, this.displayProfile, background);
    this.stageBackground = background;
    if (this.isSceneDisplay())
      for (const pet of [this.current, this.pending].filter(Boolean)) this.sendPet(pet);
    return this.sendDesktop();
  }
  cancelDisplayDrag() {
    if (this.drag?.pet && this.current)
      this.sendInteract(this.current, { kind: "drag-end", velocity: [0, 0] });
    this.drag = null;
  }
  reconcileDisplays() {
    if (this.closed) return;
    this.cancelDisplayDrag();
    const primary = screen.getPrimaryDisplay(), displays = screen.getAllDisplays();
    for (const pet of [this.current, this.pending].filter(Boolean)) {
      if (!avatarWindowsOpen(pet)) {
        this.failAvatar(pet, '角色窗口已关闭');
        continue;
      }
      const frame = this.geometry.getBounds(pet.window);
      const previous = screen.getDisplayMatching(frame);
      const display = this.displayTarget ? this.preferredDisplay(displays, primary) : previous;
      this.placePet(pet, display, display.id !== previous.id);
    }
    this.taskSide = null;
    this.positionBubble();
    this.sendDesktop();
  }
  get area() {
    if (this.displayTarget)
      return this.preferredDisplay().workArea;
    return screen.getDisplayNearestPoint(
      this.current?.origin || screen.getCursorScreenPoint(),
    ).workArea;
  }
  sendDesktop() {
    const value = {
      ...this.snapshot,
      ...this.displayState(),
      bubble_open: this.bubbleOpen,
      chat_pinned: Boolean(this.chatPinned),
      game_dock_open: Boolean(this.gameDockOpen),
      game_dock_game: this.companionGame(),
      avatar_portrait: this.current?.portrait,
      chat_dock_edge: this.desktopBubbleEdge(),
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
    if (this.closed) return;
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
        previousDisplay.id !== this.preferredDisplay().id);
      origin = placement.origin;
      viewport = placement.viewport;
    }
    origin = clampFrame({ ...origin, ...viewport }, area);
    // 渲染层先建、命中层后建，保证命中层在层之上，鼠标事件落在人物身上。
    const stageBounds = this.stageBounds({ origin });
    const stage = this.createWindow(
      { ...stageBounds, focusable: false, fullscreenable: true },
      "stage",
    );
    stage.setIgnoreMouseEvents(true, { forward: true });
    const window = this.createWindow({ ...origin, ...viewport, focusable: false }, "pet");
    // Never intercept clicks until the renderer supplies measured character bounds.
    window.setIgnoreMouseEvents(true, { forward: true });
    const candidate = {
      window,
      stage,
      stageArea: stageBounds,
      canvas: { x: Math.round(origin.x - area.x), y: Math.round(origin.y - area.y) },
      viewport,
      origin,
      crop: { x: 0, y: 0, ...viewport },
      ignoreMouse: true,
      restoreAnchor,
      ready: false,
      ...prepared,
      persist,
    };
    this.pending = candidate;
    const loaded = new Promise((resolve, reject) => {
      candidate.resolve = resolve;
      candidate.reject = reject;
    });
    candidate.timer = setTimeout(
      () => this.failAvatar(candidate, "模型加载超时"),
      30000,
    );
    window.webContents.on("did-finish-load", () => this.sendPet(candidate));
    stage.webContents.on("render-process-gone", () =>
      this.failAvatar(candidate, "角色渲染进程已退出"),
    );
    window.webContents.on("render-process-gone", () =>
      this.failAvatar(candidate, "角色渲染进程已退出"),
    );
    for (const layer of [window, stage]) {
      layer.once('closed', () => this.failAvatar(candidate, '角色窗口已关闭'));
    }
    this.sendDesktop();
    return loaded;
  }
  sendPet(pet) {
    if (!avatarWindowsOpen(pet)) return;
    this.syncStage(pet);
    if (avatarWindowsOpen(pet))
      pet.stage.webContents.send("desktop:pet", {
        url: pet.url,
        viewport: pet.viewport,
        crop: pet.crop,
        canvas: pet.canvas,
        scene: this.isSceneDisplay() ? this.stageBackground : undefined,
        gameOverlay: pet.hiddenForGame === true,
        overlayAnchors: pet.overlayAnchors,
        caption: this.bubbleOpen ? undefined : this.caption,
        footStatus: this.footStatus(pet),
        state: this.state.speaking && !this.state.user_speaking ? "speaking" : "idle",
        pointer: this.pointerValue || [0, 0],
        action: this.state.avatar_action,
        actionId: this.state.avatar_action_id,
        music: this.musicActive === true,
        walkDirection: pet.walk?.direction || 0,
      });
  }
  footStatus(pet) {
    if (this.bubbleOpen || this.tasksExpanded || pet.hiddenForGame) return;
    if (this.state.approval || ['awaiting_approval', 'needs_input'].includes(this.state.task))
      return { text: this.state.approval ? '等你确认' : '等你回复', tone: 'waiting' };
    if (['accepted', 'running', 'cancelling'].includes(this.state.task))
      return { text: this.state.task_text || this.state.task_goal || '任务进行中', tone: 'working' };
    return this.companionGame() ? { text: '陪玩中', tone: 'playing' } : { text: '在这里', tone: 'idle' };
  }
  async report(sender, message) {
    const pet = [this.pending, this.current].find(
      (p) =>
        avatarWindowsOpen(p) && (p.window.webContents.id === sender.id || p.stage.webContents.id === sender.id),
    );
    if (!pet || !message || typeof message !== "object") return;
    if (message.type === 'locomotion') {
      pet.walkEligible = message.eligible === true;
      pet.walkPlaying = message.walking === true;
      return;
    }
    if (message.type === "bounds") {
      // 只采用模型加载后的首帧；后续动作和诊断截图不能改变窗口。
      if (pet.initialCrop) return;
      const bounds = validBounds(message, pet.viewport);
      if (!bounds || this.drag) return;
      pet.crop = {
        x: bounds.x,
        y: pet.viewport.height - bounds.y - bounds.height,
        width: bounds.width,
        height: bounds.height,
      };
      pet.hasBounds = true;
      const mask = message.hitMask;
      if (Number.isInteger(mask?.size) && mask.size > 0 && mask.size <= 64 &&
          typeof mask.cells === 'string' && mask.cells.length === mask.size ** 2 && /^[01]+$/.test(mask.cells))
        pet.hitMask = mask;
      if (typeof message.portrait === 'string' && message.portrait.startsWith('data:image/png;base64,') &&
          message.portrait.length <= 64000) pet.portrait = message.portrait;
      pet.initialCrop = { crop: { ...pet.crop }, viewport: { ...pet.viewport } };
      const shoulder = message.chatAnchor;
      if (Number.isFinite(shoulder?.left) && Number.isFinite(shoulder?.right) && Number.isFinite(shoulder?.y) &&
          shoulder.left >= 0 && shoulder.left < shoulder.right && shoulder.right <= 1 && shoulder.y >= 0 && shoulder.y <= 1)
        pet.chatAnchor = shoulder;
      const { face, feet } = message.overlayAnchors || {};
      if ([face?.left, face?.right, face?.y, feet?.x, feet?.y].every(value => Number.isFinite(value) && value >= 0 && value <= 1)
          && face.left < face.right)
        pet.overlayAnchors = { face, feet };
      if (this.displayTarget) {
        const display = this.preferredDisplay();
        if (pet.restoreAnchor) {
          pet.origin = { x: pet.restoreAnchor.x - pet.crop.x - pet.crop.width / 2,
            y: pet.restoreAnchor.y - pet.crop.y - pet.crop.height };
          pet.restoreAnchor = null;
        }
        this.placePet(pet, display);
        if (pet === this.current) {
          this.positionBubble();
          this.savePosition();
        }
        return;
      }
      this.geometry.setBounds(pet.window, {
        x: Math.round(pet.origin.x + pet.crop.x),
        y: Math.round(pet.origin.y + pet.crop.y),
        width: Math.round(pet.crop.width),
        height: Math.round(pet.crop.height),
      });
      this.sendPet(pet);
      this.updatePetInput(pet);
      if (pet === this.current) this.positionBubble();
    } else if (message.type === "ready" && pet === this.pending) {
      try {
        const snapshot = await this.service.request("desktop_commit_avatar", {
          token: pet.token,
          persist: pet.persist,
        });
        if (this.closed || this.pending !== pet) return;
        if (!avatarWindowsOpen(pet)) {
          this.failAvatar(pet, '角色窗口已关闭');
          return;
        }
        pet.stage.showInactive();
        if (this.pending !== pet) return;
        pet.window.showInactive();
        if (this.pending !== pet) return;
        clearTimeout(pet.timer);
        const previous = this.current;
        this.current = pet;
        this.pending = null;
        pet.ready = true;
        this.updatePetInput(pet);
        if (this.displayTarget && pet.hasBounds) this.savePosition();
        this.snapshot = { ...this.snapshot, ...snapshot };
        if (this.snapshot.feedback === this.error) this.snapshot.feedback = "";
        this.error = "";
        destroyAvatarWindows(previous);
        this.updateGameDock();
        this.sendPet(pet);
        this.syncTasksVisibility();
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
    if (this.closed) return;
    const loading = pet === this.pending;
    if (!loading && pet !== this.current) return;
    const feedback = this.avatarFailure(
      message,
      loading && Boolean(this.current?.ready) && avatarWindowsOpen(this.current),
    );
    clearTimeout(pet.timer);
    // Detach the pair before destroying the surviving layer; its own close is expected.
    if (loading) {
      this.pending = null;
    } else {
      this.current = null;
      this.drag = null;
    }
    pet.ready = false;
    destroyAvatarWindows(pet);
    if (loading) {
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
      (p) => avatarWindowsOpen(p) && p.stage.webContents.id === sender.id,
    );
    if (pet) this.sendPet(pet);
  }
  updatePetInput(pet, point = screen.getCursorScreenPoint()) {
    if (!pet?.ready || !pet.hasBounds || pet.window.isDestroyed()) return;
    const x = point.x - pet.origin.x, y = point.y - pet.origin.y;
    const { crop, viewport, hitMask } = pet;
    const inside = x >= crop.x && x < crop.x + crop.width && y >= crop.y && y < crop.y + crop.height;
    const dragging = this.drag?.sender === pet.window.webContents.id;
    let hit = inside;
    if (inside && !this.sceneMode && hitMask) {
      const col = Math.floor(x * hitMask.size / viewport.width);
      const row = Math.floor(y * hitMask.size / viewport.height);
      hit = hitMask.cells[row * hitMask.size + col] === '1';
    }
    const ignore = !dragging && !hit;
    if (ignore === pet.ignoreMouse) return;
    pet.ignoreMouse = ignore;
    pet.window.setIgnoreMouseEvents(ignore, { forward: true });
    if (!ignore && !dragging) this.performPet(pet.window.webContents, 'hover');
  }
  petPointer(sender) {
    if (avatarWindowsOpen(this.current) && this.current.window.webContents.id === sender.id) this.updatePetInput(this.current);
  }
  pointer() {
    if (!this.current?.ready) return;
    this.stepWalk();
    const point = screen.getCursorScreenPoint(),
      pet = this.current;
    this.updatePetInput(pet, point);
    this.pointerValue = [
      Math.round(point.x - pet.origin.x - pet.viewport.width / 2),
      Math.round(pet.origin.y + pet.viewport.height / 2 - point.y),
    ];
    const signature = JSON.stringify([
      ...this.pointerValue, pet.origin.x, pet.origin.y, pet.walk?.direction || 0,
    ]);
    if (signature === this.pointerSignature) return;
    this.pointerSignature = signature;
    this.sendPet(pet);
  }
  stepWalk(now = Date.now(), random = Math.random) {
    const pet = this.current;
    if (this.closed || !pet || pet.window.isDestroyed()) return;
    const dt = Math.min(0.15, Math.max(0, (now - (pet.walkTick ?? now)) / 1000));
    pet.walkTick = now;
    const allowed = this.sceneMode && pet.walkEligible && !this.drag && !this.pending && !this.loading &&
      !this.bubbleOpen && !this.tasks?.isVisible() && !this.musicActive &&
      !this.state.speaking && !this.state.user_speaking;
    const postpone = () => { pet.walk = null; pet.walkAt = now + 15000 + random() * 15000; };
    if (!allowed) { postpone(); return; }
    // Waiting for the next walk needs no native display enumeration.
    if (!pet.walk) {
      if (!pet.walkAt) postpone();
      if (now < pet.walkAt) return;
    }
    const area = this.stageBounds(pet);
    // Keep the full animation canvas inside the display, including arm motion.
    const min = area.x + 8, max = area.x + area.width - pet.viewport.width - 8;
    if (max <= min) { postpone(); return; }
    if (!pet.walk) {
      const target = min + random() * (max - min);
      if (Math.abs(target - pet.origin.x) < 24) { postpone(); return; }
      pet.walk = { target, direction: target < pet.origin.x ? -1 : 1 };
      return;
    }
    if (!pet.walkPlaying) return;
    const target = Math.max(min, Math.min(max, pet.walk.target));
    const distance = target - pet.origin.x;
    const step = Math.max(35, pet.viewport.height * 0.22) * dt;
    pet.origin.x = Math.max(min, Math.min(max, pet.origin.x + Math.sign(distance) * Math.min(Math.abs(distance), step)));
    this.geometry.setPosition(pet.window, Math.round(pet.origin.x + pet.crop.x), Math.round(pet.origin.y + pet.crop.y));
    if (Math.abs(target - pet.origin.x) < 0.5) {
      postpone();
      this.savePosition();
    }
  }
  desktopShoulder() {
    const pet = this.current;
    return pet?.chatAnchor && {
      left: pet.origin.x + pet.chatAnchor.left * pet.viewport.width,
      right: pet.origin.x + pet.chatAnchor.right * pet.viewport.width,
      y: pet.origin.y + pet.chatAnchor.y * pet.viewport.height,
    };
  }
  desktopBubbleFrame(offset) {
    return bubbleFrame(this.geometry.getBounds(this.current.window), this.area, offset, this.bubbleHeight, this.desktopShoulder());
  }
  desktopBubbleEdge() {
    if (!this.bubbleOpen || !this.bubble || this.bubble.isDestroyed() || this.isSceneDisplay()) return;
    const shoulder = this.desktopShoulder();
    if (!shoulder) return;
    const { x, y, width } = this.geometry.getBounds(this.bubble);
    if (Math.abs(y + 28 - shoulder.y) > 10) return;
    if (Math.abs(x + width + 4 - shoulder.left) <= 10) return 'right';
    if (Math.abs(x - 4 - shoulder.right) <= 10) return 'left';
  }
  positionBubble() {
    if (this.closed || !this.bubble || this.bubble.isDestroyed() || !avatarWindowsOpen(this.current)) return;
    if (this.isSceneDisplay()) {
      const display = this.preferredDisplay();
      this.placePet(this.current, display);
      const area = companionDisplayAreas(this.current, this.snapshot.scale || 1, display.workArea, this.bubbleHeight).panel;
      this.geometry.setBounds(this.bubble, displayBubbleFrame(this.geometry.getBounds(this.bubble), area, this.bubbleHeight));
      if (this.tasks?.isVisible()) this.positionTasks();
      return;
    }
    this.geometry.setBounds(this.bubble, this.desktopBubbleFrame(this.offset));
    if (this.tasks?.isVisible()) this.positionTasks();
  }
  resizeBubble(sender, height) {
    if (this.bubble?.webContents.id !== sender.id || !Number.isFinite(height))
      return;
    const requested = Math.round(Math.max(228, Math.min(600, height)));
    if (Math.abs(requested - this.bubbleHeight) < 2) return;
    const dockEdge = this.desktopBubbleEdge();
    this.bubbleHeight = requested;
    this.positionBubble();
    if (this.desktopBubbleEdge() !== dockEdge) this.sendDesktop();
  }
  showBubble() {
    if (this.closed || !this.bubble || this.bubble.isDestroyed()) return;
    this.bubbleOpen = true;
    this.syncTasksVisibility();
    this.positionBubble();
    this.bubble.showInactive();
    this.clearCaption();
    if (this.current) this.sendPet(this.current);
    this.scheduleChatClose(true);
    this.sendDesktop();
  }
  hideBubble() {
    this.bubbleOpen = false;
    this.chatPinned = false;
    clearTimeout(this.chatCloseTimer);
    this.chatCloseTimer = undefined;
    this.bubble?.hide();
    if (this.isSceneDisplay() && this.current) this.placePet(this.current, this.preferredDisplay());
    this.syncTasksVisibility();
    if (this.current) this.sendPet(this.current);
    this.sendDesktop();
  }
  holdChat(sender, held) {
    if (!this.bubble || this.bubble.isDestroyed() || this.bubble.webContents.id !== sender.id ||
        typeof held !== 'boolean' || held === this.chatHeld) return;
    this.chatHeld = held;
    this.scheduleChatClose();
  }
  pinChat() {
    if (!this.bubbleOpen) this.showBubble();
    this.chatPinned = true;
    this.scheduleChatClose(true);
    this.sendDesktop();
  }
  scheduleChatClose(reset = false) {
    const held = this.closed || !this.bubbleOpen || this.chatPinned || this.chatHeld ||
      this.state.speaking || this.state.user_speaking || this.state.voice === 'connecting' ||
      this.state.approval || ['awaiting_approval', 'needs_input'].includes(this.state.task);
    if (held || reset) {
      clearTimeout(this.chatCloseTimer);
      this.chatCloseTimer = undefined;
    }
    if (held || this.chatCloseTimer) return;
    this.chatCloseTimer = setTimeout(() => {
      this.chatCloseTimer = undefined;
      this.hideBubble();
    }, readingTime(this.chatReadingText, this.isSceneDisplay()));
    this.chatCloseTimer.unref?.();
  }
  clearCaption() {
    clearTimeout(this.captionTimer);
    this.captionTimer = undefined;
    if (!this.caption) return;
    this.caption = null;
    if (this.current) this.sendPet(this.current);
  }
  scheduleCaptionClear() {
    clearTimeout(this.captionTimer);
    if (!this.caption || this.state.speaking || this.state.user_speaking) return;
    const delay = Math.max(6000, Math.min(18000, this.caption.text.length * 140));
    this.captionTimer = setTimeout(() => this.clearCaption(), delay);
    this.captionTimer.unref?.();
  }
  presentMessage(message, { stream = false } = {}) {
    if (!message?.text?.trim()) return;
    this.chatReadingText = message.text;
    if (this.bubbleOpen) {
      this.scheduleChatClose(true);
      return;
    }
    if (!avatarWindowsOpen(this.current) || (!stream && shouldExpand(message, Boolean(this.companionGame())))) {
      this.showBubble();
      return;
    }
    if (this.caption?.source === '微信' && message.source !== '微信') return;
    this.caption = !message.source && this.companionGame() && message.text.length > 110
      ? { text: '完整回复可以在聊天里查看。' } : message;
    this.sendPet(this.current);
    this.scheduleCaptionClear();
  }
  companionGame() {
    return this.state.lol_companion_enabled ? 'lol' : this.state.game_companion_enabled ? 'wuwa' : undefined;
  }
  syncGameTracking() {
    const game = this.companionGame();
    if (game === this.trackedGame) return;
    this.trackedGame = game;
    clearInterval(this.gameDisplayTimer);
    this.gameDockOpen = false;
    this.gameDisplay = undefined;
    this.gameShieldOffset = undefined;
    this.updateGameDock();
    if (game) {
      this.gameDisplayTimer = setInterval(() => void this.refreshGameDisplay(), 900);
      this.gameDisplayTimer.unref?.();
      void this.refreshGameDisplay();
    }
    this.sendDesktop();
  }
  async refreshGameDisplay() {
    const game = this.companionGame();
    if (!game || this.checkingGame || this.closed) return;
    this.checkingGame = true;
    try {
      const observed = await this.service.request('desktop_foreground_game');
      if (this.closed || game !== this.companionGame()) return;
      let display;
      if (observed?.game === game) {
        const bounds = observed.bounds;
        let point = bounds && { x: Math.round(bounds.x + bounds.width / 2), y: Math.round(bounds.y + bounds.height / 2) };
        if (point && process.platform === 'win32') point = screen.screenToDipPoint(point);
        display = point ? screen.getDisplayNearestPoint(point) : screen.getPrimaryDisplay();
      }
      this.gameShieldOffset = display && game === 'lol' && process.platform === 'darwin'
        && Number.isInteger(observed.shield_offset) && observed.shield_offset > 1 ? observed.shield_offset : undefined;
      this.gameDisplay = display;
      this.updateGameDock();
    } catch (error) {
      if (!this.closed && this.gameDisplay) {
        this.gameDisplay = undefined;
        this.gameShieldOffset = undefined;
        this.updateGameDock();
      }
    } finally {
      this.checkingGame = false;
    }
  }
  updateGameDock() {
    const display = this.gameDisplay;
    if (display && !this.closed) {
      if (!this.gameDock || this.gameDock.isDestroyed()) {
        this.gameDock = this.createWindow({ width: 28, height: 48, focusable: false,
          acceptFirstMouse: true, title: 'BoxAgent · 陪玩' }, 'game-dock');
        this.gameDock.webContents.on('did-finish-load', () => this.sendDesktop());
      }
      const { x, y, width, height } = display.bounds;
      const dockWidth = this.gameDockOpen ? 180 : 28;
      const frame = { x: x + width - dockWidth, y: y + Math.round(height * .27), width: dockWidth, height: 48 };
      const previous = this.geometry.getBounds(this.gameDock);
      if (Object.keys(frame).some(key => previous[key] !== frame[key])) this.geometry.setBounds(this.gameDock, frame);
      if (this.appliedGameShieldOffset !== this.gameShieldOffset) {
        this.setCompanionLevel(this.gameDock, 2, this.isSceneDisplay());
        this.appliedGameShieldOffset = this.gameShieldOffset;
      }
      if (!this.gameDock.isVisible()) this.gameDock.showInactive();
    } else if (this.gameDock && !this.gameDock.isDestroyed() && this.gameDock.isVisible()) {
      this.gameDock.hide();
      this.setCompanionLevel(this.gameDock, 2, this.isSceneDisplay());
      this.appliedGameShieldOffset = undefined;
    }
    const pet = this.current;
    if (!avatarWindowsOpen(pet) || this.closed) return;
    const hide = Boolean(display && !this.isSceneDisplay() && screen.getDisplayMatching(this.geometry.getBounds(pet.window)).id === display.id);
    if (hide === Boolean(pet.hiddenForGame)) return;
    pet.hiddenForGame = hide;
    if (hide) pet.window.hide();
    else pet.window.showInactive();
    this.sendPet(pet);
  }
  toggleGameDock(sender) {
    if (!this.gameDock || this.gameDock.isDestroyed() || this.gameDock.webContents.id !== sender.id) return;
    this.gameDockOpen = !this.gameDockOpen;
    this.updateGameDock();
    this.sendDesktop();
  }
  toggleBubble() {
    this.bubbleOpen ? this.hideBubble() : this.showBubble();
  }
  showTasks(expanded = true) {
    if (this.closed) return;
    const changed = Boolean(this.tasksExpanded) !== expanded;
    this.tasksExpanded = expanded;
    if (changed && this.isSceneDisplay() && this.current && !this.bubbleOpen)
      this.placePet(this.current, this.preferredDisplay());
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
    if (this.current) this.sendPet(this.current);
    this.sendDesktop();
  }
  hasTaskStatus() {
    return Boolean((this.state.task && this.state.task !== "idle") || this.state.task_queue?.length);
  }
  syncTasksVisibility(reposition = false) {
    if (!this.tasks || this.tasks.isDestroyed()) return;
    if (this.tasksExpanded || (!this.bubbleOpen && !avatarWindowsOpen(this.current) && this.hasTaskStatus())) {
      const place = reposition || !this.tasks.isVisible();
      if (place) this.positionTasks();
      const frame = this.geometry.getBounds(this.tasks);
      this.tasks.showInactive();
      // macOS 可能在重新显示窗口时调整位置，恢复已计算的整组停靠坐标。
      if (place) this.geometry.setBounds(this.tasks, frame);
    } else this.tasks.hide();
  }
  positionTasks() {
    const anchor = this.bubble
      ? this.geometry.getBounds(this.bubble)
      : this.current ? { ...this.current.origin, ...this.current.viewport }
      : { x: this.area.x + this.area.width - 280, y: this.area.y + this.area.height - 210, width: 252, height: 182 };
    const displayArea = screen.getDisplayMatching(anchor).workArea;
    const area = this.isSceneDisplay() && this.current
      ? companionDisplayAreas(this.current, this.snapshot.scale || 1, this.preferredDisplay().workArea, this.bubbleHeight).panel
      : displayArea;
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
    this.geometry.setBounds(this.bubble, frames.bubble);
    this.geometry.setBounds(this.tasks, frames.tasks);
    if (this.current && !this.isSceneDisplay()) {
      const base = this.desktopBubbleFrame(null);
      this.offset = { x: frames.bubble.x - base.x, y: frames.bubble.y - base.y };
    }
  }
  collapseTasks() {
    if (!this.tasks || this.tasks.isDestroyed()) return;
    this.tasksPinned = false;
    this.tasksExpanded = false;
    if (this.isSceneDisplay() && this.current && !this.bubbleOpen)
      this.placePet(this.current, this.preferredDisplay());
    this.syncTasksVisibility(true);
    if (this.current) this.sendPet(this.current);
    this.sendDesktop();
  }
  pinTasks() {
    this.tasksPinned = !this.tasksPinned;
    this.sendDesktop();
  }
  dragWindow(sender, message) {
    if (!avatarWindowsOpen(this.current) || !message || typeof message !== "object") return;
    let { phase } = message;
    const { point, buttons } = message;
    let windows = [this.current.window, this.bubble, this.tasks].filter(window => window && !window.isDestroyed());
    if (!windows.some(window => window.webContents.id === sender.id)) return;
    if (!["start", "move", "end", "cancel"].includes(phase)) return;
    if (phase === "move" && !(Number.isInteger(buttons) && (buttons & 1))) phase = "cancel";
    if (["start", "move"].includes(phase) && (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y))) return;
    const pet = this.current.window.webContents.id === sender.id;
    const scene = this.isSceneDisplay();
    if (scene) windows = windows.filter(window => pet ? window === this.current.window : window !== this.current.window);
    if (phase === "start") {
      if (!(Number.isInteger(buttons) && (buttons & 1))) return;
      this.drag = {
        sender: sender.id, pet,
        start: point,
        frames: windows.map(window => ({ window, frame: this.geometry.getBounds(window), visible: window.isVisible(),
          limit: window === this.current.window && !this.displayTarget
            ? { ...this.current.origin, ...this.current.viewport } : this.geometry.getBounds(window) })),
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
        this.sendInteract(this.current, {
          kind: "drag-start",
          velocity: [0, 0],
        });
      drag.moved = true;
      const visible = drag.frames.filter(item => item.visible);
      if (!visible.length) return;
      let area = this.displayTarget ? this.preferredDisplay().workArea : screen.getDisplayNearestPoint(point).workArea;
      if (scene && (!pet || this.bubbleOpen || this.tasksExpanded)) {
        const assigned = companionDisplayAreas(this.current, this.snapshot.scale || 1, area, this.bubbleHeight);
        area = pet ? assigned.pet : assigned.panel;
      }
      const clamped = clampFrames(visible.map(item => ({ ...item.limit, x: item.limit.x + dx, y: item.limit.y + dy })),
        { x: area.x + 8, y: area.y + 8, width: area.width - 16, height: area.height - 16 });
      const moveX = clamped[0].x - visible[0].limit.x, moveY = clamped[0].y - visible[0].limit.y;
      if (pet || !scene) this.current.origin = { x: drag.origin.x + moveX, y: drag.origin.y + moveY };
      for (const item of drag.frames) if (!item.window.isDestroyed())
        this.geometry.setPosition(item.window, Math.round(item.frame.x + moveX), Math.round(item.frame.y + moveY));
      if (!pet && !scene) this.sendPet(this.current);
      if (pet) {
        const now = Date.now(),
          dt = Math.max(1, now - drag.time) / 1000;
        this.sendInteract(this.current, {
          kind: "drag-move",
          velocity: [
            (point.x - drag.last.x) / dt,
            -(point.y - drag.last.y) / dt,
          ],
        });
        // 画布跟随人物移动，整屏渲染层保持不动。
        this.sendPet(this.current);
        drag.last = point;
        drag.time = now;
      }
    } else {
      this.drag = null;
      if (pet) {
        this.sendInteract(this.current, {
          kind: drag.moved || phase === "cancel" ? "drag-end" : "tap",
          velocity: [0, 0],
        });
        if (!drag.moved && phase === "end") this.toggleBubble();
      }
      if (drag.moved) {
        if (scene) {
          if (pet) this.savePosition();
          return;
        }
        this.savePosition();
        const defaultFrame = this.desktopBubbleFrame(null);
        const actual = this.geometry.getBounds(this.bubble);
        this.offset = {
          x: actual.x - defaultFrame.x,
          y: actual.y - defaultFrame.y,
        };
      }
    }
  }
  savePosition() {
    if (!this.current) return;
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
  syncWakeWord() {
    this.wakeWord?.sync(this.client.online && this.state.voice === "off" && !this.companionGame() && !this.closed && this.wakeAllowed());
  }
  async answerWakeWord() {
    if (!this.wakeWord.enabled || this.closed || this.state.voice !== "off") return;
    await this.client.request("wake_voice", { greeting: wakeReply(this.state) });
  }
  stateChanged(state, event) {
    if (this.closed) return;
    const feedback = this.snapshot.feedback || "";
    const staleWakeError = event?.type === "voice.ready" &&
      /^(?:TimeoutError|千问唤醒问候超时，请再叫我一次。)$/.test(feedback);
    if (staleWakeError || /Engine (?:未连接|已断开)|^connect (?:ENOENT|ECONNREFUSED)/.test(feedback)) {
      this.snapshot.feedback = "";
      this.sendDesktop();
    }
    const previous = this.state;
    this.state = { ...previous, ...state };
    const type = event?.type;
    this.syncGameTracking();
    if (!this.tasks && this.hasTaskStatus() && !this.bubbleOpen && !avatarWindowsOpen(this.current)) this.showTasks(false);
    else this.syncTasksVisibility();
    if (type === "task.approval") {
      this.showBubble();
      this.showTasks(true);
    }
    if (type === "chat.window_command") {
      if (this.state.chat_window_action === 'open') this.showBubble();
      if (this.state.chat_window_action === 'close') this.hideBubble();
      if (this.state.chat_window_action === 'pin') this.pinChat();
    }
    if (previous.speaking !== this.state.speaking || previous.user_speaking !== this.state.user_speaking)
      this.scheduleCaptionClear();
    this.scheduleChatClose();
    if (this.current && (event?.type === "engine.ready" ||
        ["speaking", "user_speaking", "avatar_action", "avatar_action_id", "task",
          "task_text", "task_goal", "approval", "lol_companion_enabled", "game_companion_enabled"]
          .some(key => previous[key] !== this.state[key]))) this.sendPet(this.current);
    if (event?.type === "engine.ready") {
      this.engineReady = true;
      if (this.bubbleReady) void this.recoverNotifications();
    }
    this.syncWakeWord();
    if (event?.type === "engine.ready" || ["voice", "lol_companion_enabled",
        "game_companion_enabled", "game_companion_available"].some(key => previous[key] !== this.state[key]))
      this.updateMenu();
    const message = messageForEvent(type, this.state);
    if (type === 'notification.pending') this.lastPresentedNotification = this.state.notification_id;
    this.presentMessage(message, { stream: type === 'voice.assistant_text' });
    if (["task.succeeded", "task.failed", "task.blocked", "voice.error"].includes(type)) this.showBubble();
    if (type === "notification.system_requested")
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
    if (!id || !this.bubbleReady || this.notified.has(id)) return;
    if (id !== this.lastPresentedNotification) this.presentMessage({ text, source: '提醒' });
    this.notified.add(id);
    let channel = "ui";
    if (Notification.isSupported()) {
      new Notification({
        title: `${this.snapshot.name} 提醒`,
        body: text || "有一条新提醒",
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
    if (method === 'desktop_set_display_profile') return this.setDisplayProfile(params.profile);
    if (method === 'desktop_set_stage_background') return this.setStageBackground(params.background);
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
    if (method === "desktop_open_game_news") {
      const url = new URL(this.state.game_news_url);
      if (url.protocol !== "https:" || url.username || url.password)
        throw Error("新闻来源地址无效");
      await shell.openExternal(url.href);
      return { status: "opened" };
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
        pet.walk = null;
        pet.walkAt = null;
        if (this.displayTarget) {
          this.placePet(pet, this.preferredDisplay());
          this.savePosition();
          this.positionBubble();
          return this.sendDesktop();
        }
        const size = petSize(result.scale, this.area);
        pet.origin.x += (pet.viewport.width - size.width) / 2;
        pet.origin.y += pet.viewport.height - size.height;
        pet.viewport = size;
        pet.crop = pet.initialCrop
          ? scalePetCrop(pet.initialCrop, size)
          : { x: 0, y: 0, ...size };
        pet.origin = clampFrame({ ...pet.origin, ...size }, this.area);
        this.geometry.setBounds(pet.window, {
          x: Math.round(pet.origin.x + pet.crop.x),
          y: Math.round(pet.origin.y + pet.crop.y),
          width: Math.round(pet.crop.width),
          height: Math.round(pet.crop.height),
        });
        this.sendPet(pet);
        this.savePosition();
        this.positionBubble();
      }
      return this.sendDesktop();
    }
    if (method === "desktop_save_persona") {
      this.snapshot.name = result.name;
      this.wakeWord?.setName(result.name);
      this.tray?.setToolTip(`BoxAgent · ${result.name} · ⌃⌥空格切换麦克风`);
      this.sendDesktop();
    }
    return result;
  }
  close() {
    this.musicMonitor?.stop();
    this.closed = true;
    this.wakeWord?.setEnabled(false);
    clearInterval(this.pointerTimer);
    clearInterval(this.gameDisplayTimer);
    clearTimeout(this.chatCloseTimer);
    clearTimeout(this.captionTimer);
    for (const event of ['display-added', 'display-removed', 'display-metrics-changed'])
      screen.removeListener(event, this.displayChanged);
    globalShortcut.unregisterAll();
    this.tray?.destroy();
    const pending = this.pending;
    this.pending = null;
    this.current = null;
    this.drag = null;
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(Error("桌宠已关闭"));
    }
    for (const window of this.windows.keys())
      if (!window.isDestroyed()) window.destroy();
  }
}
module.exports = { DesktopHost };
