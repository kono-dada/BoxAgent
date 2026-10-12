// Exercise continuous slider RPCs and reset in an isolated Electron renderer.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path');const assert=require('node:assert/strict');
app.whenReady().then(async()=>{
 const window=new BrowserWindow({width:1200,height:820,show:false,webPreferences:{partition:'pet-scale-smoke',preload:path.join(__dirname,'../preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
 let scale=1;const calls=[];
 const snapshot=()=>({scale,min_scale:.5,max_scale:6,appearance:'fixture',appearance_ready:true,bubble_open:false});
 ipcMain.handle('engine:errors',()=>[]);
 ipcMain.handle('engine:request',async(_,method,params)=>{
  if(method==='state')return {voice:'off',task:'idle'};
  if(method==='list_sessions')return [];
  if(method==='desktop_avatars')return {...snapshot(),items:[{id:'fixture',name:'Fixture'}]};
  if(method==='desktop_snapshot')return snapshot();
  if(method==='desktop_preview_avatar')throw Error('Preview fixture');
  if(method==='desktop_set_pet_scale') {calls.push(params.scale);await new Promise(r=>setTimeout(r,80));scale=params.scale;return snapshot();}
  throw Error(method);
 });
 const js=source=>window.webContents.executeJavaScript(source);
 const wait=async condition=>{for(let i=0;i<120;i++){if(await condition())return;await new Promise(r=>setTimeout(r,50));}throw Error('Timed out');};
 try{
  await window.loadFile(path.join(__dirname,'../dist/index.html'));
  await wait(()=>js(`!document.getElementById('refresh').disabled`));
  await js(`document.getElementById('settings-button').click()`);
  await wait(()=>js(`Boolean(document.querySelector('.settings-overview'))`));
  await js(`document.querySelector('#settings-tabs [data-view="appearance"]').click()`);
  await wait(()=>js(`Boolean(document.querySelector('#pet-scale'))`));
  assert(await js(`document.getElementById('pet-scale').min==='50' && document.getElementById('pet-scale').max==='600' && document.querySelector('.pet-scale output').textContent==='100%'`));
  await js(`(()=>{const slider=document.getElementById('pet-scale'),setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;for(const value of [125,150,200,237]){setter.call(slider,String(value));slider.dispatchEvent(new Event('input',{bubbles:true}));}})()`);
  await wait(async()=>scale===2.37);
  assert.deepEqual(calls,[1.25,2.37]);
  assert(await js(`document.querySelector('.pet-scale output').textContent==='237%'`));
  await js(`document.querySelector('.pet-scale button').click()`);await wait(async()=>scale===1);
  assert(await js(`document.getElementById('pet-scale').value==='100'`));
  await js(`document.querySelector('#settings-tabs [data-view="interface"]').click()`);
  await wait(()=>js(`Boolean(document.querySelector('[data-language="en"]'))`));
  await js(`document.querySelector('[data-language="en"]').click();document.querySelector('#settings-tabs [data-view="appearance"]').click()`);
  await wait(()=>js(`Boolean(document.querySelector('#pet-scale'))`));
  assert(await js(`document.querySelector('.pet-scale strong').textContent==='Desktop companion size' && document.getElementById('page').scrollWidth<=document.getElementById('page').clientWidth`));
  console.log('Pet scale renderer: range, continuous RPC coalescing, latest value, reset and English layout passed');app.quit();
 }catch(error){console.error(error);app.exit(1);}
});
