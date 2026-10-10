import {configureRenderer,configureLights,configureAvatar,WEB_RENDER_PROFILE} from './render-profile.js';
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { MotionBank } from './motion-bank.js';
import { AvatarAnimator } from './avatar-animator.js';
import { SpringPhysics } from './spring-physics.js';

const report = (type, detail = {}) => window.webkit?.messageHandlers.pet.postMessage({ type, ...detail });
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
configureRenderer(renderer);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
configureLights(scene);
const camera = new THREE.OrthographicCamera();
let vrm, animator, physics, height = 1, width = 1, state = 'idle', elapsed = 0;
let disposed = false, frames = 0, last = 0, started = 0, suspended = false;
const warnings = [];
function resize() {
  renderer.setSize(innerWidth, innerHeight);
  const half = Math.max(height * 0.72, width * 0.56 * innerHeight / innerWidth);
  camera.left = -half * innerWidth / innerHeight; camera.right = -camera.left;
  camera.top = half; camera.bottom = -half;
  camera.near = 0.01; camera.far = 100; camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
function update(dt) {
  physics.beginFrame();
  const steps = Math.max(1, Math.ceil(dt * 60)), step = dt / steps;
  for (let i = 0; i < steps; i++) {
    animator.update(step);
    vrm.humanoid.update(); vrm.scene.updateMatrixWorld(true);
    vrm.update(step); animator.afterUpdate();
  }
  elapsed += dt;
}
function frame(now) {
  if (disposed) return;
  requestAnimationFrame(frame);
  if (document.hidden || suspended) { last = now; return; }
  const interval = animator?.behavior.dragging ? 1000 / 60 : 1000 / 30;
  if (now - last < interval - 0.7) return;
  const delta = Math.min((now - (last || now)) / 1000, 0.08);
  last = now;
  if (!animator) return;
  update(delta); renderer.render(scene, camera); frames++;
}
function snapshot() {
  renderer.render(scene, camera);
  return { image: renderer.domElement.toDataURL('image/png'), behavior: animator.debug() };
}
window.pet = {
  motions() { return animator?.bank.catalog() || []; },
  async playMotion(name) {
    if (!animator) throw new Error('模型尚未准备好');
    const clip = await animator.bank.loadMotion(name);
    const b = animator.behavior;
    b.gesture = {name,start:b.time,duration:clip.duration/animator.bank.settings(name).timeScale};
    return animator.bank.settings(name);
  },
  present(value) { state = value.state; animator?.behavior.present(value); },
  interact(value) { animator?.behavior.interact(value); physics?.interact(value); },
  debug() { return { ready: Boolean(animator), state, frames, warnings, elapsed,
    visibility: document.visibilityState, seconds: (performance.now() - started) / 1000,
    behavior: animator?.debug(), physics:physics?.debug(), modelVersion:vrm?.meta.metaVersion, lighting:WEB_RENDER_PROFILE }; },
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
  screenshot() { renderer.render(scene, camera); return renderer.domElement.toDataURL('image/png'); },
  dispose() {
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
  const bank = await new MotionBank(vrm, manifest, message => {
    warnings.push(message); report('warning', {message});
  }).load();
  physics = new SpringPhysics(vrm,manifest.physicsSettings);
  animator = new AvatarAnimator(vrm, scene, box, bank, manifest, camera, () => ({width:innerWidth,height:innerHeight}));
  update(1 / 30); started = performance.now(); renderer.render(scene, camera);
  report('ready'); requestAnimationFrame(frame);
})().catch(error => report('error', { message:String(error) }));
