const {test}=require('node:test');
const assert=require('node:assert/strict');
const {MusicMonitor}=require('../desktop/music-monitor.cjs');
test('music changes are delivered once, failures clear dance, closed polls are ignored',async()=>{
  const changes=[];let active=true;
  const monitor=new MusicMonitor(value=>changes.push(value),{probe:async()=>active});
  await monitor.poll();await monitor.poll();assert.deepEqual(changes,[true]);
  active=false;await monitor.poll();assert.deepEqual(changes,[true,false]);
  active=true;await monitor.poll();monitor.probe=async()=>{throw Error('unavailable');};
  await monitor.poll();assert.deepEqual(changes,[true,false,true,false]);
  monitor.stop();active=true;await monitor.poll();assert.equal(changes.length,4);
});
test('polls never overlap or notify after shutdown',async()=>{
  let resolve,calls=0;const changes=[];
  const monitor=new MusicMonitor(value=>changes.push(value),{probe:()=>{
    calls++;return new Promise(done=>{resolve=done;});
  }});
  const first=monitor.poll();await monitor.poll();assert.equal(calls,1);
  monitor.stop();resolve(true);await first;assert.deepEqual(changes,[]);
});
