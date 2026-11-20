import {configureRenderer,configureLights,configureAvatar,WEB_RENDER_PROFILE} from './render-profile.js';
import {createGroundShadow} from './ground-shadow.js';
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { MotionBank } from './motion-bank.js';
import { MusicDance } from './music-dance.js';
import { AvatarAnimator } from './avatar-animator.js';
import { SpringPhysics } from './spring-physics.js';
import { visibleBounds, visibleHitMask } from './visible-bounds.js';

const report = (type, detail = {}) => {
  const value = { type, ...detail };
  if (window.parent !== window) window.parent.postMessage({type:'boxagent-pet-report',value}, '*');
};
window.addEventListener('message', event => {
  if (event.source !== window.parent || event.data?.type !== 'boxagent-pet-command') return;
  const { method, value } = event.data;
  if (['present','interact','dispose','perform'].includes(method)) {
    Promise.resolve(window.pet?.[method](value)).catch(error => report('warning', {message:String(error)}));
  }
});
const previewTheme = new URLSearchParams(location.search).get('previewTheme');
function applyPreviewTheme(theme) {
  if (!['light', 'dark'].includes(theme)) return;
  document.documentElement.style.colorScheme = theme;
  document.documentElement.style.background = theme === 'dark' ? '#171717' : '#ffffff';
  document.body.style.background = 'transparent';
}
if (previewTheme) {
  applyPreviewTheme(previewTheme);
  window.addEventListener('message', event => {
    if (event.source === window.parent && event.data?.type === 'boxagent-preview-theme') applyPreviewTheme(event.data.theme);
  });
}
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
configureRenderer(renderer);
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
configureLights(scene);
const camera = new THREE.OrthographicCamera();
let vrm, animator, physics, height = 1, width = 1, state = 'idle', elapsed = 0;
let disposed = false, frames = 0, last = 0, started = 0, suspended = false;
let hiddenForGame = false;
let frameTimer;
let musicDance, musicActive = false;
let walkDirection = 0, locomotionStatus = '';
const walkNames = { '-1':'PET_LOCOMOTION/PET_WALK_LEFT', '1':'PET_LOCOMOTION/PET_WALK_RIGHT' };
let shadow = null;
const warnings = [];
const boundsCanvas = document.createElement('canvas');
const boundsContext = boundsCanvas.getContext('2d', {willReadFrequently:true});
let lastBounds = '', firstHitMask, firstPortrait, firstOverlayAnchors, firstOverlayPose, lastOverlay;
let overlaysVisible = false;
let boundsChecks = 0, boundsReports = 0, boundsMilliseconds = 0;
// 加载首帧和显式诊断时才测量边界，日常动画不扫描像素、不调整窗口。
function chatAnchor() {
  const leftArm = vrm.humanoid.getRawBoneNode('leftUpperArm');
  const rightArm = vrm.humanoid.getRawBoneNode('rightUpperArm');
  if (!leftArm || !rightArm) return undefined;
  const left = leftArm.getWorldPosition(new THREE.Vector3()).project(camera);
  const right = rightArm.getWorldPosition(new THREE.Vector3()).project(camera);
  const center = (left.x + right.x + 2) / 4;
  // Leave space beyond the bones for sleeves and the resting arm pose.
  const halfWidth = Math.abs(left.x - right.x);
  return { left: Math.max(0, center - halfWidth), right: Math.min(1, center + halfWidth), y: (2 - left.y - right.y) / 4 };
}
function avatarPortrait(bounds, width, height) {
  const head = vrm.humanoid.getRawBoneNode('head');
  if (!head) return undefined;
  const position = head.getWorldPosition(new THREE.Vector3()).project(camera);
  const size = bounds.height * height / innerHeight * .29;
  const x = (position.x + 1) * width / 2;
  const y = (1 - position.y) * height / 2 - size * .2;
  if (size < 1) return undefined;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 64;
  canvas.getContext('2d').drawImage(boundsCanvas, x - size / 2, y - size / 2, size, size, 0, 0, 64, 64);
  return canvas.toDataURL('image/png');
}
const overlayVector = new THREE.Vector3();
function overlayPose() {
  const bone = name => {
    const node = vrm.humanoid.getRawBoneNode(name);
    if (!node) return;
    const point = node.getWorldPosition(overlayVector).project(camera);
    return { x: (point.x + 1) / 2, y: (1 - point.y) / 2 };
  };
  const left = bone('leftFoot'), right = bone('rightFoot');
  return {
    head: bone('head'),
    feet: left && right ? { x: (left.x + right.x) / 2, y: Math.max(left.y, right.y) } : left || right,
  };
}
function overlayAnchors(pixels, w, h, bounds, pose) {
  const center = pose.head?.x ?? (bounds.x + bounds.width / 2) / innerWidth;
  // A VRM head bone starts near the neck. Place the bubble near the mouth.
  const faceY = pose.head ? pose.head.y - bounds.height / innerHeight * .043
    : 1 - (bounds.y + bounds.height * .82) / innerHeight;
  const row = Math.max(0, Math.min(h - 1, Math.round(faceY * h)));
  const opaque = (x, y) => pixels[(y * w + x) * 4 + 3] >= 192;
  let left = w, right = -1;
  for (let x = 0; x < w; x++) {
    if (!opaque(x, row)) continue;
    left = Math.min(left, x);
    right = x;
  }
  // The translucent shadow and the interaction crop padding are below the shoes.
  let bottom = h - 1;
  while (bottom >= 0) {
    let visible = false;
    for (let x = 0; x < w && !visible; x++) visible = opaque(x, bottom);
    if (visible) break;
    bottom--;
  }
  const ground = shadow?.shadow.position.clone().project(camera);
  const radius = bounds.height / innerWidth * .105;
  return {
    face: {
      left: Math.max(center - radius, right >= 0 ? left / w : center - .06),
      right: Math.min(center + radius, right >= 0 ? (right + 1) / w : center + .06),
      y: faceY,
    },
    feet: {
      x: pose.feet?.x ?? center,
      y: bottom >= 0 ? (bottom + 1) / h : ground ? (1 - ground.y) / 2 : 1 - bounds.y / innerHeight,
    },
  };
}
function reportOverlayAnchors() {
  if (!overlaysVisible || !firstOverlayAnchors) return;
  const current = overlayPose();
  const { face, feet } = firstOverlayAnchors;
  const shift = (point, original, axis) => Math.round(((point?.[axis] ?? 0) - (original?.[axis] ?? 0))
    * (axis === 'x' ? innerWidth : innerHeight));
  const dx = shift(current.head, firstOverlayPose.head, 'x');
  const dy = shift(current.head, firstOverlayPose.head, 'y');
  const footX = shift(current.feet, firstOverlayPose.feet, 'x');
  const footY = shift(current.feet, firstOverlayPose.feet, 'y');
  const position = {
    face: { left: face.left + dx / innerWidth, right: face.right + dx / innerWidth, y: face.y + dy / innerHeight },
    feet: { x: feet.x + footX / innerWidth, y: feet.y + footY / innerHeight },
  };
  const signature = JSON.stringify(position);
  if (signature === lastOverlay) return;
  lastOverlay = signature;
  report('overlay-anchors', { anchors: position });
}
function reportBounds() {
  const now = performance.now();
  boundsChecks++;
  const ratio = Math.min(1, 256 / Math.max(innerWidth, innerHeight));
  const w = Math.ceil(innerWidth * ratio), h = Math.ceil(innerHeight * ratio);
  if (boundsCanvas.width !== w || boundsCanvas.height !== h) {
    boundsCanvas.width = w; boundsCanvas.height = h;
  }
  boundsContext.clearRect(0, 0, w, h);
  boundsContext.drawImage(renderer.domElement, 0, 0, w, h);
  const pixels = boundsContext.getImageData(0, 0, w, h).data;
  const bounds = visibleBounds(pixels, w, h, innerWidth, innerHeight);
  boundsMilliseconds += performance.now() - now;
  if (!bounds) return;
  firstHitMask ??= visibleHitMask(pixels, w, h);
  firstPortrait ??= avatarPortrait(bounds, w, h);
  firstOverlayPose ??= overlayPose();
  firstOverlayAnchors ??= overlayAnchors(pixels, w, h, bounds, firstOverlayPose);
  const detail = {bounds, viewport:[innerWidth, innerHeight], chatAnchor: chatAnchor(),
    hitMask: firstHitMask, portrait: firstPortrait, overlayAnchors: firstOverlayAnchors};
  const signature = JSON.stringify(detail);
  if (signature !== lastBounds) { lastBounds = signature; boundsReports++; report('bounds', detail); }
}
function resize() {
  // CSS sizes the canvas. Keep its drawing buffer at native density up to 2×.
  renderer.setDrawingBufferSize(innerWidth, innerHeight, Math.min(window.devicePixelRatio || 1, 2));
  const half = Math.max(height * 0.72, width * 0.56 * innerHeight / innerWidth);
  camera.left = -half * innerWidth / innerHeight; camera.right = -camera.left;
  camera.top = half; camera.bottom = -half;
  camera.near = 0.01; camera.far = 100; camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
let resolution;
function watchResolution() {
  resolution?.removeEventListener('change', watchResolution);
  resolution = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
  resolution.addEventListener('change', watchResolution);
  // A move between equal-size viewports can change density without a resize event.
  if (renderer.getPixelRatio() !== Math.min(window.devicePixelRatio || 1, 2)) resize();
}
watchResolution();
function update(dt) {
  musicDance?.update();
  const b = animator.behavior;
  const available = Object.values(walkNames).every(name => animator.bank.clips.has(name));
  const eligible = available && b.state === 'idle' && !b.dragging && !b.mouseHeld &&
    !b.gesture && !b.musicGesture && !musicActive && !b.sleep;
  b.walkMotion = eligible ? walkNames[walkDirection] : null;
  const status = JSON.stringify({eligible, walking:Boolean(b.walkMotion)});
  if (status !== locomotionStatus) {
    locomotionStatus = status;
    report('locomotion', {eligible, walking:Boolean(b.walkMotion)});
  }
  physics.beginFrame();
  const active = state === 'speaking' || b.dragging || b.gesture || b.musicGesture || b.walkMotion;
  const steps = Math.max(1, Math.ceil(dt * (active ? 60 : 30))), step = dt / steps;
  for (let i = 0; i < steps; i++) {
    animator.update(step);
    vrm.humanoid.update(); vrm.scene.updateMatrixWorld(true);
    vrm.update(step); animator.afterUpdate();
  }
  elapsed += dt;
}
function frame(now) {
  if (disposed) return;
  if (document.hidden || suspended || hiddenForGame) {
    last = now;
    frameTimer = setTimeout(() => requestAnimationFrame(frame), 250);
    return;
  }
  const behavior = animator?.behavior;
  const active = state === 'speaking' || behavior?.dragging || behavior?.gesture ||
    behavior?.musicGesture || behavior?.walkMotion;
  const interval = 1000 / (active ? 30 : 15);
  const delta = Math.min((now - (last || now)) / 1000, 0.08);
  last = now;
  if (animator) {
    update(delta); renderer.render(scene, camera); frames++;
    reportOverlayAnchors();
  }
  // Skipping renders inside a perpetual rAF still wakes Chromium's compositor
  // at the display refresh rate. Request the next frame only when it is due.
  frameTimer = setTimeout(() => requestAnimationFrame(frame),
    Math.max(0, interval - (performance.now() - now)));
}
function snapshot() {
  renderer.render(scene, camera);
  reportBounds();
  reportOverlayAnchors();
  return { image: renderer.domElement.toDataURL('image/png'), behavior: animator.debug() };
}
const semanticMotions = Object.freeze({
  greeting:'PET_INTRO/PET_INTRO',
  hover:'PET_MISC/HoverReaction',
  dance:'PET_DANCING/PET_DANCING_5',
  happy:'PET_MISC/PET_HAPPY',
  laugh:'PET_MISC/PET_LAUGHING',
  shy:'PET_MISC/PET_SHY_POINT',
});
let actionSequence = 0, lastHoverAt = -Infinity, lastActionId = '';
window.pet = {
  bounds() { return lastBounds ? JSON.parse(lastBounds) : null; },
  motions() { return animator?.bank.catalog() || []; },
  async playMotion(name) {
    if (!animator) throw new Error('模型尚未准备好');
    const clip = await animator.bank.loadMotion(name);
    const b = animator.behavior;
    b.gesture = {name,start:b.time,duration:clip.duration/animator.bank.settings(name).timeScale};
    return animator.bank.settings(name);
  },
  async perform(action) {
    if (!Object.hasOwn(semanticMotions, action) || !animator) return;
    if (musicActive) return;
    const b = animator.behavior;
    if (b.dragging) return;
    if (action === 'hover') {
      if (b.gesture || !['idle','listening'].includes(b.state) || performance.now() - lastHoverAt < 15000) return;
      lastHoverAt = performance.now();
    }
    const sequence = ++actionSequence;
    const name = semanticMotions[action];
    const clip = await animator.bank.loadMotion(name);
    if (sequence !== actionSequence || b.dragging || disposed || musicActive) return;
    b.gesture = {name,start:b.time,duration:Math.min(12,clip.duration/animator.bank.settings(name).timeScale),semantic:action};
  },
  present(value) {
    hiddenForGame = value.hiddenForGame === true;
    if (!overlaysVisible && value.overlaysVisible) lastOverlay = undefined;
    overlaysVisible = value.overlaysVisible === true && !hiddenForGame;
    walkDirection = value.walkDirection === -1 || value.walkDirection === 1 ? value.walkDirection : 0;
    musicActive = value.music === true;
    musicDance?.setActive(musicActive);
    state = value.state; animator?.behavior.present(value);
    if (animator && value.actionId && value.actionId !== lastActionId) {
      lastActionId = value.actionId;
      void window.pet.perform(value.action).catch(error => report('warning',{message:String(error)}));
    }
  },
  interact(value) {
    if (value.kind === 'drag-start' || value.kind === 'drag-move') actionSequence++;
    animator?.behavior.interact(value); physics?.interact(value);
  },
  debug() { return { ready: Boolean(animator), state, frames, warnings, elapsed,
    visibility: document.visibilityState, seconds: (performance.now() - started) / 1000,
    behavior: animator?.debug(), physics:physics?.debug(), modelVersion:vrm?.meta.metaVersion, lighting:WEB_RENDER_PROFILE,
    windowSizing:{checks:boundsChecks,reports:boundsReports,milliseconds:boundsMilliseconds} }; },
  // 原生验收推进同一套行为与渲染逻辑，不引入角色专用的测试动画。
  preview(name, seconds = 1) {
    if (!animator) return null;
    suspended = true;
    const b = animator.behavior;
    if (name !== 'sleep') b.wake();
    if (!name.startsWith('drag-') && name !== 'release') {
      if (b.dragging) b.interact({kind:'drag-end'});
      b.present({state:'idle',pointer:[0,0]});
      b.gesture = null;
    }
    if (name === 'drag-left' || name === 'drag-right') b.interact({kind:'drag-start',velocity:[name === 'drag-left' ? -800 : 800,0]});
    else if (name === 'release') b.interact({kind:'drag-end'});
    else if (name === 'tap') b.interact({kind:'tap'});
    else if (name === 'sleep') b.lastInteraction = b.time - 160;
    else if (['speaking','listening','success','error','working','approval','idle'].includes(name)) b.present({state:name});
    else b.startGesture(name);
    for (let i = 0; i < Math.ceil(seconds * 60); i++) {
      if (name === 'drag-left' || name === 'drag-right') window.pet.interact({kind:'drag-move',velocity:[name === 'drag-left' ? -800 : 800,0]});
      update(1 / 60);
    }
    return snapshot();
  },
  advance(seconds) {
    if (!animator) return null;
    suspended = true;
    for (let i = 0; i < Math.ceil(Math.min(seconds, 10) * 60); i++) update(1 / 60);
    return snapshot();
  },
  resume() { suspended = false; last = performance.now(); },
  screenshot() { renderer.render(scene, camera); reportBounds(); return renderer.domElement.toDataURL('image/png'); },
  dispose() {
    clearTimeout(frameTimer);
    window.removeEventListener('resize', resize);
    resolution.removeEventListener('change', watchResolution);
    musicDance?.dispose();
    shadow?.dispose(); shadow = null;
    disposed = true; physics?.dispose(); animator?.dispose();
    if (vrm) VRMUtils.deepDispose(vrm.scene);
    renderer.dispose();
  }
};
window.addEventListener('error', event => report('error', { message:event.message }));
window.addEventListener('unhandledrejection', event => report('error', { message:String(event.reason) }));
(async () => {
  const manifest = await (await fetch('asset/pet.json')).json();
  const loader = new GLTFLoader();
  loader.register(parser => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(`asset/${manifest.model}`);
  vrm = gltf.userData.vrm;
  if (!vrm?.humanoid) throw new Error('模型需要有效的 VRM 人形骨架');
  VRMUtils.rotateVRM0(vrm); scene.add(vrm.scene); configureAvatar(vrm.scene);
  // 可选部件由素材配置决定，与动画框架无关。
  vrm.scene.traverse(object => { if ((manifest.hiddenMeshes || []).includes(object.name)) object.visible = false; });
  vrm.scene.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(vrm.scene);
  const center = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
  height = size.y; width = size.x;
  if (!Number.isFinite(height) || height <= 0) throw new Error('模型尺寸无效');
  camera.position.set(center.x, center.y, center.z + height * 3); camera.lookAt(center); resize();
  // 角色脚下的落地阴影。
  shadow = createGroundShadow(scene, box, size);
  const bank = await new MotionBank(vrm, manifest, message => {
    warnings.push(message); report('warning', {message});
  }).load();
  physics = new SpringPhysics(vrm,manifest.physicsSettings);
  animator = new AvatarAnimator(vrm, scene, box, bank, manifest, camera, () => ({width:innerWidth,height:innerHeight}));
  for (const name of Object.values(walkNames)) {
    if (manifest.actions[name]) void bank.loadMotion(name).catch(error => report('warning',{message:String(error)}));
  }
  musicDance = new MusicDance(bank,animator.behavior,Math.random,
    message=>report('warning',{message}));
  musicDance.setActive(musicActive);
  animator.behavior.present({state});
  if (window.parent !== window) {
    void window.pet.perform('greeting').catch(error => report('warning',{message:String(error)}));
  }
  update(1 / 30); started = performance.now(); renderer.render(scene, camera);
  reportBounds(); report('ready'); requestAnimationFrame(frame);
})().catch(error => report('error', { message:String(error) }));
