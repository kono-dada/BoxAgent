// Exercise writing controls in a real renderer with isolated IPC data.
const {app,BrowserWindow,ipcMain}=require('electron');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const calls=[];
let state={session_id:'ses_fixture',session_title:'隔离验收',voice:'off',task:'running',task_text:'隔离任务',context_enabled:false,approval:'允许隔离操作？'},skills=[{skill_id:'builtin',name:'内置',description:'不可修改',instructions:'builtin',source:'builtin',enabled:true}],events=[];
let persona={name:'小芽',content:'名字：小芽\n保持温柔。'};
app.whenReady().then(async()=>{
 const window=new BrowserWindow({show:true,width:1200,height:820,webPreferences:{preload:path.join(__dirname,'../preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false}});
 const emit=()=>window.webContents.send('engine:state',state);
 ipcMain.handle('engine:errors',()=>[]);
 ipcMain.handle('engine:request',(_,method,params={})=>{
  calls.push({method,params});
  const session={session_id:'ses_fixture',title:'隔离验收',updated_at:1};
  switch(method){
   case 'state':return state;
   case 'list_sessions':return [session];
   case 'context_snapshot':return {status:'empty'};
   case 'session_events':return events;
   case 'session_timeline':return [];
   case 'desktop_snapshot':return {bubble_open:false};
   case 'desktop_persona':return persona;
   case 'desktop_save_persona':return persona={...params};
   case 'list_skills':return skills;
   case 'create_skill':{const skill={...params,source:'user',enabled:true};skills.push(skill);return skill;}
   case 'update_skill':{const i=skills.findIndex(s=>s.skill_id===params.skill_id);skills[i]={...skills[i],...params};return skills[i];}
   case 'set_skill_enabled':{const skill=skills.find(s=>s.skill_id===params.skill_id);skill.enabled=params.enabled;return skill;}
   case 'delete_skill':skills=skills.filter(s=>s.skill_id!==params.skill_id);return {status:'succeeded'};
   case 'submit_text':events.push({type:'message.final',sequence:1,role:'user',content:params.goal});return {status:'accepted'};
   case 'create_session':return session;
   case 'activate_session':return session;
   case 'archive_session':return session;
   case 'toggle_voice':state.voice=state.voice==='off'?'ready':'off';emit();return {};
   case 'toggle_context':state.context_enabled=!state.context_enabled;emit();return {};
   case 'answer_approval':state.approval='';emit();return {};
   case 'cancel_task':{const next=state.task_queue?.shift();state.task=next?'running':'cancelled';if(next)state.task_goal=next.goal;emit();return {status:'cancelled'};}
   case 'cancel_all_tasks':state.task='cancelled';state.task_queue=[];emit();return {status:'cancelled'};
   case 'desktop_open_run':case 'desktop_open_logs':case 'desktop_toggle_bubble':case 'desktop_quit':return {};
   case 'desktop_runs':return {items:[{id:'fixture-run',manifest:{task_id:'fixture',goal:'隔离运行'},files:['manifest.json']}]};
   default:throw new Error(method);
  }
 });
 const js=s=>window.webContents.executeJavaScript(s);
 const wait=async(condition)=>{for(let i=0;i<100;i++){if(await js(condition))return;await new Promise(r=>setTimeout(r,50));}throw new Error(condition);};
 const nav=async view=>{
  if(['persona','controls'].includes(view) && await js(`document.getElementById('settings-tabs').hidden`)) {await js(`document.getElementById('settings-button').click()`);await wait(`document.querySelector('.settings-overview')`);}
  await js(`document.querySelector('${['persona','controls'].includes(view)?'#settings-tabs':'.navigation'} [data-view="${view}"]').click()`);
  await wait(`document.querySelector('.feature-page') && document.getElementById('content').getAttribute('aria-busy')==='false'`);
 };
 const click=async text=>{await js(`[...document.querySelectorAll('#content button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}).click()`);await new Promise(r=>setTimeout(r,120));};
 const fill=async(label,value,kind='input')=>{await js(`(()=>{const el=document.querySelector('[aria-label="${label}"]');Object.getOwnPropertyDescriptor(${kind==='textarea'?'HTMLTextAreaElement':'HTMLInputElement'}.prototype,'value').set.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event('input',{bubbles:true}));})()`);await new Promise(r=>setTimeout(r,60));};
 try{
  await window.loadFile(path.join(__dirname,'../dist/index.html'));await wait(`!document.getElementById('refresh').disabled`);
  await js(`void (window.confirm=()=>true)`);
  await nav('persona');await fill('角色名称','验收角色');await click('保存并应用');assert.equal(persona.name,'验收角色');
  await nav('skills');assert(await js(`document.querySelector('[aria-label="Skill name"]').disabled`));
  await click('新建');await fill('Skill skill_id','fixture-skill');await fill('Skill name','隔离能力');await fill('Skill description','隔离描述');await fill('Skill 说明','隔离说明','textarea');await click('保存');assert.equal(skills.length,2);
  await fill('Skill name','更新能力');await click('保存');assert.equal(skills[1].name,'更新能力');
  await js(`document.querySelector('[aria-label="启用此 Skill"]').click()`);await new Promise(r=>setTimeout(r,150));assert.equal(skills[1].enabled,false);
  await click('删除');assert.equal(skills.length,1);
  await nav('conversation');await click('开启麦克风');assert.equal(state.voice,'ready');await click('关闭麦克风');assert.equal(state.voice,'off');await fill('对话消息','隔离消息','textarea');await click('发送');assert.equal(events[0].content,'隔离消息');
  await js(`document.querySelector('.conversation-toolbar [aria-label="显示 / 收起桌面小窗"]').click()`);assert(calls.some(c=>c.method==='desktop_toggle_bubble'));
  await click('新建会话');await fill('新会话名称','新验收');await click('创建并开始');assert(calls.some(c=>c.method==='create_session'&&c.params.title==='新验收'));
  await nav('runs');await click('允许操作');assert(calls.some(c=>c.method==='answer_approval'&&c.params.allowed===true));
  state.task_goal='收藏网易云当前歌曲';state.task_queue=[{task_id:'b',goal:'打开计算器'},{task_id:'c',goal:'计算二的五次方'}];emit();
  await wait(`document.querySelector('.task-queue strong')?.textContent==='排队中 · 2 项'`);assert(await js(`document.querySelector('.task-queue').open`));
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('.task-queue li')).map(row=>row.lastElementChild.textContent)`),['打开计算器','计算二的五次方']);
  await js(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`);
  fs.writeFileSync('/tmp/boxagent-task-queue-panel.png',(await window.webContents.capturePage()).toPNG());
  await click('停止当前任务');await wait(`document.querySelector('.task-goal')?.textContent==='打开计算器' && document.querySelector('.task-queue strong')?.textContent==='排队中 · 1 项'`);assert.equal(state.task,'running');
  await click('取消全部任务');await wait(`!document.querySelector('.task-queue')`);assert.equal(state.task,'cancelled');assert(calls.some(c=>c.method==='cancel_all_tasks'));
  await click('打开最近运行');await click('打开会话运行目录');assert(calls.some(c=>c.method==='desktop_open_run'&&c.params.latest));
  await nav('controls');await click('显示 / 收起');await click('开启麦克风');assert.equal(state.voice,'ready');await click('关闭麦克风');assert.equal(state.voice,'off');await click('开启屏幕总结');assert(state.context_enabled);await click('关闭屏幕总结');assert(!state.context_enabled);await click('打开目录');await click('退出 BoxAgent');assert(calls.some(c=>c.method==='desktop_quit'));
  await js(`document.getElementById('settings-back').click()`);await wait(`document.body.dataset.view==='runs' && document.querySelector('.run-list')`);
  assert(await js(`document.getElementById('settings-tabs').hidden && Boolean(document.querySelector('.approval-card'))===false`));
  const bubble=new BrowserWindow({show:true,width:420,height:700,webPreferences:{preload:path.join(__dirname,'../preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false}});
  state.task='running';state.task_goal='收藏网易云当前歌曲';state.task_queue=[{task_id:'b',goal:'打开计算器'},{task_id:'c',goal:'计算二的五次方'}];
  await bubble.loadFile(path.join(__dirname,'../dist/companion.html'));
  const small=s=>bubble.webContents.executeJavaScript(s);
  for(let i=0;i<100 && !(await small(`Boolean(document.querySelector('.task-queue'))`));i++)await new Promise(r=>setTimeout(r,50));
  assert.equal(await small(`document.querySelector('.task-queue').open`),false);
  assert.equal(await small(`document.querySelector('.queue-next').textContent`),'下一项：打开计算器');
  await small(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`);
  assert(await small(`Array.from(document.querySelectorAll('.task-status .button')).every(button=>button.getBoundingClientRect().bottom<=document.querySelector('.task-status').getBoundingClientRect().bottom)`));
  fs.writeFileSync('/tmp/boxagent-task-queue-small.png',(await bubble.webContents.capturePage()).toPNG());
  await small(`document.querySelector('.task-queue > summary').click()`);
  assert.equal(await small(`document.querySelectorAll('.task-queue li').length`),2);
  console.log('Isolated feature renderer: existing controls, live FIFO queue/advance/clear, compact collapse/expand passed');app.quit();
 }catch(error){console.error(error);app.exit(1);}
});
