import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {repairMateAnimation} from '../mate-animation-compat.js';
import {SpringPhysics} from '../spring-physics.js';
import {MateTracking} from '../mate-tracking.js';
const q = (x,y=0) => new THREE.Quaternion().setFromEuler(new THREE.Euler(x,y,0));
test('旧导出包的脱离骨骼还原局部旋转，重组后的世界旋转保持原样',()=>{
 const names=['hips','spine','chest','neck','head','leftShoulder','leftUpperArm'];
 const humanBones=Object.fromEntries(names.map((name,node)=>[name,{node}]));
 const json={asset:{generator:'MateEngineAnimToVrma (Unity Editor)'},nodes:[{}, {children:[0]}, {children:[1]}, {}, {children:[3]}, {children:[2]}, {children:[5]}],extensions:{VRMC_vrm_animation:{humanoid:{humanBones}}}};
 const world=q(0.7,0.1), parent=q(0.5).multiply(q(0.1)).multiply(q(-0.05));
 const values=[q(0.5),q(0.1),q(-0.05),world,q(0.02),q(0),q(0)];
 const rotation=new Map(names.map((name,i)=>[name,new THREE.QuaternionKeyframeTrack(name,[0,1],[...values[i].toArray(),...values[i].toArray()])]));
 const animation={humanoidTracks:{rotation}};
 assert.deepEqual(repairMateAnimation(animation,json),['neck']);
 const repaired=new THREE.Quaternion().fromArray(rotation.get('neck').values);
 assert.ok(parent.multiply(repaired).angleTo(world)<0.001);
 const untouched=rotation.get('neck').values.slice();json.asset.generator='标准 VRMA';
 assert.deepEqual(repairMateAnimation(animation,json),[]);assert.deepEqual(rotation.get('neck').values,untouched);
});
function springFixture(version){
 const settings={gravityPower:0.2,gravityDir:new THREE.Vector3(0,-1,0),stiffness:0.6,dragForce:0.3,hitRadius:0.04};
 const vrm={meta:{metaVersion:version},scene:new THREE.Group(),springBoneManager:{joints:new Set([{settings}]),reset(){}}};
 return {physics:new SpringPhysics(vrm),settings};
}
test('VRM 0 窗口外力使用预制体强度，静止帧恢复作者重力，碰撞与阻尼不变',()=>{
 const {physics,settings}=springFixture('0');
 physics.interact({kind:'drag-move',velocity:[1000,1000]});physics.beginFrame();
 assert.ok(Math.abs(physics.force.length()-0.35)<1e-8);assert.ok(physics.force.x<0 && physics.force.y<0);
 assert.equal(settings.stiffness,0.6);assert.equal(settings.dragForce,0.3);assert.equal(settings.hitRadius,0.04);
 physics.beginFrame();assert.equal(settings.gravityPower,0.2);assert.deepEqual(settings.gravityDir.toArray(),[0,-1,0]);
 physics.dispose();assert.equal(settings.gravityPower,0.2);
});
test('VRM 1 按原版覆盖运行时重力，释放后清零，销毁恢复素材配置',()=>{
 const {physics,settings}=springFixture('1');physics.interact({kind:'drag-move',velocity:[50,0]});physics.beginFrame();
 assert.equal(settings.gravityPower,0.35);physics.interact({kind:'drag-end'});physics.beginFrame();assert.equal(settings.gravityPower,0);
 physics.dispose();assert.equal(settings.gravityPower,0.2);
});
test('头眼脊柱在拖拽允许状态继续跟随，骨骼角度保持限制',()=>{
 const root=new THREE.Group(),spine=new THREE.Bone(),head=new THREE.Bone(),eye=new THREE.Bone();root.add(spine);spine.add(head);head.add(eye);head.position.y=1;
 const camera=new THREE.OrthographicCamera(-1,1,1,-1,.01,10);camera.position.set(0,1,3);camera.lookAt(0,1,0);camera.updateMatrixWorld(true);
 const vrm={meta:{metaVersion:'1'},humanoid:{getNormalizedBoneNode:n=>({spine,head,leftEye:eye}[n])}};
 const tracking=new MateTracking(vrm,camera,()=>({width:200,height:200}));
 for(let i=0;i<120;i++){spine.quaternion.identity();head.quaternion.identity();eye.quaternion.identity();root.updateMatrixWorld(true);tracking.apply(1/60,[100,0],true);}
 assert.ok(head.quaternion.angleTo(new THREE.Quaternion())>0.01);
 assert.ok(head.quaternion.angleTo(new THREE.Quaternion())<Math.PI/3);
 assert.ok(eye.quaternion.angleTo(new THREE.Quaternion())<0.31);
 assert.equal(tracking.settings.headBlend,0.7);assert.equal(tracking.settings.spineBlend,0.5);
});
