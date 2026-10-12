import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {AvatarAnimator} from '../avatar-animator.js';
function fixture(timeScale=1,withLegs=false){
 globalThis.document={createElement:()=>({getContext:()=>({createRadialGradient:()=>({addColorStop(){}}),fillRect(){}})})};
 const scene=new THREE.Scene(),root=new THREE.Group();scene.add(root);
 const hips=new THREE.Bone(),head=new THREE.Bone(),arm=new THREE.Bone();
 hips.name='hips';head.name='head';arm.name='arm';root.add(hips);hips.add(head,arm);head.position.y=1;
 const bones={hips:{node:hips},head:{node:head},leftUpperArm:{node:arm}};
 if(withLegs){
  hips.position.y=1;
  for(const [side,sign] of [['left',-1],['right',1]]){
   let parent=hips;
   for(const [suffix,offset] of [['UpperLeg',[sign*0.1,0,0]],['LowerLeg',[0,-0.45,0.025]],['Foot',[0,-0.5,-0.025]]]){
    const node=new THREE.Bone();node.name=side+suffix;node.position.fromArray(offset);
    parent.add(node);parent=node;bones[node.name]={node};
   }
  }
 }
 const track=(bone,angle)=>{const q=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),angle).toArray();return new THREE.QuaternionKeyframeTrack(bone+'.quaternion',[0,1],[...q,...q]);};
 const clips=new Map([['idle',new THREE.AnimationClip('idle',1,[track('hips',0),track('arm',0)])],['drag',new THREE.AnimationClip('drag',1,[track('hips',0.4),track('arm',0.9)])]]);
 if(withLegs)for(const clip of clips.values())clip.tracks.push(new THREE.VectorKeyframeTrack('hips.position',[0,0.5,1],[0,1,0,0.1,0.85,0.05,0,1,0]),track('leftFoot',0.2),track('rightFoot',-0.3));
 const vrm={scene:root,meta:{metaVersion:'1'},humanoid:{normalizedHumanBones:bones,getRawBoneNode:n=>bones[n]?.node}};
 const bank={settings:()=>({timeScale,cycleOffset:0}),roles:{dragging:'drag'},clips,rejected:[],idleActions:()=>[{name:'idle',duration:1,weight:1}]};
 const animator=new AvatarAnimator(vrm,scene,new THREE.Box3(new THREE.Vector3(-0.3,0,-0.1),new THREE.Vector3(0.3,1.4,0.1)),bank,{});
 return {animator,hips,arm,bones};
}
test('持续拖动保留动画中的躯干与手臂姿势，不被程序放松姿势覆盖',()=>{
 const {animator,hips,arm}=fixture();animator.behavior.interact({kind:'drag-start'});
 for(let i=0;i<200;i++)animator.update(1/60);
 assert.equal(animator.current,'drag');assert.equal(animator.active.loop,THREE.LoopRepeat);
 assert.ok(Math.abs(hips.rotation.x-0.4)<1e-5);assert.ok(Math.abs(arm.rotation.x-0.9)<1e-5);
 animator.dispose();
});

test('保留髋部位移和脚部旋转，脚能随动作移动且跨帧不累积附加位移',()=>{
 const {animator,hips,arm,bones}=fixture(1,true);
 const initial=bones.leftFoot.node.getWorldPosition(new THREE.Vector3());
 let movement=0;
 for(let i=0;i<600;i++){
  if(i%120===0)animator.behavior.interact({kind:'drag-start'});
  if(i%120===60)animator.behavior.interact({kind:'drag-end'});
  if(i%120<60)animator.behavior.interact({kind:'drag-move',velocity:[1200,600]});
  animator.update(i%3===0?1/30:1/60);
  movement=Math.max(movement,bones.leftFoot.node.getWorldPosition(new THREE.Vector3()).distanceTo(initial));
  if(i%120===59 || i%120===119){
   const positionTrack=animator.active.getClip().tracks.find(track=>track.name==='hips.position');
   const expected=new THREE.Vector3().fromArray(positionTrack.createInterpolant().evaluate(animator.active.time));
   assert.ok(hips.position.distanceTo(expected)<1e-6);
   assert.ok(Math.abs(bones.leftFoot.node.rotation.x-0.2)<1e-6);
   assert.ok(Math.abs(bones.rightFoot.node.rotation.x+0.3)<1e-6);
  }
  assert.ok(hips.position.length()<1.1);
 }
 assert.ok(movement>0.05);
 for(let i=0;i<120;i++)animator.update(1/60);
 assert.ok(Math.abs(arm.rotation.x)<1e-5);
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
