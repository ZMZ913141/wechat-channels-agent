/* 发布中心：短标题校验、任务轮询、拖拽上传 */
/* 智能填写：字段 -> input id（发布页没有标题字段，留空即可） */
var AF_MAP={{title:'', short_title:'p_short', desc:'p_desc', tags:'p_tags', ai:''}};
AF_FALLBACK_NAME={{ (m.title if mat else "")|tojson }};
/* 生成后同步刷新左侧预览 */
function afterAutofill(d){{
  var t=document.getElementById('pv_title'), s=document.getElementById('pv_desc');
  if(t && d.short_title) t.textContent=d.short_title;
  if(s && d.desc) s.textContent=d.desc;
  checkShort();
}}
{}
// 封面上传：拖拽 / 点选 + 提交前校验
function coverReady(){{
  var f=document.getElementById('pvc_file');
  if(f && (!f.files || !f.files.length)){{ alert('请先选择封面图片（点击或拖拽到虚线框）'); return false; }}
  return true;
}}
bindDrop('pvc_drop','pvc_file','pvc_prev','image');
// 短标题字数校验（平台要求 >= 6 字，少于 6 字会发布失败）
function checkShort(){{
  var inp=document.getElementById('p_short');
  var el=document.getElementById('st_count');
  if(!inp || !el) return;
  var v=inp.value.trim();
  if(!v){{ el.textContent=''; el.className='muted'; return; }}
  var n=v.length;
  el.textContent = (n<6 ? '⚠️ 仅 '+n+' 字，平台要求≥6字，少于6字会发布失败' : '✅ '+n+' 字');
  el.className = (n<6 ? 'bad' : 'ok');
}}
checkShort();
// 发布任务自动刷新（无需手动按 F5）
var _jobRunning = {{ 'true' if job and job.running else 'false' }};
function refreshJob(){{
  fetch('/publish/status').then(function(r){{return r.json()}}).then(function(d){{
    var w=document.getElementById('jobwrap');
    if(w) w.innerHTML=d.html;
    var a=document.getElementById('autoref');
    if(a) a.textContent = d.running ? '（运行中，自动刷新…）' : '';
    if(!d.running){{ clearInterval(_jobTimer); }}
  }}).catch(function(){{}});
}}
var _jobTimer = _jobRunning ? setInterval(refreshJob, 5000) : null;
