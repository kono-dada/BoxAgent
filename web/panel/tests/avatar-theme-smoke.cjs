const {app,BrowserWindow,ipcMain,nativeTheme}=require('electron');
const path=require('node:path');const fs=require('node:fs/promises');const assert=require('node:assert/strict');const {EngineClient}=require('../engine-client.cjs');
app.whenReady().then(async()=>{
 const client=new EngineClient(path.resolve(__dirname,'../../../.runtime/pet/desktop.sock'));
 await new Promise((resolve,reject)=>{client.once('online',resolve);client.once('diagnostic',reject);client.connect();});
 const window=new BrowserWindow({width:1200,height:820,show:true,webPreferences:{partition:'avatar-theme-check',preload:path.join(__dirname,'../preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
 ipcMain.handle('engine:errors',()=>[]);
 ipcMain.handle('engine:request',(_,method,params)=>method.startsWith('desktop_')?client.request(method,params):method==='state'?{}:[]);
 const js=code=>window.webContents.executeJavaScript(code);
 const wait=async check=>{for(let i=0;i<160;i++){if(await check())return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out');};
 const nav=async view=>{await js(`document.querySelector('[data-view="${view}"]').click()`);await wait(()=>js(`document.body.dataset.view==='${view}' && document.getElementById('content').getAttribute('aria-busy')==='false'`));await new Promise(r=>setTimeout(r,100));};
 const out=path.resolve(__dirname,'../../../artifacts/panel-preferences');await fs.mkdir(out,{recursive:true});const checks=[];
 try {
  nativeTheme.themeSource='light';await window.loadFile(path.join(__dirname,'../dist/index.html'));await wait(()=>js(`!document.getElementById('refresh').disabled`));
  await nav('settings');await nav('interface');await js(`document.querySelector('[data-theme-option="dark"]').click()`);await nav('appearance');
  await wait(async()=>{const f=window.webContents.mainFrame.frames.find(f=>f.url.startsWith('http://127.0.0.1'));return f && f.executeJavaScript(`Boolean(window.pet?.debug().ready && window.pet.debug().frames>0)`);});
  let frame=window.webContents.mainFrame.frames.find(f=>f.url.startsWith('http://127.0.0.1'));
  assert.equal(await frame.executeJavaScript(`getComputedStyle(document.documentElement).backgroundColor`),'rgb(21, 28, 24)');checks.push('real VRM preview has matching dark background');
  await fs.writeFile(path.join(out,'avatar-dark.png'),(await window.webContents.capturePage()).toPNG());
  await nav('interface');await js(`document.querySelector('[data-theme-option="system"]').click()`);await nav('appearance');
  await wait(async()=>{frame=window.webContents.mainFrame.frames.find(f=>f.url.startsWith('http://127.0.0.1'));return frame && frame.executeJavaScript(`Boolean(window.pet?.debug().ready)`);});
  assert.equal(await frame.executeJavaScript(`getComputedStyle(document.documentElement).backgroundColor`),'rgb(255, 255, 255)');checks.push('system light preview initialized');
  const url=frame.url;nativeTheme.themeSource='dark';
  await wait(()=>frame.executeJavaScript(`getComputedStyle(document.documentElement).backgroundColor==='rgb(21, 28, 24)'`));assert.equal(frame.url,url);checks.push('preview follows system theme without reloading model');
  const transparent=await frame.executeJavaScript(`(()=>{const canvas=document.querySelector('canvas');const pixel=canvas.getContext('webgl2').readPixels;return window.pet.debug().ready;})()`);assert(transparent);checks.push('VRM remains ready after theme update');
  console.log(JSON.stringify({checks}));await fs.writeFile(path.join(out,'avatar-theme-report.json'),JSON.stringify({checks},null,2));client.close();app.quit();
 }catch(error){console.error(error);client.close();app.exit(1);}
});
