import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from 'three';
import {MotionBank} from '../motion-bank.js';
import {configureLights,configureRenderer} from '../render-profile.js';
const profile=JSON.parse(fs.readFileSync(new URL('../mate-motion-profile.json',import.meta.url)));
test('自动状态只引用实际动作清单，待机顺序来自原版预制体范围',()=>{
 const available=new Set(profile.entries.map(e=>e.id));
 assert.equal(available.size,128);assert.equal(profile.motionSet.idle.length,11);
 assert.equal(profile.motionSet.idle[0],'PET_IDLE/PET_IDLE');
 assert.equal(profile.motionSet.idle[1],'UPDATE_2/PET_IDLE_UPDATE2_01');
 for(const role of Object.values(profile.motionSet))for(const id of Array.isArray(role)?role:[role])assert.ok(available.has(id));
 assert.equal(profile.motionSettings['PET_IDLE/PET_IDLE'].timeScale,0.3);
 assert.equal(profile.motionSettings['PET_IDLE/PET_IDLE_14'].timeScale,0.4);
 assert.equal(profile.motionSettings['PET_IDLE/PET_IDLE_15'].timeScale,0.4);
 assert.equal(profile.motionSettings[profile.motionSet.sleeping].timeScale,0.6);
});
test('动作时长按实际播放速度换算，完整目录不会全部在启动时加载',()=>{
 const manifest={actions:{slow:'slow.vrma',other:'other.vrma'},motionSet:{idle:['slow']},motionSettings:{slow:{timeScale:0.4}}};
 const bank=new MotionBank({},manifest,()=>{});
 bank.clips.set('slow',new THREE.AnimationClip('slow',4,[]));
 assert.equal(bank.idleActions()[0].duration,10);
 assert.equal(bank.catalog().length,2);assert.equal(bank.catalog().find(x=>x.name==='other').loaded,false);
});
test('Web 版灯光、透明背景和软阴影配置被实际应用',()=>{
 const scene=new THREE.Scene();configureLights(scene);
 const key=scene.children.find(x=>x.isDirectionalLight),fill=scene.children.find(x=>x.isHemisphereLight);
 assert.equal(key.intensity,2.4);assert.deepEqual(key.position.toArray(),[2.5,4.5,4]);
 assert.equal(fill.intensity,2.1);assert.equal(fill.color.getHex(),0xcfd8ff);assert.equal(fill.groundColor.getHex(),0x2b2b2b);
 const renderer={shadowMap:{},setClearColor(color,alpha){this.alpha=alpha;}};configureRenderer(renderer);
 assert.equal(renderer.alpha,0);assert.equal(renderer.shadowMap.enabled,true);assert.equal(renderer.shadowMap.type,THREE.PCFSoftShadowMap);
});
