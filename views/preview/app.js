// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Shennoter
const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginView;
const clone = (value) => JSON.parse(JSON.stringify(value));
const sections = [
  {id:"studio",title:"生成与试听",icon:"◉",description:"调整声音，听见文字。当前草稿立即参与下一次生成。"},
  {id:"clone",title:"音色复刻",icon:"✧",description:"上传一段清晰录音，创建属于你的声音。"},
  {id:"connection",title:"服务与音色",icon:"◇",groups:["connection"],description:"独立配置语音服务，密钥只写不回显。"},
  {id:"audio",title:"合成参数",icon:"≋",groups:["audio"],description:"调整格式、语速、音调和情绪，生成前可随时试听。"},
  {id:"reply",title:"随机语音",icon:"⤨",groups:["reply"],description:"决定哪些回复以声音呈现。"},
  {id:"pronunciation",title:"发音与词典",icon:"文",groups:["pronunciation"],description:"管理单次读音模式和持久替换词典。"},
  {id:"scope",title:"会话范围",icon:"◎",groups:["scope"],description:"通过 AstrBot 内置 /sid 获取完整 UMO，设置使用范围。"},
  {id:"tools",title:"朗读与工具",icon:"⌘",groups:["command","natural"],description:"配置语言朗读、权限和自然语言调用。"},
  {id:"preview",title:"试听设置",icon:"▷",groups:["preview"],description:"设置默认试听文案、语言和冷却时间。"},
];
const limits = {volume:[0,100,1],rate:[0.5,2,0.05],pitch:[0.5,2,0.05],probability:[0,100,1],max_prompt_audio_length:[3,30,1]};
let data, draft, revision, currentPage="studio", dirty=false, clearKey=false, busy=false, saving=false, objectUrl, uploaded, selectedFile, cloneBusy=false;
let inputMap = new Map();
let confirmReload=false;

