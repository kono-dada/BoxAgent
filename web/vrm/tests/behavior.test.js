import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PetBehavior} from '../behavior.js';
const idleActions=Array.from({length:3},(_,i)=>({name:'idle-'+i,duration:3,weight:1}));
const create=(settings={})=>new PetBehavior(()=>0.5,{idleActions,roles:{dragging:'drag',speaking:'talk'},settings});
function advance(b,seconds,dt=1/60){for(let i=0;i<Math.ceil(seconds/dt);i++)b.step(dt);}
test('待机按预制体参数每十秒循环索引，拖动中索引继续推进',()=>{
 const b=create();assert.equal(b.motion().name,'idle-0');
 advance(b,10.1);assert.equal(b.motion().name,'idle-1');
 b.interact({kind:'drag-start'});advance(b,10);assert.equal(b.motion().name,'drag');assert.equal(b.idleIndex,2);
 b.interact({kind:'drag-end'});advance(b,0.1);assert.equal(b.motion().name,'idle-2');
 advance(b,10);assert.equal(b.motion().name,'idle-0');
});
test('按下立即进入 Drag，快速松开也保持至少 0.3 秒',()=>{
 const b=create();b.interact({kind:'drag-start'});assert.equal(b.motion().name,'drag');
 advance(b,0.05);b.interact({kind:'drag-end'});advance(b,0.1);assert.equal(b.dragging,true);
 advance(b,0.2);assert.equal(b.dragging,false);assert.equal(b.motion().name,'idle-0');
});
test('Drag 覆盖说话但不丢失业务状态，持续移动不会重置最短保持计时',()=>{
 const b=create();b.present({state:'speaking'});b.interact({kind:'drag-start'});
 const deadline=b.dragUntil;
 for(let i=0;i<60;i++){b.interact({kind:'drag-move',velocity:[800,0]});b.step(1/60);}
 assert.equal(b.dragUntil,deadline);assert.deepEqual(b.motion(),{name:'drag',kind:'drag',loop:true});
 b.interact({kind:'drag-end'});b.step(1/60);assert.equal(b.motion().name,'talk');
});
test('摆动与播放姿势分离，停住后回正，松开后权重清零',()=>{
 const b=create();for(let i=0;i<60;i++){b.interact({kind:'drag-move',velocity:[1800,200]});b.step(1/60);}
 assert.ok(b.lastOutput.roll<0);assert.equal(b.lastOutput.lift,undefined);
 advance(b,3);assert.ok(Math.abs(b.lastOutput.roll)<1e-4);assert.equal(b.motion().name,'drag');
 b.interact({kind:'drag-end'});advance(b,0.2);assert.equal(b.lastOutput.drag,0);
});
test('慢帧的弹簧保持有限且释放后不残留摆动',()=>{
 const b=create();for(let i=0;i<100;i++){b.interact({kind:'drag-move',velocity:[i%2?1e9:-1e9,1e8]});b.step(0.08);assert.ok(Number.isFinite(b.lastOutput.roll));}
 b.interact({kind:'drag-end'});advance(b,2,0.08);assert.ok(Math.abs(b.lastOutput.roll)<1e-8);
});
test('睡眠默认关闭，开启后只在待机计时，拖动唤醒',()=>{
 const b=create();advance(b,180);assert.equal(b.sleep,0);
 const enabled=create({enableSleep:true,sleepTimer:30});advance(enabled,31);assert.equal(enabled.sleep,1);
 enabled.interact({kind:'drag-start'});assert.equal(enabled.sleep,0);
 enabled.present({state:'speaking'});enabled.interact({kind:'drag-end'});advance(enabled,40);assert.equal(enabled.sleep,0);
});
test('没有动作库时使用通用静态回退',()=>{
 const b=new PetBehavior();advance(b,30);assert.equal(b.motion().name,'rest');
});
