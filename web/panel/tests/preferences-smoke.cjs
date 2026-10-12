// Real Electron renderer, isolated records and preference storage.
const {app,BrowserWindow,ipcMain,nativeTheme}=require('electron');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs/promises');
app.whenReady().then(async()=>{
 const window=new BrowserWindow({width:1200,height:820,show:true,webPreferences:{partition:'preferences-smoke',preload:path.join(__dirname,'../preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
 const session={session_id:'ses_fixture',title:'用户原文：工作计划',updated_at:1};
 const context={status:'ready',session_id:session.session_id,model:'Qwen',runtime:'qwen',generation:1,totals:{tokens:10,entries:2},entries:[{seq:1,category:'user',label:'用户消息',tokens:5,preview:'用户原文：工作计划'},{seq:2,category:'reference',label:'本轮参考包',tokens:5,preview:'参考原文：乌龙茶'}],provider_entries:[],composition:[{key:'user',label:'用户消息',tokens:5,count:1,percent:50},{key:'reference',label:'本轮参考包',tokens:5,count:1,percent:50}],requests:[{seq:3,time:1,entries:[1,2],added:[1,2],removed:[]}],turns:[],expired_count:0,delete_ack_count:0};
 const graph={nodes:[{id:'n1',type:'EVENT',content:'用户原文：乌龙茶'},{id:'n2',type:'ENTITY',content:'记忆原文：钢琴'}],edges:[{id:'e1',type:'SEMANTIC',subtype:'RELATED_TO',source:'n1',target:'n2'}]};
 ipcMain.handle('engine:errors',()=>[]);
 ipcMain.handle('engine:request',(_,method)=>{
  switch(method){
   case 'state':return {session_id:session.session_id,voice:'off',task:'idle'};
   case 'list_sessions':return [session];
   case 'session_events':return [{type:'message.final',sequence:1,role:'user',content:'用户原文：工作计划'}];
   case 'context_snapshot':return context;
   case 'session_timeline':return [];
   case 'memory_snapshot':return graph;
   case 'list_skills':return [{skill_id:'fixture',name:'用户原文：能力',description:'原文描述',instructions:'原文说明',source:'user',enabled:true}];
   case 'desktop_snapshot':return {bubble_open:true};
   case 'desktop_persona':return {name:'用户原文：名字',content:'原文角色设定',path:'SOUL.md'};
   case 'desktop_runs':return {items:[]};
   default:throw new Error(method);
  }
 });
 const js=code=>window.webContents.executeJavaScript(code);
 const wait=async code=>{for(let i=0;i<120;i++){if(await js(code))return;await new Promise(r=>setTimeout(r,50));}throw Error(code);};
 const check=async(code,label)=>{assert(await js(code),label);checks.push(label);};
 const checks=[];
 const nav=async view=>{await js(`document.querySelector('[data-view="${view}"]').click()`);await wait(`document.body.dataset.view==='${view}' && document.getElementById('content').getAttribute('aria-busy')==='false'`);await new Promise(r=>setTimeout(r,90));};
 const pick=async(selector)=>{await js(`document.querySelector('${selector}').click()`);await new Promise(r=>setTimeout(r,100));};
 const out=path.resolve(__dirname,'../../../artifacts/panel-preferences');await fs.mkdir(out,{recursive:true});
 const capture=async name=>{await new Promise(r=>setTimeout(r,200));await fs.writeFile(path.join(out,name+'.png'),(await window.webContents.capturePage()).toPNG());};
 try{
  nativeTheme.themeSource='light';
  await window.loadFile(path.join(__dirname,'../dist/index.html'));await wait(`!document.getElementById('refresh').disabled`);
  await nav('settings');await nav('interface');
  await check(`document.documentElement.lang==='zh-CN' && document.documentElement.dataset.theme==='light'`,'default Chinese and system light');
  await pick('[data-language="en"]');
  await check(`document.getElementById('page-title').textContent==='Interface' && document.querySelector('.navigation [data-view="memory"]').textContent.trim()==='Memory' && document.querySelector('[data-language="en"]').getAttribute('aria-pressed')==='true'`,'language updates shell and React controls');
  await check(`document.querySelector('.session-title').textContent==='用户原文：工作计划'`,'session content remains verbatim');
  await pick('[data-theme-option="dark"]');
  await check(`document.documentElement.dataset.theme==='dark' && getComputedStyle(document.querySelector('.feature-card')).backgroundColor==='rgb(23, 23, 23)'`,'neutral dark palette reaches content cards');
  await capture('interface-dark-en');
  window.setContentSize(760,680);await new Promise(r=>setTimeout(r,100));
  await check(`document.getElementById('page').scrollWidth<=document.getElementById('page').clientWidth && document.body.scrollWidth<=innerWidth`,'English preference controls fit narrow window');
  window.setContentSize(1200,820);
  await window.reload();await wait(`!document.getElementById('refresh').disabled`);
  await check(`document.documentElement.lang==='en' && document.documentElement.dataset.theme==='dark'`,'preferences persist across renderer reload');
  await nav('settings');await nav('interface');await pick('[data-theme-option="system"]');
  await wait(`document.documentElement.dataset.theme==='light'`);checks.push('system mode resolves light');
  nativeTheme.themeSource='dark';await wait(`document.documentElement.dataset.theme==='dark'`);checks.push('system mode reacts to native appearance change');
  await pick('[data-theme-option="light"]');nativeTheme.themeSource='dark';
  await check(`document.documentElement.dataset.theme==='light'`,'explicit light overrides dark system');
  await capture('interface-light-en');
  await pick('[data-theme-option="dark"]');
  for(const view of ['conversation','memory','context','timeline','skills','runs']) {
   await nav(view);await check(`document.getElementById('page').scrollWidth<=document.getElementById('page').clientWidth`,'English dark '+view+' fits');
   if(view==='conversation'){
    await capture('conversation-dark-en');
    window.setContentSize(760,680);await new Promise(r=>setTimeout(r,100));
    await check(`document.getElementById('page').scrollWidth<=document.getElementById('page').clientWidth && [...document.querySelectorAll('.conversation-toolbar button')].every(b=>b.getBoundingClientRect().right<=innerWidth)`,'English conversation toolbar fits narrow window');
    await capture('conversation-narrow-dark-en');window.setContentSize(1200,820);
   }
   await check(`!/[\\u4e00-\\u9fff]/.test([...document.querySelectorAll('#content button, #content h2, #content dt, .console-legend')].map(n=>n.textContent.replace(/用户原文：能力/g,'')).join(''))`,'English labels '+view);
   if(view==='memory'){await new Promise(r=>setTimeout(r,800));await check(`document.querySelector('.memory-canvas canvas')!==null && getComputedStyle(document.querySelector('.graph-surface')).backgroundColor==='rgb(28, 28, 28)'`,'neutral dark memory canvas and surface render');await capture('memory-dark-en');}
   if(view==='context'){await check(`document.querySelector('.context-browser select').selectedOptions[0].textContent==='Current context'`,'context request labels translated');await capture('context-dark-en');}
  }
  await nav('settings');await nav('persona');await check(`document.querySelector('[aria-label="Companion name"]').value==='用户原文：名字'`,'persona draft remains verbatim');
  await nav('interface');await pick('[data-language="zh-CN"]');await check(`document.getElementById('page-title').textContent==='界面偏好'`,'switch back to Chinese');
  await capture('interface-dark-zh');
  await nav('conversation');await capture('conversation-dark-zh');
  await nav('settings');await nav('interface');await pick('[data-theme-option="light"]');
  await nav('conversation');await capture('conversation-light-zh');
  await fs.writeFile(path.join(out,'report.json'),JSON.stringify({checks},null,2));console.log(JSON.stringify({checks}));app.quit();
 }catch(error){console.error(error);app.exit(1);}
});