function status(message,error=false) { $("status").textContent=message; $("status").classList.toggle("error",error); }
function markDirty() { dirty=true; confirmReload=false; $("refresh").textContent="重新加载"; updateSummary(); }
function updateSummary() {
  if(!draft)return;
  $("save-state").textContent=dirty?"有未保存的调整":"全部设置已保存";
  $("save-state").classList.toggle("dirty",dirty);
  $("save").disabled=busy||saving||!dirty;
  $("refresh").disabled=busy||saving||cloneBusy;
  const c=draft.connection;
  $("current-model").textContent=c.model||"未填写模型";
  $("current-voice").textContent=c.voice||"请填写音色 ID";
  const ready=(c.api_key||(data.api_key_set&&!clearKey))&&c.workspace&&c.voice;
  $("service-badge").textContent=ready?"服务已配置":"等待配置";
  const length=Array.from($("text").value.trim()).length;
  $("count").textContent=`${length} / ${draft.reply.max_chars} 字`;
  $("generate").disabled=busy||saving||!draft.preview.enabled||!ready||!length||length>draft.reply.max_chars;
  $("upload").disabled=cloneBusy||!selectedFile||!data.config.cloning.enabled;
  $("create-voice").disabled=cloneBusy||!uploaded||!data.config.cloning.enabled;
}
function setDraft(group,key,value,source) {
  draft[group][key]=value;
  for(const input of inputMap.get(`${group}.${key}`)||[]) {
    if(input===source)continue;
    if(input.type==="checkbox")input.checked=value;
    else input.value=Array.isArray(value)?value.join("\n"):value;
    input.removeAttribute("aria-invalid");
  }
  if(group==="preview"&&key==="text"&&source!==$("text"))$("text").value=value;
  if(group==="preview"&&key==="language"&&source!==$("language"))$("language").value=value;
  markDirty();
}
function register(input,group,key) {
  const id=`${group}.${key}`;
  if(!inputMap.has(id))inputMap.set(id,[]);
  inputMap.get(id).push(input);
  input.dataset.field=id;
  return input;
}
function field(group,key,{compact=false}={}) {
  const meta=data.schema[group].items[key], value=draft[group][key];
  const container=document.createElement("div");container.className="field";
  if(meta.type==="text"||meta.type==="list"||key==="api_key"||key==="workspace"||key==="voice")container.classList.add("wide");
  const label=document.createElement("label");label.textContent=meta.description;
  const id=`field-${group}-${key}-${inputMap.get(`${group}.${key}`)?.length||0}`;label.htmlFor=id;
  let input;
  if(meta.type==="bool") {
    const row=document.createElement("div");row.className="switch-row";row.append(label);
    input=document.createElement("input");input.type="checkbox";input.className="switch";input.checked=value;row.append(input);container.append(row);
  } else {
    container.append(label);
    if(meta.options) { input=document.createElement("select");for(const option of meta.options){const el=document.createElement("option");el.value=option;el.textContent=option;input.append(el);} }
    else if(meta.type==="text"||meta.type==="list"){ input=document.createElement("textarea");input.rows=meta.type==="list"?4:5; }
    else { input=document.createElement("input");input.type=meta.secret?"password":(["int","float"].includes(meta.type)?"number":"text");if(meta.type==="int")input.step="1";if(meta.type==="float")input.step="any"; }
    input.value=Array.isArray(value)?value.join("\n"):value;
    if(meta.secret){input.autocomplete="new-password";input.placeholder=data.api_key_set?"已配置 · 留空保留原密钥":"输入 API Key";}
    if(key==="workspace")input.placeholder="ws-xxx.cn-beijing.maas.aliyuncs.com";
    if(key==="model")input.placeholder="例如 qwen-audio-3.0-tts-flash";
    if(limits[key]&&!meta.options) {
      const [min,max,step]=limits[key],row=document.createElement("div");row.className="range-row";
      const slider=register(document.createElement("input"),group,key);slider.type="range";slider.min=min;slider.max=max;slider.step=step;slider.value=value;slider.setAttribute("aria-label",meta.description);
      slider.addEventListener("input",()=>setDraft(group,key,Number(slider.value),slider));
      input.min=min;input.max=max;input.step=step;row.append(slider,input);container.append(row);
    } else container.append(input);
  }
  input.id=id;register(input,group,key);
  input.addEventListener(meta.type==="bool"?"change":"input",()=>{
    let next=meta.type==="bool"?input.checked:input.value;
    if(["int","float"].includes(meta.type)) {
      next=Number(input.value);
      if(input.value===""||!Number.isFinite(next)||(meta.type==="int"&&!Number.isInteger(next))||!input.validity.valid){input.setAttribute("aria-invalid","true");return;}
    }
    if(meta.type==="list")next=input.value.split("\n").map(v=>v.trim()).filter(Boolean);
    input.removeAttribute("aria-invalid");setDraft(group,key,next,input);
  });
  if(meta.hint&&!compact){const hint=document.createElement("p");hint.className="hint";hint.textContent=meta.hint+(meta.type==="list"?" 每行填写一项。":"");container.append(hint);}
  if(meta.secret){
    const actions=document.createElement("div");actions.className="secret-actions";
    const state=document.createElement("span");state.className="hint";state.textContent=clearKey?"保存时将清除密钥":data.api_key_set?"已安全保存，接口不返回原值":"尚未配置密钥";
    const erase=document.createElement("button");erase.type="button";erase.textContent=clearKey?"取消清除":"清除已保存密钥";
    erase.addEventListener("click",()=>{clearKey=!clearKey;state.textContent=clearKey?"保存时将清除密钥":"留空保留原密钥";erase.textContent=clearKey?"取消清除":"清除已保存密钥";markDirty();});
    actions.append(state,erase);container.append(actions);
  }
  return container;
}
function card(group,keys) {
  const node=document.createElement("section");node.className="card settings-card";
  const header=document.createElement("div");header.className="card-header";
  const heading=document.createElement("h2");heading.textContent=data.schema[group].description;header.append(heading);node.append(header);
  const grid=document.createElement("div");grid.className="form-grid";
  for(const key of keys||Object.keys(data.schema[group].items))grid.append(field(group,key));
  node.append(grid);return node;
}
function showPage(id) {
  if(!sections.some(s=>s.id===id))id="studio";
  currentPage=id;const section=sections.find(s=>s.id===id);
  document.querySelectorAll("[data-page]").forEach(page=>page.hidden=page.dataset.page!==id);
  document.querySelectorAll(".nav-link").forEach(button=>{if(button.dataset.go===id)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");});
  $("page-title").textContent=section.title;$("breadcrumb-title").textContent=section.title;$("page-description").textContent=section.description;
  $("sidebar").classList.remove("open");
  history.replaceState(null,"",`#${id}`);
  if(id==="clone"&&data?.api_key_set)loadJobs();
}
function render() {
  inputMap=new Map();
  $("workspace-nav").replaceChildren();$("settings-nav").replaceChildren();$("settings-pages").replaceChildren();
  for(const section of sections) {
    const button=document.createElement("button");button.className="nav-link";button.dataset.go=section.id;
    const icon=document.createElement("span");icon.className="nav-icon";icon.textContent=section.icon;icon.setAttribute("aria-hidden","true");
    const title=document.createElement("span");title.textContent=section.title;button.append(icon,title);button.addEventListener("click",()=>showPage(section.id));
    $(section.groups?"settings-nav":"workspace-nav").append(button);
    if(section.groups){const page=document.createElement("section");page.className="page";page.dataset.page=section.id;page.hidden=true;for(const group of section.groups)page.append(card(group));$("settings-pages").append(page);}
  }
  $("quick-controls").replaceChildren(...["volume","rate","pitch","format","sample_rate"].map(key=>field("audio",key,{compact:true})));
  $("clone-settings").replaceChildren(...["enabled","cooldown_seconds","daily_limit"].map(key=>field("cloning",key)));
  $("clone-options").replaceChildren(...["prefix","language","max_prompt_audio_length","enable_preprocess","enable_volume_normalization"].map(key=>field("cloning",key)));
  const model=field("connection","model");$("clone-options").prepend(model);
  $("text").value=draft.preview.text;$("language").value=draft.preview.language;
  showPage(currentPage);updateSummary();
}
function collectDraft() {
  const invalid=document.querySelector('[aria-invalid="true"]');
  if(invalid)throw new Error(`请检查 ${invalid.dataset.field} 的输入范围。`);
  return clone(draft);
}
async function refresh() {
  if(dirty&&!confirmReload){confirmReload=true;$("refresh").textContent="确认丢弃调整";status("重新加载会丢弃未保存的调整，再次点击确认。也可以先保存全部设置。");return;}
  confirmReload=false;$("refresh").textContent="重新加载";
  busy=true;$("refresh").disabled=true;updateSummary();
  try{
    data=await bridge.apiGet("preview/settings");draft=clone(data.config);revision=data.revision;dirty=false;clearKey=false;uploaded=undefined;
    render();status("设置已加载。调整参数后可直接生成；保存全部设置后应用到聊天。音频不会自动播放。");
  }catch(error){status(error.message||"加载失败，请重载插件后重试。",true);}
  finally{busy=false;$("refresh").disabled=false;updateSummary();}
}
async function save() {
  if(saving||!draft)return;
  try{
    const config=collectDraft();saving=true;$("save").disabled=true;
    const response=await bridge.apiPost("settings/save",{config,revision,clear_api_key:clearKey});
    data={...data,...response};draft=clone(response.config);revision=response.revision;dirty=false;clearKey=false;
    render();status("全部设置已保存，后续请求立即使用新设置。");
  }catch(error){status(error.message||"保存失败，原设置已保留。",true);}
  finally{saving=false;updateSummary();}
}
async function generate(event) {
  event.preventDefault();if($("generate").disabled)return;
  let config;try{config=collectDraft();}catch(error){status(error.message,true);return;}
  busy=true;updateSummary();$("generate").textContent="正在生成…";status("正在合成音频，请等待。不会自动重试。");
  try {
    const response=await bridge.apiPost("preview/generate",{text:$("text").value,language:$("language").value,config,clear_api_key:clearKey});
    if(!["audio/wav","audio/mpeg","audio/ogg"].includes(response.mime_type))throw new Error("服务返回了不支持的音频格式。");
    const bytes=Uint8Array.from(atob(response.audio_base64),c=>c.charCodeAt(0));
    $("audio").pause();if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=URL.createObjectURL(new Blob([bytes],{type:response.mime_type}));
    $("audio").src=objectUrl;$("download").href=objectUrl;$("download").download=response.filename;$("result").hidden=false;
    $("audio-summary").textContent=`${config.connection.model} · ${config.audio.format.toUpperCase()} · ${config.audio.sample_rate} Hz · 语速 ${config.audio.rate} · ${config.connection.save_audio?"服务端按保留时间保存":"服务端临时文件已清理，可在此下载"}`;
    status("生成完成，点击播放器试听。此次使用页面草稿，没有自动保存设置。");
  }catch(error){status(error.message||"生成失败，请检查设置和网络。",true);}
  finally{busy=false;$("generate").textContent="▶ 生成音频";updateSummary();}
}
async function upload() {
  if(!selectedFile||cloneBusy)return;
  cloneBusy=true;updateSummary();$("clone-status").textContent="正在上传并校验 WAV…";
  try{uploaded=await bridge.upload("cloning/upload",selectedFile);$("clone-status").textContent=`校验通过：${uploaded.seconds} 秒 · ${uploaded.sample_rate} Hz · ${uploaded.channels} 声道。可点击创建云端音色。`;}
  catch(error){uploaded=undefined;$("clone-status").textContent=error.message;}
  finally{cloneBusy=false;updateSummary();}
}
async function createVoice() {
  if(!uploaded||cloneBusy)return;
  cloneBusy=true;updateSummary();
  try{
    const cfg=collectDraft(),p=cfg.cloning;
    const job=await bridge.apiPost("cloning/create",{upload_id:uploaded.upload_id,model:cfg.connection.model,prefix:p.prefix,language:p.language,enable_preprocess:p.enable_preprocess,enable_volume_normalization:p.enable_volume_normalization,max_prompt_audio_length:p.max_prompt_audio_length});
    $("clone-status").textContent=`任务已记录（${job.status}）。稍后刷新任务查看返回的音色 ID；同一上传不会重复创建。`;
    uploaded=undefined;await loadJobs();
  }catch(error){$("clone-status").textContent=error.message;}
  finally{cloneBusy=false;updateSummary();}
}
async function loadJobs() {
  if(!data?.api_key_set)return;
  try{const result=await bridge.apiGet("cloning/jobs");renderJobs(result.jobs);}
  catch(error){$("jobs").textContent=error.message;}
}
function renderJobs(jobs) {
  $("jobs").replaceChildren();
  if(!jobs.length){const hint=document.createElement("p");hint.className="hint";hint.textContent="暂无任务。上传校验后即可创建音色。";$("jobs").append(hint);return;}
  for(const job of jobs){
    const item=document.createElement("article");item.className="job";
    const heading=document.createElement("div");heading.className="job-heading";const name=document.createElement("strong");name.textContent=job.model;const badge=document.createElement("span");badge.className="small-badge";badge.textContent=job.status;heading.append(name,badge);item.append(heading);
    const code=document.createElement("code");code.className="job-code";code.textContent=job.voice_id||job.error||"正在创建，请稍后刷新任务。";item.append(code);
    const time=document.createElement("p");time.className="hint";time.textContent=`${new Date(job.created*1000).toLocaleString()} · ${job.prefix}`;item.append(time);
    const actions=document.createElement("div");actions.className="inline-actions";
    if(job.voice_id){
      const query=document.createElement("button");query.className="button secondary";query.textContent="查询云端状态";query.addEventListener("click",async()=>{query.disabled=true;try{await bridge.apiPost("cloning/query",{job_id:job.job_id});await loadJobs();}catch(error){status(error.message,true);}finally{query.disabled=false;}});actions.append(query);
      if(job.status==="OK"){
        const apply=document.createElement("button");apply.className="button primary";apply.textContent="填入并试听";apply.addEventListener("click",()=>{setDraft("connection","model",job.model);setDraft("connection","voice",job.voice_id);showPage("studio");status("已将音色填入草稿，可先试听；保存全部设置后应用到聊天。");});actions.append(apply);
      }
    }
    item.append(actions);$("jobs").append(item);
  }
}
$("text").addEventListener("input",()=>{if(draft)setDraft("preview","text",$("text").value,$("text"));});
$("language").addEventListener("change",()=>{if(draft)setDraft("preview","language",$("language").value,$("language"));});
$("preview-form").addEventListener("submit",generate);$("save").addEventListener("click",save);$("refresh").addEventListener("click",refresh);
$("audio").addEventListener("error",()=>status("浏览器无法播放此格式，请下载音频，或改为 WAV / MP3 后生成。",true));
document.querySelector(".brand").addEventListener("click",event=>{event.preventDefault();showPage("studio");});
$("menu-toggle").addEventListener("click",()=>$("sidebar").classList.toggle("open"));
$("upload").addEventListener("click",upload);$("create-voice").addEventListener("click",createVoice);$("refresh-jobs").addEventListener("click",loadJobs);
$("wav-file").addEventListener("change",()=>{
  selectedFile=$("wav-file").files[0];uploaded=undefined;
  if(selectedFile&&(!selectedFile.name.toLowerCase().endsWith(".wav")||selectedFile.size>10*1024*1024)){selectedFile=undefined;$("clone-status").textContent="请选择不超过 10 MiB 的 .wav 文件。";}
  $("file-info").textContent=selectedFile?`${selectedFile.name} · ${(selectedFile.size/1024/1024).toFixed(2)} MiB`:"尚未选择录音。";updateSummary();
});
document.querySelectorAll("[data-go]").forEach(button=>button.addEventListener("click",()=>showPage(button.dataset.go)));
window.addEventListener("beforeunload",event=>{if(objectUrl)URL.revokeObjectURL(objectUrl);if(dirty){event.preventDefault();event.returnValue="";}});
try {
  if(!bridge)throw new Error("请从 AstrBot 的“阿里云TTS助手”可视化页面打开，需要支持插件视图的版本。");
  await bridge.ready();currentPage=location.hash.slice(1)||"studio";await refresh();
}catch(error){status(error.message,true);}
