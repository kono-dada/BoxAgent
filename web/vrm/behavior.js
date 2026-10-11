import {MateController, clamp} from './mate-controller.js';
export {clamp};
const smooth = v => {const x=clamp(v,0,1);return x*x*(3-2*x);};
// 业务状态和表情适配保留在外围；动作调度与拖拽优先级由 MateController 决定。
export class PetBehavior extends MateController {
  constructor(random = Math.random, options = {}) {
    super(options); this.random=random;
    this.pointer=[0,0];
    this.nextBlink=this.range(1.5,3.5);this.blinkStart=-100;this.doubleBlink=false;
    this.lastOutput=null;
  }
  range(a,b){return a+this.random()*(b-a);}
  present({state=this.state,pointer=this.pointer}) {
    this.presentState(state);
    this.pointer=pointer;
  }
  interact(value){super.interact(value);if(value.kind==='tap')this.nextBlink=this.time;}
  step(dt) {
    dt=clamp(dt,0,0.08);
    const sway=this.advanceState(dt),t=this.time;
    if(t>=this.nextBlink){
      this.blinkStart=t;this.nextBlink=t+this.range(2.6,5.8);
      this.doubleBlink=!this.doubleBlink && this.random()<0.14;
      if(this.doubleBlink)this.nextBlink=t+0.4;
    }
    const age=t-this.blinkStart;
    const blink=age<0.09?smooth(age/0.09):1-smooth((age-0.09)/0.16);
    const out={gesture:this.motion().name,
      roll:sway.roll,pitch:sway.pitch,drag:sway.weight,
      blink:Math.max(blink,this.sleep*0.94),smile:0,jaw:0,surprised:0};
    if(this.state==='speaking' && !this.dragging){
      const phrase=(t-this.stateSince)%3.9;
      out.jaw=phrase<3.1?0.04+Math.max(0,Math.sin(t*15.5+Math.sin(t*3.7)))*(0.15+0.1*Math.sin(t*2.3)**2):0;
    }
    if(this.state==='success' && t-this.stateSince<3 && !this.dragging)out.smile=0.65;
    this.lastOutput=out;return out;
  }
  debug(){return {state:this.state,time:this.time,gesture:this.lastOutput?.gesture,history:[...this.history],
    dragging:this.dragging,mouseHeld:this.mouseHeld,sleep:this.sleep,idleIndex:this.idleIndex,
    roll:this.lastOutput?.roll || 0,events:[...this.events],settings:this.settings};}
}
