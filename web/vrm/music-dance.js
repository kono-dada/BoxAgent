// Keep loading/races separate from the avatar's synchronous motion priorities.
export class MusicDance {
  constructor(bank, behavior, random = Math.random, warn = () => {}) {
    Object.assign(this,{bank,behavior,random,warn});
    this.names=Object.keys(bank.manifest.actions).filter(name=>name.startsWith('PET_DANCING/'));
    this.active=false;this.pending=false;this.generation=0;this.last=null;this.rejected=new Set();
  }
  setActive(active) {
    if(this.active===active)return;
    this.active=active;this.generation++;
    this.behavior.musicGesture=null;
    if(active)this.behavior.gesture=null;
  }
  update() {
    if(!this.active || this.pending || this.behavior.dragging || this.behavior.musicGesture)return;
    let names=this.names.filter(name=>!this.rejected.has(name));
    if(names.length>1)names=names.filter(name=>name!==this.last);
    if(!names.length)return;
    const name=names[Math.min(names.length-1,Math.floor(this.random()*names.length))];
    const generation=this.generation;
    this.pending=true;
    return this.bank.loadMotion(name).then(clip=>{
      if(!this.active || generation!==this.generation || this.behavior.dragging)return;
      const duration=clip.duration/this.bank.settings(name).timeScale;
      if(!Number.isFinite(duration)||duration<=0)throw Error('Invalid dance duration');
      this.last=name;this.behavior.gesture=null;
      this.behavior.musicGesture={name,start:this.behavior.time,duration};
    }).catch(error=>{this.rejected.add(name);this.warn(String(error));})
      .finally(()=>{this.pending=false;});
  }
  dispose(){this.setActive(false);}
}
