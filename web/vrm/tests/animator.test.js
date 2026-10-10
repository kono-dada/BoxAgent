import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {AvatarAnimator} from '../avatar-animator.js';
function fixture(timeScale=1){
 globalThis.document={createElement:()=>({getContext:()=>({createRadialGradient:()=>({addColorStop(){}}),fillRect(){}})})};
 const scene=new THREE.Scene(),root=new THREE.Group();scene.add(root);
 const hips=new THREE.Bone(),head=new THREE.Bone(),arm=new THREE.Bone();
 hips.name='hips';head.name='head';arm.name='arm';root.add(hips);hips.add(head,arm);head.position.y=1;
 const bones={hips:{node:hips},head:{node:head},leftUpperArm:{node:arm}};
 const track=(bone,angle)=>{const q=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),angle).toArray();return new THREE.QuaternionKeyframeTrack(bone+'.quaternion',[0,1],[...q,...q]);};
 const clips=new Map([['idle',new THREE.AnimationClip('idle',1,[track('hips',0),track('arm',0)])],['drag',new THREE.AnimationClip('drag',1,[track('hips',0.4),track('arm',0.9)])]]);
 const vrm={scene:root,meta:{metaVersion:'1'},humanoid:{normalizedHumanBones:bones,getRawBoneNode:n=>bones[n]?.node}};
 const bank={settings:()=>({timeScale,cycleOffset:0}),roles:{dragging:'drag'},clips,rejected:[],idleActions:()=>[{name:'idle',duration:1,weight:1}]};
 const animator=new AvatarAnimator(vrm,scene,new THREE.Box3(new THREE.Vector3(-0.3,0,-0.1),new THREE.Vector3(0.3,1.4,0.1)),bank,{});
 return {animator,hips,arm};
}
test('持续拖动保留动画中的躯干与手臂姿势，不被程序放松姿势覆盖',()=>{
 const {animator,hips,arm}=fixture();animator.behavior.interact({kind:'drag-start'});
 for(let i=0;i<200;i++)animator.update(1/60);
 assert.equal(animator.current,'drag');assert.equal(animator.active.loop,THREE.LoopRepeat);
 assert.ok(Math.abs(hips.rotation.x-0.4)<1e-5);assert.ok(Math.abs(arm.rotation.x-0.9)<1e-5);
 animator.dispose();
});
test('摆动在动画后附加，下一帧移除，不能积累到骨骼姿势里',()=>{
 const {animator,hips}=fixture();
 for(let i=0;i<120;i++){animator.behavior.interact({kind:'drag-move',velocity:[1200,0]});animator.update(1/60);}
 for(let i=0;i<240;i++)animator.update(1/60);
 const expected=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),0.4);
 assert.ok(hips.quaternion.angleTo(expected)<1e-4);
 animator.behavior.interact({kind:'drag-end'});for(let i=0;i<60;i++)animator.update(1/60);
 assert.equal(animator.current,'idle');assert.ok(hips.quaternion.angleTo(new THREE.Quaternion())<1e-4);
 animator.dispose();
});

test('动画求值使用素材独立速度，0.4 倍不会退回 1 倍',()=>{
 const {animator}=fixture(0.4);
 for(let i=0;i<60;i++)animator.update(1/60);
 assert.equal(animator.active.getEffectiveTimeScale(),0.4);
 assert.ok(Math.abs(animator.active.time-0.4)<1e-6);
 animator.behavior.interact({kind:'drag-start'});
 for(let i=0;i<60;i++)animator.update(1/60);
 assert.ok(Math.abs(animator.active.time-0.4)<1e-6);
 animator.dispose();
});
