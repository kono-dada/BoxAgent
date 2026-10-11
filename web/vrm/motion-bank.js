import {repairMateAnimation} from './mate-animation-compat.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {VRMAnimationLoaderPlugin,createVRMAnimationClip} from '@pixiv/three-vrm-animation';

// 清单覆盖本地全部动作；启动只加载自动状态使用的资源，其余按需载入。
export class MotionBank {
  constructor(vrm,manifest,warn) {
    this.vrm=vrm;this.manifest=manifest;this.warn=warn;
    this.clips=new Map();this.pending=new Map();this.rejected=[];this.repairs=[];
    this.loader=new GLTFLoader();this.loader.register(parser=>new VRMAnimationLoaderPlugin(parser));
    this.roles=manifest.motionSet || {idle:['idle'],speaking:'speaking',success:'success',dragging:'dragging'};
  }
  async load() {
    const names=[...new Set([...(this.roles.idle || []),this.roles.speaking,this.roles.success,this.roles.dragging,this.roles.sleeping].filter(Boolean))];
    await Promise.all(names.map(name=>this.loadMotion(name).catch(error=>{
      this.rejected.push({name,reason:String(error)});this.warn(`跳过动作 ${name}：${error}`);
    })));
    return this;
  }
  loadMotion(name) {
    if(this.clips.has(name))return Promise.resolve(this.clips.get(name));
    if(this.pending.has(name))return this.pending.get(name);
    const file=this.manifest.actions[name];
    if(!file)return Promise.reject(new Error('动作不在本地清单中'));
    const promise=this.loader.loadAsync(`asset/${file}`).then(gltf=>{
      const animation=gltf.userData.vrmAnimations?.[0];
      if(!animation)throw new Error('文件没有 VRM 动作');
      const repaired=repairMateAnimation(animation,gltf.parser.json);
      if(repaired.length)this.repairs.push({name,bones:repaired});
      const clip=createVRMAnimationClip(animation,this.vrm);
      const nodes=new Set(Object.values(this.vrm.humanoid.normalizedHumanBones).map(bone=>bone.node.name));
      // 人形旋转与表情仍分层处理；不把窗口根位移混入局部骨架。
      clip.tracks=clip.tracks.filter(track=>track.name.endsWith('.quaternion') && nodes.has(track.name.slice(0,-11)));
      if(!clip.tracks.length)throw new Error('模型没有与动作匹配的骨骼轨道');
      if(!Number.isFinite(clip.duration) || clip.duration<=0)throw new Error('动作时长无效');
      clip.name=name;this.clips.set(name,clip);return clip;
    }).finally(()=>this.pending.delete(name));
    this.pending.set(name,promise);return promise;
  }
  settings(name) {
    const value=this.manifest.motionSettings?.[name] || {};
    return {timeScale:Number.isFinite(value.timeScale) && value.timeScale>0?value.timeScale:1,
      cycleOffset:Number.isFinite(value.cycleOffset)?value.cycleOffset:0,loop:value.loop!==false};
  }
  idleActions() {
    return (this.roles.idle || []).filter(name=>this.clips.has(name)).map(name=>({
      name,duration:this.clips.get(name).duration/this.settings(name).timeScale,timeScale:this.settings(name).timeScale,
    }));
  }
  catalog() {
    return Object.keys(this.manifest.actions).map(name=>({name,...this.settings(name),loaded:this.clips.has(name)}));
  }
}
