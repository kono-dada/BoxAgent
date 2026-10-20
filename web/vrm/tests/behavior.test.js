import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PetBehavior} from '../behavior.js';
const idleActions=Array.from({length:3},(_,i)=>({name:'idle-'+i,duration:3,weight:1}));
const create=(settings={})=>new PetBehavior(()=>0.5,{idleActions,roles:{dragging:'drag',speaking:'talk'},settings});
test('显式表演跨说话状态保留，结束恢复说话，拖动可中断',()=>{
 const b=create();
 b.gesture={name:'dance',start:b.time,duration:2,semantic:'dance'};
 b.present({state:'speaking'});
 assert.deepEqual(b.motion(),{name:'dance',kind:'gesture',loop:false});
 b.step(0.05);assert.ok(b.lastOutput.jaw>=0);
 b.time=2.1;b.step(0.01);assert.equal(b.motion().name,'idle-0');
 b.gesture={name:'laugh',start:b.time,duration:2,semantic:'laugh'};
 b.step(0.01);assert.equal(b.lastOutput.smile,0.65);
 b.interact({kind:'drag-start'});assert.equal(b.gesture,null);assert.equal(b.motion().name,'drag');
});
function advance(b,seconds,dt=1/60){for(let i=0;i<Math.ceil(seconds/dt);i++)b.step(dt);}
test('Talking 复用连续待机身体，脸部说话，停止后口型归零',()=>{
 const b=create();const before=b.motion();
 b.present({state:'speaking'});assert.deepEqual(b.motion(),before);
 b.step(0.1);assert.ok(b.lastOutput.jaw>0);
 advance(b,10);assert.equal(b.motion().name,'idle-1');
 assert.equal(b.motion().kind,'idle');
 const body=b.motion();b.present({state:'idle'});b.step(0.01);
 assert.deepEqual(b.motion(),body);assert.equal(b.lastOutput.jaw,0);
});
test('说话结束清除聊天情绪动作并恢复待机，明确表演仍可独立完成',()=>{
 const b=create();b.present({state:'speaking'});
 b.gesture={name:'laugh',start:b.time,duration:8,semantic:'laugh'};
 b.present({state:'idle'});
 assert.equal(b.gesture,null);assert.equal(b.motion().kind,'idle');
 b.step(0.05);assert.equal(b.lastOutput.jaw,0);
 b.present({state:'speaking'});
 b.gesture={name:'dance',start:b.time,duration:2,semantic:'dance'};
 b.present({state:'idle'});assert.equal(b.motion().name,'dance');
 advance(b,2.1);assert.equal(b.motion().kind,'idle');
});
test('待机每十秒抽样，允许继续当前动作，拖动中计时暂停',()=>{
 const b=create();assert.equal(b.motion().name,'idle-0');
 advance(b,10.1);assert.equal(b.motion().name,'idle-1');
 const timer=b.idleTimer;
 b.interact({kind:'drag-start'});advance(b,10);assert.equal(b.motion().name,'drag');assert.equal(b.idleIndex,1);
 assert.equal(b.idleTimer,timer);
 b.interact({kind:'drag-end'});advance(b,0.1);assert.equal(b.motion().name,'idle-1');
 advance(b,10);assert.equal(b.motion().name,'idle-1');
});
test('收到实际拖拽事件后进入 Drag，快速释放也保持至少 0.3 秒',()=>{
 const b=create();b.interact({kind:'drag-start'});assert.equal(b.motion().name,'drag');
 advance(b,0.05);b.interact({kind:'drag-end'});advance(b,0.1);assert.equal(b.dragging,true);
 advance(b,0.2);assert.equal(b.dragging,false);assert.equal(b.motion().name,'idle-0');
});
test('Drag 覆盖说话但不丢失业务状态，持续移动不会重置最短保持计时',()=>{
 const b=create();b.present({state:'speaking'});b.interact({kind:'drag-start'});
 const deadline=b.dragUntil;
 for(let i=0;i<60;i++){b.interact({kind:'drag-move',velocity:[800,0]});b.step(1/60);}
 assert.equal(b.dragUntil,deadline);assert.deepEqual(b.motion(),{name:'drag',kind:'drag',loop:true});
 b.interact({kind:'drag-end'});b.step(1/60);assert.equal(b.motion().name,'idle-0');
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
