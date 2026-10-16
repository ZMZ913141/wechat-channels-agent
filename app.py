"""本地管理面板（Flask）：多账号切换 + 可视化素材管理 + 同步发布 + 私信/评论监控
（可直接在面板启动/停止）+ 固定规则自动回复 + 公众号管理 + 监控状态。

启动（推荐用 uv，依赖已装）：
  uv run python app.py
然后浏览器打开 http://127.0.0.1:5000

设计要点：
  - 监控不再需要单独开一个终端：面板里点「启动监控」即用子进程拉起 monitor_dm.py，
    心跳写入 state/heartbeat.json，面板实时显示运行状态；点「停止监控」即结束。
  - 素材改为可视化卡片网格：缩略图（视频首帧/封面）、状态徽章、标签、快捷操作
    （复制文案 / 编辑 / 发布 / 复用 / 标记 / 删除），支持文件上传与多账号复用。
  - 暗色「夜视版」为默认主题，右上角可切「日间」并本地持久化。
"""
import os
import html
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import quote

from flask import (
    Flask,
    redirect,
    render_template_string,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from wechannels import accounts, config, loginprobe, login_state, materials, media_probe
from wechannels import mp as mpmod
from wechannels import proclock
from wechannels import publish as pubmod

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

CREATE_NEW_CONSOLE = 0x00000010 if sys.platform == "win32" else 0

# ---------------------------------------------------------------------------
# 模板（CSS 变量驱动暗/亮双主题，默认暗色“夜视版”）
# ---------------------------------------------------------------------------
TEMPLATE = """<!doctype html>
<html lang="zh-CN" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>视频号助手 · 管理面板</title>
<script>
  (function(){try{var t=localStorage.getItem('theme')||'dark';document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
</script>
<style>
  :root{
    --bg:#0f1115;--card:#181b22;--line:#262b36;--fg:#e6e9ef;--mut:#9aa3b2;
    --acc:#3b82f6;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;
    --elev:#1f232b;--elev2:#262b34;
  }
  [data-theme="light"]{
    --bg:#f4f6f9;--card:#ffffff;--line:#dee2e6;--fg:#1f2933;--mut:#5b6675;
    --acc:#2563eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626;
    --elev:#f8fafc;--elev2:#eef2f7;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif;transition:background .2s,color .2s}
  header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;position:sticky;top:0;background:var(--bg);z-index:10}
  h1{font-size:18px;margin:0;display:flex;align-items:center;gap:10px}
  .nav a{margin-left:8px;color:var(--mut);text-decoration:none;font-size:13px}
  .nav a:hover{color:var(--acc)}
  .actions a{margin-left:8px}
  a.btn{display:inline-block;padding:7px 12px;border-radius:8px;background:var(--acc);color:#fff;text-decoration:none;font-size:13px;cursor:pointer;border:none}
  a.btn.ghost{background:var(--elev2);color:var(--fg);border:1px solid var(--line)}
  a.btn.danger{background:var(--bad)}
  .theme-btn{border:1px solid var(--line);background:var(--elev);color:var(--fg);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:13px}
  main{padding:18px clamp(14px,3vw,44px);max-width:none;width:100%}
  @media (min-width:2000px){ main{max-width:1880px;margin:0 auto} }
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:18px}
  h2{font-size:15px;margin:0 0 12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  h2 .sub{font-size:12px;color:var(--mut);font-weight:400}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--mut);font-weight:500;font-size:12px}
  .tag{display:inline-block;background:var(--elev2);border:1px solid var(--line);border-radius:6px;padding:1px 8px;margin:2px 4px 2px 0;color:var(--mut);font-size:12px}
  .st{font-size:12px;padding:2px 8px;border-radius:6px}
  .st.待发布{background:rgba(245,158,11,.15);color:var(--warn)}
  .st.已发布{background:rgba(34,197,94,.15);color:var(--ok)}
  .st.草稿{background:rgba(59,130,246,.15);color:var(--acc)}
  .st.发布中{background:rgba(59,130,246,.15);color:var(--acc)}
  .st.失败{background:rgba(239,68,68,.15);color:var(--bad)}
  a.mini{color:var(--acc);text-decoration:none;margin-right:8px;font-size:12px;cursor:pointer;background:none;border:none;padding:0}
  a.mini.danger{color:var(--bad)}
  .warn{color:var(--bad);font-size:12px}
  form label{display:block;margin:8px 0 4px;color:var(--mut);font-size:12px}
  input,textarea,select{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px;font:inherit}
  textarea{resize:vertical;min-height:60px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media (max-width:760px){ .grid2{grid-template-columns:1fr} }
  .log{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:var(--mut);white-space:pre-wrap;max-height:260px;overflow:auto}
  .muted{color:var(--mut)}
  .accbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .accbar select{width:auto;min-width:160px}
  .chips a{margin-right:10px;color:var(--mut);font-size:12px;text-decoration:none;padding:3px 10px;border-radius:20px;border:1px solid var(--line)}
  .chips a.on{color:var(--acc);border-color:var(--acc)}
  .toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--ok);color:#04210f;padding:10px 18px;border-radius:10px;font-size:13px;opacity:0;transition:opacity .25s;pointer-events:none;font-weight:600;z-index:20}
  .toast.show{opacity:1}
  .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
  .dot.ok{background:var(--ok);box-shadow:0 0 0 3px rgba(34,197,94,.18)}
  .dot.bad{background:var(--bad);box-shadow:0 0 0 3px rgba(239,68,68,.18)}
  .dot.warn{background:var(--warn);box-shadow:0 0 0 3px rgba(245,158,11,.18)}
  .stat{display:flex;gap:18px;flex-wrap:wrap;margin-top:8px;align-items:center}
  .stat div{font-size:13px}
  .stat b{color:var(--fg)}

  /* 主区域两栏：左=素材库，右=新建素材 */
  .maingrid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);gap:18px;align-items:start}
  .maingrid .col{min-width:0}
  .maingrid .card{height:100%}
  @media (max-width:1100px){ .maingrid{grid-template-columns:1fr} }
  /* 字段级「智能生成」小按钮 */
  .genbtn{background:var(--elev2);border:1px solid var(--line);color:var(--acc);border-radius:6px;padding:1px 7px;font-size:11px;cursor:pointer;margin-left:6px}
  .genbtn:hover{border-color:var(--acc)}
  .genbtn:disabled{opacity:.5;cursor:default}

  /* 素材卡片网格 */
  .grid-mat{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}
  .mcard{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--mut);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;transition:transform .15s,border-color .15s}
  .mcard:hover{transform:translateY(-3px)}
  .mcard.ok{border-left-color:var(--ok)} .mcard.warn{border-left-color:var(--warn)}
  .mcard.acc{border-left-color:var(--acc)} .mcard.bad{border-left-color:var(--bad)}
  .thumb{height:140px;background:var(--elev);display:flex;align-items:center;justify-content:center;overflow:hidden}
  .thumb img,.thumb video{width:100%;height:100%;object-fit:cover;display:block}
  .thumb-ph{font-size:40px;color:var(--mut)}
  .mbody{padding:12px;display:flex;flex-direction:column;gap:8px;flex:1}
  .mtitle{font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .mtags{display:flex;flex-wrap:wrap;gap:4px}
  .mbadge{display:flex;align-items:center;gap:8px}
  .mactions{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}
  .mpath{font-size:11px;word-break:break-all}
  .drop{border:2px dashed var(--line);border-radius:10px;padding:10px;text-align:center;color:var(--mut);font-size:12px;cursor:pointer}
  .drop:hover{border-color:var(--acc);color:var(--acc)}
  .searchbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
  .searchbar input{max-width:260px}
  .searchbar select{width:auto;min-width:110px}
  .hist{font-size:11px;color:var(--mut);margin-top:2px}
  .hist .ok{color:var(--ok)} .hist .bad{color:var(--bad)}
  .abadge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;margin-left:4px;vertical-align:middle}
  .abadge.ai{background:rgba(168,85,247,.18);color:#c084fc}
  .abadge.orig{background:rgba(34,197,94,.15);color:var(--ok)}
  .abadge.sched{background:rgba(59,130,246,.15);color:var(--acc)}
  .batchbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}
  .filelist{max-height:340px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin-top:8px}
  .filelist label{display:flex;align-items:center;gap:8px;padding:7px 10px;border-bottom:1px solid var(--line);margin:0;color:var(--fg);font-size:13px;cursor:pointer}
  .filelist label:hover{background:var(--elev)}
  .filelist .meta{color:var(--mut);font-size:11px;margin-left:auto}
  .check-mat{position:absolute;top:8px;left:8px;z-index:2;transform:scale(1.3);cursor:pointer}
  .mcard{position:relative}
</style></head>
<body>
<header>
  <h1>视频号助手 · 管理面板</h1>
  <div class="nav">
    <a href="/publish">同步发布</a>
    <a href="/import">目录导入</a>
    <a href="/mp">公众号</a>
    <a href="/accounts">账号</a>
    <button class="theme-btn" id="themeBtn" onclick="toggleTheme()">🌙 夜视</button>
  </div>
</header>
<main>

<div class="card">
  <div class="accbar">
    <span class="muted">当前账号：</span>
    <form method="post" action="/switch_account" style="display:inline">
      <select name="account_id" onchange="this.form.submit()">{account_options}</select>
    </form>
    <span class="muted">（{account_count} 个账号）</span>
    <a class="btn ghost" href="/accounts">＋ 管理/新增账号</a>
    <a class="btn ghost" href="/open_publish?account={cur_account_id}" target="_blank" title="为该账号打开视频号发表页，登录态独占，不会和发布任务打架">打开发表页（{cur_account_name}）</a>
  </div>
</div>

<div class="maingrid">
  <div class="col">
    <div class="card">
      <h2>素材库 <span class="sub">{count} 条 · 当前账号</span>
        <span class="chips" style="margin-left:auto">
          <a class="{f_all}" href="/">全部</a>
          <a class="{f_待发布}" href="/?status=待发布">待发布</a>
          <a class="{f_草稿}" href="/?status=草稿">草稿</a>
          <a class="{f_已发布}" href="/?status=已发布">已发布</a>
        </span>
      </h2>
      <div class="searchbar">
        <form method="get" action="/" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
          <input type="hidden" name="status" value="{sf}">
          <input name="q" value="{q}" placeholder="🔍 搜索标题 / 文案 / 标签…">
          <select name="sort">
            <option value="new" {s_new}>最新创建</option>
            <option value="updated" {s_updated}>最近更新</option>
            <option value="status" {s_status}>按状态</option>
          </select>
          <button class="btn ghost" type="submit">筛选</button>
        </form>
      </div>
      <form method="post" action="/materials/batch" id="batchForm">
      <div class="batchbar" id="batchbar" style="display:none">
        <span class="muted">已选 <b id="selCount">0</b> 项：</span>
        <button class="btn ghost" type="submit" name="op" value="mark_已发布" onclick="return setOp('mark_已发布')">标为已发布</button>
        <button class="btn ghost" type="submit" name="op" value="mark_待发布" onclick="return setOp('mark_待发布')">标为待发布</button>
        <button class="btn ghost" type="submit" name="op" value="mark_草稿" onclick="return setOp('mark_草稿')">标为草稿</button>
        <button class="btn danger" type="submit" name="op" value="delete" onclick="return confirm('确认批量删除所选素材？')">批量删除</button>
        <a class="mini" href="javascript:void(0)" onclick="toggleSelect(true)">全选</a>
        <a class="mini" href="javascript:void(0)" onclick="toggleSelect(false)">取消全选</a>
      </div>
      {materials_grid}
      </form>
    </div>
  </div>
  <div class="col">
    <div class="card">
      <h2>新建素材 <span class="sub">支持本地路径或上传文件</span></h2>
      {new_material_form}
    </div>
  </div>
</div>

</main>
<div class="toast" id="toast">已复制到剪贴板</div>
<script>
function copyText(btn){
  var t = btn.getAttribute('data-copy');
  if(navigator.clipboard){ navigator.clipboard.writeText(t).then(showToast); }
  else { var ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();showToast(); }
}
function showToast(){ var x=document.getElementById('toast'); x.classList.add('show'); setTimeout(function(){x.classList.remove('show');},1500); }
function toggleTheme(){
  var cur=document.documentElement.getAttribute('data-theme');
  var next=cur==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',next);
  try{localStorage.setItem('theme',next);}catch(e){}
  document.getElementById('themeBtn').textContent = next==='dark'?'🌙 夜视':'☀️ 日间';
}
document.getElementById('themeBtn').textContent =
  document.documentElement.getAttribute('data-theme')==='dark'?'🌙 夜视':'☀️ 日间';
// 批量选择
function selChanged(){ updateBar(); }
function toggleSelect(on){
  document.querySelectorAll('.check-mat').forEach(function(c){ c.checked=on; });
  updateBar();
}
function selCount(){ return document.querySelectorAll('.check-mat:checked').length; }
function updateBar(){
  var n=selCount();
  var bar=document.getElementById('batchbar');
  if(bar){ bar.style.display = n>0 ? 'flex' : 'none'; }
  var sc=document.getElementById('selCount');
  if(sc){ sc.textContent=n; }
}
function setOp(v){
  var f=document.getElementById('batchForm');
  var i=f.querySelector('input[name="op"]');
  if(i) i.value=v;
  return selCount()>0 || (alert('请先勾选素材'), false);
}
// 智能填写（AI 生成标题/短标题/文案/标签/AI标注建议）+ 拖拽上传 —— 共享实现
{form_js}
var AF_MAP={title:'f_title', short_title:'f_short', desc:'f_desc', tags:'f_tags', ai:'f_ai'};
// 从视频路径同步文件名到智能填写输入框
function syncName(v){
  var el=document.getElementById('af_name');
  if(el && v && !el.value) el.value=v;
}
bindDrop('vdrop','vfile','vprev','video');
bindDrop('cdrop','cfile','cprev','image');
// 初始隐藏 op
(function(){
  var f=document.getElementById('batchForm');
  if(f){
    var i=document.createElement('input');
    i.type='hidden'; i.name='op'; i.value='';
    f.appendChild(i);
  }
  updateBar();
})();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# 共享前端脚本：智能填写（AI 生成字段）+ 拖拽上传预览
#   注意：TEMPLATE / EDIT_TPL 都是纯字符串 + .replace() 渲染（不走 str.format），
#   所以这里的 JS 必须写「单花括号」，否则整段 <script> 会语法报错、全部前端功能失效。
#   每个页面只需先设置 AF_MAP（字段 -> input id），再调用 bindDrop(...)。
# ---------------------------------------------------------------------------
FORM_JS = """
// 页面没有「智能填写」输入框时（如发布中心）用它兜底；页面若已先赋值则保留（勿直接置空）
var AF_FALLBACK_NAME = (typeof AF_FALLBACK_NAME === 'undefined') ? '' : AF_FALLBACK_NAME;
function afArgs(){
  var n=document.getElementById('af_name'), h=document.getElementById('af_hint');
  var v=document.getElementById('f_vpath')||document.getElementById('e_vpath');
  var name=(n&&n.value.trim())||'';
  var hint=(h&&h.value.trim())||'';
  if(!name && v && v.value.trim()) name=v.value.trim();
  if(!name) name=AF_FALLBACK_NAME||'';
  return {name:name, hint:hint};
}
function afApply(d, only){
  function setv(field, v){
    if(!v) return;
    if(only && only!==field) return;
    var el=document.getElementById(AF_MAP[field]);
    if(el) el.value=v;
  }
  setv('title', d.title);
  setv('short_title', d.short_title);
  setv('desc', d.desc);
  setv('tags', d.tags);
  if(d.ai_declare && (!only || only==='ai_declare')){
    var sel=document.getElementById(AF_MAP.ai);
    if(sel){
      for(var i=0;i<sel.options.length;i++){
        if(sel.options[i].value===d.ai_declare){ sel.selectedIndex=i; break; }
      }
    }
  }
}
function setAfNote(t){ var el=document.getElementById('af_note'); if(el) el.textContent=t; }
function setGenBtns(disabled){
  document.querySelectorAll('.genbtn, #af_btn').forEach(function(b){ b.disabled=disabled; });
}
function genField(field){
  var a=afArgs();
  if(!a.name && !a.hint){ alert('先填视频文件名/路径，或写一句补充提示'); return; }
  setGenBtns(true);
  setAfNote('正在生成'+(field==='all'?'全部字段':'「'+field+'」')+'…');
  fetch('/materials/autofill?name='+encodeURIComponent(a.name)+'&hint='+encodeURIComponent(a.hint)+'&field='+encodeURIComponent(field==='all'?'':field))
    .then(function(r){ return r.json(); })
    .then(function(d){
      afApply(d, field==='all'?null:field);
      setAfNote(d._note || ('✅ 已生成（'+(d._source==='llm'?'AI':'本地规则')+'），可手动修改后保存'));
      setGenBtns(false);
      if(typeof afterAutofill==='function') afterAutofill(d);
    })
    .catch(function(e){
      setAfNote('生成失败：'+e);
      setGenBtns(false);
    });
}
function doAutofill(){ genField('all'); }
function bindDrop(dropId, inputId, prevId, kind){
  var drop=document.getElementById(dropId), input=document.getElementById(inputId), prev=document.getElementById(prevId);
  if(!drop || !input) return;
  drop.onclick=function(){ input.click(); };
  drop.ondragover=function(e){ e.preventDefault(); drop.style.borderColor='var(--acc)'; };
  drop.ondragleave=function(){ drop.style.borderColor=''; };
  drop.ondrop=function(e){
    e.preventDefault(); drop.style.borderColor='';
    if(e.dataTransfer.files.length){ input.files=e.dataTransfer.files; showPrev(); }
  };
  input.onchange=showPrev;
  function showPrev(){
    var f=input.files && input.files[0];
    if(!prev) return;
    if(!f){ prev.innerHTML=''; return; }
    drop.textContent=f.name;
    var url=URL.createObjectURL(f);
    if(kind==='video' && f.type.indexOf('video')===0){
      prev.innerHTML='<video src="'+url+'" controls muted style="width:100%;max-height:150px;border-radius:8px"></video>';
    } else if(f.type.indexOf('image')===0){
      prev.innerHTML='<img src="'+url+'" style="width:100%;max-height:150px;object-fit:cover;border-radius:8px">';
    } else { prev.innerHTML=''; }
  }
}
"""


NEW_FORM = """<form method="post" action="/materials/new" enctype="multipart/form-data">
  <div class="fillbar" style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin-bottom:4px">
    <div style="flex:2;min-width:220px"><label>✨ 智能填写：视频文件名或路径</label>
      <input id="af_name" placeholder="d:/videos/AI模特上新.mp4 或只填文件名"></div>
    <div style="flex:3;min-width:220px"><label>补充提示（可选，如：女装秋冬新款，温柔风）</label>
      <input id="af_hint" placeholder="告诉 AI 这条视频讲什么，生成更准"></div>
    <div><label>&nbsp;</label>
      <button class="btn ghost" type="button" id="af_btn" onclick="doAutofill()">✨ 一键生成全部</button></div>
  </div>
  <div id="af_note" class="muted" style="font-size:12px;min-height:18px"></div>
  <div class="grid2">
    <div><label>标题 * <button class="genbtn" type="button" onclick="genField('title')">✨ 生成</button></label>
      <input name="title" id="f_title" required></div>
    <div><label>短标题 <span class="muted">（6-16字）</span> <button class="genbtn" type="button" onclick="genField('short_title')">✨ 生成</button></label>
      <input name="short_title" id="f_short" maxlength="16"></div>
  </div>
  <label>描述 / 文案 <span class="muted">（官方上限 1000 字，重要信息放前两行）</span>
    <button class="genbtn" type="button" onclick="genField('desc')">✨ 生成</button></label>
  <textarea name="desc" id="f_desc" placeholder="可含 #话题 标签"></textarea>
  <label>话题标签（逗号分隔，自动加 #，建议 5-8 个）
    <button class="genbtn" type="button" onclick="genField('tags')">✨ 生成</button></label>
  <input name="tags" id="f_tags" placeholder="干货,教程">
  <div class="grid2">
    <div><label>视频标注 <span class="muted">（AI 内容依法须声明）</span>
        <button class="genbtn" type="button" onclick="genField('ai_declare')">✨ 生成</button></label>
      <select name="ai_declare" id="f_ai">
        <option value="">不标注（非 AI 内容）</option>
        <option value="内容为AI制作">内容为AI制作</option>
        <option value="内容为虚构剧情">内容为虚构剧情</option>
      </select></div>
    <div><label>可见范围</label>
      <select name="visibility">
        <option>公开</option><option>仅好友可见</option><option>私密</option>
      </select></div>
  </div>
  <div class="grid2">
    <div><label>声明原创</label>
      <select name="original"><option value="true" selected>是（默认勾选：展示原创标记，有机会获得广告收入）</option><option value="">否</option></select></div>
    <div><label>定时发表 <span class="muted">（留空=立即；助手端专属功能）</span></label>
      <input type="datetime-local" name="scheduled_at"></div>
  </div>
  <div class="grid2">
    <div>
      <label>视频文件（可上传或填本地路径；官方限制：≤4G / ≤2小时 / ≥720p）</label>
      <div class="drop" id="vdrop">点击选择 或 拖拽视频文件到这里（可选）</div>
      <input type="file" name="video_file" id="vfile" accept="video/*,image/*" style="display:none">
      <div id="vprev" style="margin-top:6px"></div>
      <input name="video_path" id="f_vpath" placeholder="或填本地路径 d:/videos/demo.mp4" oninput="syncName(this.value)">
    </div>
    <div>
      <label>封面图（可上传或填本地路径）</label>
      <div class="drop" id="cdrop">点击选择 或 拖拽图片到这里（可选）</div>
      <input type="file" name="cover_file" id="cfile" accept="image/*" style="display:none">
      <div id="cprev" style="margin-top:6px"></div>
      <input name="cover_path" placeholder="或填本地路径 d:/videos/cover.jpg">
    </div>
  </div>
  <p><button class="btn" type="submit">保存素材</button> <span class="muted">也可以用顶部「目录导入」从本地文件夹批量导入（支持粘贴路径）。</span></p>
</form>"""

CARD = """<div class="mcard {statuscls}">
  <input type="checkbox" class="check-mat" name="mids" value="{id}" onchange="selChanged(this)">
  <div class="thumb">{thumb}</div>
  <div class="mbody">
    <div class="mtitle" title="{title}">{title}</div>
    <div class="mtags">{tags}</div>
    <div class="mbadge"><span class="st {status}">{status}</span>{badges}</div>
    {history}
    <div class="mactions">
      <button class="mini" onclick="copyText(this)" data-copy="{copy}">复制文案</button>
      <a class="mini" href="/materials/{id}/edit">编辑</a>
      <a class="mini" href="/publish?material={id}">发布</a>
      <a class="mini" href="/materials/{id}/clone">复用</a>
      <a class="mini" href="/materials/{id}/status?status=已发布">标已发</a>
      <a class="mini danger" href="/materials/{id}/delete" onclick="return confirm('删除此素材？')">删除</a>
    </div>
    <div class="mpath muted">{path}</div>
  </div>
</div>"""

EDIT_TPL = """<!doctype html><html lang="zh-CN" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>编辑素材</title>
<script>
  (function(){try{var t=localStorage.getItem('theme')||'dark';document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
</script>
<style>
  :root{
    --bg:#0f1115;--card:#181b22;--line:#262b36;--fg:#e6e9ef;--mut:#9aa3b2;
    --acc:#3b82f6;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;
    --elev:#1f232b;--elev2:#262b34;
  }
  [data-theme="light"]{
    --bg:#f4f6f9;--card:#ffffff;--line:#dee2e6;--fg:#1f2933;--mut:#5b6675;
    --acc:#2563eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626;
    --elev:#f8fafc;--elev2:#eef2f7;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}
  .wrap{max-width:1000px;margin:0 auto;padding:22px clamp(14px,3vw,32px) 60px}
  header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px}
  h2{font-size:17px;margin:0}
  a{color:var(--acc);text-decoration:none}
  label{display:block;margin:10px 0 4px;color:var(--mut);font-size:12px}
  input,textarea,select{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px;font:inherit}
  textarea{resize:vertical;min-height:70px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
  .btn{display:inline-block;padding:8px 14px;background:var(--acc);color:#fff;border-radius:8px;text-decoration:none;border:none;cursor:pointer;font-size:13px}
  .btn.ghost{background:var(--elev2);color:var(--fg);border:1px solid var(--line)}
  .theme-btn{border:1px solid var(--line);background:var(--elev);color:var(--fg);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:13px}
  .g2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media (max-width:760px){ .g2{grid-template-columns:1fr} }
  .muted{color:var(--mut);font-size:12px}
  .genbtn{background:var(--elev2);border:1px solid var(--line);color:var(--acc);border-radius:6px;padding:1px 7px;font-size:11px;cursor:pointer;margin-left:6px}
  .genbtn:hover{border-color:var(--acc)}
  .genbtn:disabled{opacity:.5;cursor:default}
  .drop{border:2px dashed var(--line);border-radius:10px;padding:10px;text-align:center;color:var(--mut);font-size:12px;cursor:pointer;word-break:break-all}
  .drop:hover{border-color:var(--acc);color:var(--acc)}
  .thumbs{margin-top:6px}
  .thumbs img,.thumbs video{width:100%;max-height:150px;object-fit:cover;border-radius:8px;display:block}
  .hist{font-size:11px;color:var(--mut);margin-top:3px}
  .hist .ok{color:var(--ok)} .hist .bad{color:var(--bad)}
  .law{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.35);border-radius:8px;padding:8px 12px;font-size:12px;margin:8px 0;color:var(--warn)}
</style></head><body>
<div class="wrap">
<header>
  <h2>编辑素材</h2>
  <div><a class="btn ghost" href="/">← 返回面板</a>
    <a class="btn ghost" href="/publish?material={id}">去发布</a>
    <button class="theme-btn" id="themeBtn" onclick="toggleTheme()">🌙 夜视</button></div>
</header>

<form method="post" action="/materials/{id}/edit" enctype="multipart/form-data">
  <div class="card">
    <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
      <div style="flex:2;min-width:200px"><label>✨ 智能填写：视频文件名或路径</label>
        <input id="af_name" placeholder="d:/videos/AI模特上新.mp4 或只填文件名"></div>
      <div style="flex:3;min-width:200px"><label>补充提示（可选，如：女装秋冬新款，温柔风）</label>
        <input id="af_hint" placeholder="告诉 AI 这条视频讲什么，生成更准"></div>
      <div><label>&nbsp;</label>
        <button class="btn ghost" type="button" id="af_btn" onclick="doAutofill()">✨ 一键生成全部</button></div>
    </div>
    <div id="af_note" class="muted" style="min-height:18px"></div>
  </div>

  <div class="card">
    <div class="g2">
      <div><label>标题 <button class="genbtn" type="button" onclick="genField('title')">✨ AI填写</button></label>
        <input name="title" id="f_title" value="{title}"></div>
      <div><label>短标题 <span class="muted">（6-16字）</span>
          <button class="genbtn" type="button" onclick="genField('short_title')">✨ AI填写</button></label>
        <input name="short_title" id="f_short" maxlength="16" value="{short_title}"></div>
    </div>
    <label>描述 / 文案
      <button class="genbtn" type="button" onclick="genField('desc')">✨ AI填写</button></label>
    <textarea name="desc" id="f_desc">{desc}</textarea>
    <label>话题标签（逗号分隔，自动加 #）
      <button class="genbtn" type="button" onclick="genField('tags')">✨ AI填写</button></label>
    <input name="tags" id="f_tags" value="{tags}">
  </div>

  <div class="card">
    <div class="g2">
      <div>
        <label>视频文件（可上传/拖拽，或填本地路径）</label>
        <div class="drop" id="vdrop">点击选择 或 拖拽视频文件到这里</div>
        <input type="file" name="video_file" id="vfile" accept="video/*,image/*" style="display:none">
        <div class="thumbs" id="vprev">{video_thumb}</div>
        <input name="video_path" id="f_vpath" value="{video_path}" placeholder="或填本地路径 d:/videos/demo.mp4"
               oninput="syncName(this.value)">
      </div>
      <div>
        <label>封面图（可上传/拖拽，或填本地路径）</label>
        <div class="drop" id="cdrop">点击选择 或 拖拽图片到这里</div>
        <input type="file" name="cover_file" id="cfile" accept="image/*" style="display:none">
        <div class="thumbs" id="cprev">{cover_thumb}</div>
        <input name="cover_path" id="f_cpath" value="{cover_path}" placeholder="或填本地路径 d:/videos/cover.jpg">
      </div>
    </div>
  </div>

  <div class="card">
    <div class="g2">
      <div><label>视频标注 <span class="muted">（AI 内容依法必须声明）</span>
          <button class="genbtn" type="button" onclick="genField('ai_declare')">✨ AI填写</button></label>
        <select name="ai_declare" id="f_ai">{ai_opts}</select></div>
      <div><label>可见范围</label>
        <select name="visibility">{vis_opts}</select></div>
    </div>
    <div class="law">⚖️ 依据《人工智能生成合成内容标识办法》（2025-09-01 施行），AI 生成/合成的视频发布时必须主动声明。</div>
    <div class="g2">
      <div><label>定时发表（留空=立即）</label>
        <input type="datetime-local" name="scheduled_at" value="{scheduled_at}"></div>
      <div><label>扩展链接</label><input name="ext_link" value="{ext_link}" placeholder="https://mp.weixin.qq.com/s/..."></div>
    </div>
    <div class="g2">
      <div><label>声明原创</label>
        <select name="original"><option value="" {orig_no}>否</option><option value="true" {orig_yes}>是</option></select></div>
      <div><label>状态</label><select name="status">{opts}</select></div>
    </div>
  </div>

  <p><button class="btn" type="submit">保存</button> <a class="muted" href="/" style="margin-left:10px">返回</a></p>
</form>

<div class="card">
  <h3 style="margin:0 0 8px;font-size:14px">发布状态记录</h3>
  {history_html}
</div>
</div>
<script>
{form_js}
var AF_MAP={title:'f_title', short_title:'f_short', desc:'f_desc', tags:'f_tags', ai:'f_ai'};
function syncName(v){
  var el=document.getElementById('af_name');
  if(el && v && !el.value) el.value=v;
}
bindDrop('vdrop','vfile','vprev','video');
bindDrop('cdrop','cfile','cprev','image');
function toggleTheme(){
  var cur=document.documentElement.getAttribute('data-theme');
  var next=cur==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',next);
  try{localStorage.setItem('theme',next);}catch(e){}
  document.getElementById('themeBtn').textContent = next==='dark'?'🌙 夜视':'☀️ 日间';
}
document.getElementById('themeBtn').textContent =
  document.documentElement.getAttribute('data-theme')==='dark'?'🌙 夜视':'☀️ 日间';
</script>
</body></html>"""

# ---------------------------------------------------------------------------
# 把共享脚本注入到用 .replace() 渲染的模板里（这些模板的花括号必须保持单写）
TEMPLATE = TEMPLATE.replace("{form_js}", FORM_JS)
EDIT_TPL = EDIT_TPL.replace("{form_js}", FORM_JS)


ACCOUNTS_TPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>账号管理</title>
<style>body{background:#0f1115;color:#e6e9ef;font:14px system-ui;padding:24px;max-width:none}
a{color:#3b82f6;text-decoration:none}label{display:block;margin:8px 0 4px;color:#9aa3b2;font-size:12px}
input,select{width:100%;background:#0f1115;border:1px solid #262b36;color:#e6e9ef;border-radius:8px;padding:8px;font:inherit}
.card{background:#181b22;border:1px solid #262b36;border-radius:12px;padding:16px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid #262b36}
.btn{display:inline-block;padding:7px 12px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-size:13px;margin-right:6px}
.ghost{background:#222936;border:1px solid #262b36;color:#e6e9ef}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}.muted{color:#9aa3b2}
.msg{padding:10px 12px;border-radius:8px;margin:10px 0;font-size:13px}
.ok{background:rgba(34,197,94,.12);color:#22c55e}.bad{background:rgba(239,68,68,.12);color:#ef4444}</style></head><body>
<h2>账号管理</h2>
<p><a href="/">返回面板</a></p>
{msg}
<div class="card">
  <h3>新增账号</h3>
  <form method="post" action="/accounts/new">
    <div class="grid">
      <div><label>账号ID（英文/数字，唯一，留空自动生成）</label><input name="id" placeholder="acc2"></div>
      <div><label>展示名</label><input name="name" placeholder="副号-带货"></div>
      <div><label>浏览器档案目录（留空自动 profiles/xxx）</label><input name="profile_dir" placeholder="profiles/acc2"></div>
    </div>
    <label>启用监控</label>
    <select name="enabled"><option value="true">是</option><option value="false">否</option></select>
    <label>浏览器内核（建议两个账号各选一个，例如一个 Chrome、一个 Edge）</label>
    <select name="channel">
      <option value="">自动（本机 Chrome/Edge）</option>
      <option value="chrome">Google Chrome</option>
      <option value="msedge">Microsoft Edge</option>
      <option value="chromium">内置 Chromium（备选）</option>
    </select>
    <p><button class="btn" type="submit">添加</button></p>
  </form>
</div>
<div class="card">
  <h3>已有账号</h3>
  <p class="muted">「扫码登录」会弹出一个浏览器窗口：未登录时页面会出现二维码，用微信扫码并确认即可；登录态会保存到该账号的档案目录，之后发布/监控都免扫码。每个账号要分别扫一次（用对应的微信号扫）。</p>
  <p class="muted">✅ <b>不同账号 = 不同的浏览器</b>：每个账号都有自己独立的档案目录（登录态互不影响），再配合下面「浏览器内核」可分别指定 <b>Chrome / Edge</b>。所以两个账号可以<b>同时打开、同时操作、互不打架</b>。</p>
  <p class="muted">⚠️ 唯一的限制是<b>同一个账号</b>不能被两个进程同时打开（Chromium 的硬规则：一个档案同一时刻只能被一个进程占用）。因此面板点「扫码登录 / 打开发表页」时会自动为<b>这个账号</b>暂停后台监控（把档案让出来），关掉窗口后自动恢复；<b>其它账号不受影响，监控照常</b>。手动用终端跑 monitor_dm.py 的话请先 Ctrl+C，否则会出现「明明刚登录过，打开发表页却是未登录」。</p>
  <table><tr><th>ID</th><th>名称</th><th>档案目录</th><th>浏览器内核</th><th>监控</th><th>当前</th><th>登录态 / 实际视频号</th><th>操作</th></tr>
  {rows}
  </table>
</div>
</body></html>"""

ACCOUNT_ROW = """<tr>
  <td>{id}</td><td>{name}</td><td class="muted">{profile_dir}</td>
  <td>{browser}</td>
  <td>{enabled}</td><td>{current}</td><td>{login}</td>
  <td>
    <a class="btn" href="/accounts/{id}/login" target="_blank">扫码登录</a>
    <a class="ghost btn" href="/accounts/{id}/probe" target="_blank">检测登录态</a>
    <a class="ghost btn" href="/open_publish?account={id}" target="_blank">打开发表页</a>
    <a class="ghost btn" href="/accounts/{id}/set_current">设为当前</a>
    <a class="ghost btn" href="/accounts/{id}/resume">解除暂停</a>
    <a class="ghost btn" href="/accounts/{id}/delete" onclick="return confirm('确认删除该账号？')">删除</a>
  </td>
</tr>"""


def _channel_select(aid: str, cur: str) -> str:
    """该账号用哪个浏览器内核（Chrome/Edge/自动…）。选了立即生效（下一个新窗口）。"""
    cur = (cur or "").strip().lower()
    opts = []
    for val, label in accounts.CHANNEL_LABELS.items():
        sel = " selected" if val == cur else ""
        opts.append(f'<option value="{val}"{sel}>{html.escape(label)}</option>')
    return (
        f'<form method="post" action="/accounts/{aid}/channel" style="margin:0">'
        '<select name="channel" onchange="this.form.submit()" '
        'style="padding:4px 6px;font-size:12px;width:auto" '
        'title="每个账号可各用一个浏览器内核：例如账号A选Chrome、账号B选Edge，'
        '就是两个真正不同的浏览器，可同时打开">'
        + "".join(opts)
        + "</select></form>"
    )

CLONE_TPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>复用素材</title>
<style>body{background:#0f1115;color:#e6e9ef;font:14px system-ui;padding:24px;max-width:600px;margin:auto}
a{color:#3b82f6;text-decoration:none}label{display:block;margin:10px 0 4px;color:#9aa3b2;font-size:12px}
select{width:100%;background:#0f1115;border:1px solid #262b36;color:#e6e9ef;border-radius:8px;padding:8px;font:inherit}
.card{background:#181b22;border:1px solid #262b36;border-radius:12px;padding:16px;margin-bottom:16px}
.btn{display:inline-block;padding:8px 14px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;border:none;cursor:pointer}
.muted{color:#9aa3b2}</style></head><body>
<h2>复用素材到其他账号</h2>
<p class="muted">将「{title}」的内容（标题/文案/标签/视频/封面）复制一份到目标账号的素材库（状态重置为待发布）。</p>
<form method="post" action="/materials/{id}/clone">
  <label>目标账号</label>
  <select name="target">{opts}</select>
  <p><button class="btn" type="submit">复制到该账号</button> <a class="muted" href="/">取消</a></p>
</form></body></html>"""

PUBLISH_TPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>发布中心</title>
<style>body{{background:#0f1115;color:#e6e9ef;font:14px system-ui;padding:24px;max-width:none}}
a{{color:#3b82f6;text-decoration:none}}label{{display:block;margin:8px 0 4px;color:#9aa3b2;font-size:12px}}
select,textarea,input{{width:100%;background:#0f1115;border:1px solid #262b36;color:#e6e9ef;border-radius:8px;padding:8px;font:inherit}}
textarea{{min-height:90px;resize:vertical}}
.card{{background:#181b22;border:1px solid #262b36;border-radius:12px;padding:16px;margin-bottom:16px}}
.btn{{display:inline-block;padding:8px 14px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;border:none;cursor:pointer;font-size:13px;margin-right:6px}}
.ghost{{background:#222936;border:1px solid #262b36;color:#e6e9ef}}
.accs{{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}}
.accs label{{display:flex;align-items:center;gap:6px;color:#e6e9ef;font-size:13px;margin:0}}
.muted{{color:#9aa3b2}}.ok{{color:#22c55e}}.bad{{color:#ef4444}}
table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:8px;border-bottom:1px solid #262b36}}
.pubgrid{{display:grid;grid-template-columns:minmax(260px,320px) 1fr;gap:20px;align-items:start}}
@media (max-width:860px){{ .pubgrid{{grid-template-columns:1fr}} }}
.preview{{background:#0f1115;border:1px solid #262b36;border-radius:12px;overflow:hidden}}
.preview .pv-thumb{{height:180px;background:#000;display:flex;align-items:center;justify-content:center}}
.preview img,.preview video{{width:100%;height:100%;object-fit:cover;display:block}}
.preview .pv-body{{padding:12px}}
.preview .pv-title{{font-weight:600;font-size:14px;margin-bottom:6px}}
.preview .pv-desc{{font-size:12px;color:#9aa3b2;white-space:pre-wrap;max-height:120px;overflow:auto}}
.aiwarn{{background:rgba(168,85,247,.12);border:1px solid rgba(168,85,247,.4);border-radius:8px;padding:8px 12px;font-size:12px;margin:8px 0;color:#c084fc}}
.law{{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.35);border-radius:8px;padding:8px 12px;font-size:12px;margin:8px 0;color:#f59e0b}}
.checks label{{display:flex;align-items:center;gap:6px;color:#e6e9ef;font-size:13px;margin:4px 0}}
.checks input{{width:auto}}
h3{{margin:6px 0 10px;font-size:14px}}
.probe{{border:1px solid #262b36;border-radius:8px;padding:8px 12px;font-size:12px;margin:8px 0;background:#0f1115}}
.probe.ok{{color:#22c55e;border-color:rgba(34,197,94,.35)}}
.probe.bad{{color:#ef4444;border-color:rgba(239,68,68,.45)}}
.genbtn{{background:#222936;border:1px solid #262b36;color:#3b82f6;border-radius:6px;padding:1px 7px;font-size:11px;cursor:pointer;margin-left:6px}}
.genbtn:hover{{border-color:#3b82f6}}
.genbtn:disabled{{opacity:.5;cursor:default}}
.pv-cover{{padding:12px;border-top:1px solid #262b36}}
.pv-cover input[type=file]{{font-size:12px;padding:6px}}
.pv-cover .drop{{border:2px dashed #262b36;border-radius:10px;padding:10px;text-align:center;color:#9aa3b2;font-size:12px;cursor:pointer}}
.pv-cover .drop:hover{{border-color:#3b82f6;color:#3b82f6}}
.pv-cover .thumbs img{{width:100%;max-height:150px;object-fit:cover;border-radius:8px;display:block;margin-top:6px}}
#af_note{{font-size:12px;min-height:18px;color:#9aa3b2}}</style></head><body>
<h2>发布中心 <span class="muted">（仿官方发布页 1:1 字段 · 一条视频同步发多个视频号）</span></h2>
<p><a href="/">返回面板</a></p>
<div class="card">
  <div class="pubgrid">
    <div class="preview">
      <div class="pv-thumb" id="pv_thumb">{preview_thumb}</div>
      <div class="pv-body">
        <div class="pv-title" id="pv_title">{preview_title}</div>
        <div class="pv-desc" id="pv_desc">{preview_desc}</div>
      </div>
      <div class="pv-cover">{cover_upload}</div>
    </div>
    <div>
      <form method="post" action="/publish/start">
        {probe_html}
        <label>选择素材（仅列出视频文件存在的素材）</label>
        <select name="material_id" id="matSel" onchange="location.href='/publish?material='+this.value">{material_options}</select>
        <label>短标题 <span class="muted">（6-16 字，展示在搜索/话题/红点；留空则用素材里配置）</span>
          <span id="st_count" class="muted"></span>
          <button type="button" class="genbtn" onclick="genField('short_title')">✨ AI填写</button></label>
        <input name="short_title" id="p_short" maxlength="16" value="{short_title}" oninput="checkShort()">
        <label>描述 / 文案 <span class="muted">（官方上限 1000 字；留空则用素材里配置）</span>
          <button type="button" class="genbtn" onclick="genField('desc')">✨ AI填写</button></label>
        <textarea name="desc" id="p_desc">{desc}</textarea>
        <label>话题标签（逗号分隔，自动加 #）
          <button type="button" class="genbtn" onclick="genField('tags')">✨ AI填写</button></label>
        <input name="tags" id="p_tags" value="{tags}">
        <div id="af_note"></div>
        <div class="law">⚖️ 依据《人工智能生成合成内容标识办法》（2025-09-01 施行），AI 生成/合成的视频发布时必须主动声明，未声明将被限流、下架甚至封号。</div>
        <label>视频标注 <span class="muted">（AI 视频务必选「内容为AI制作」）</span></label>
        <select name="ai_declare">
          {ai_opts}
        </select>
        <div class="checks">
          <label><input type="checkbox" name="original" value="true" {orig_checked}> 声明原创（展示原创标记，有机会获得广告收入）</label>
          <label><input type="checkbox" name="allow_download" value="true" {dl_checked}> 允许他人下载视频</label>
          <label><input type="checkbox" name="allow_comment" value="true" {cm_checked}> 允许评论</label>
        </div>
        <div class="grid" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div><label>可见范围</label>
            <select name="visibility">{vis_opts}</select></div>
          <div><label>定时发表 <span class="muted">（助手端专属；留空=立即发表）</span></label>
            <input type="datetime-local" name="scheduled_at" value="{scheduled_at}"></div>
        </div>
        <label>扩展链接 <span class="muted">（公众号文章需近 7 天发表且阅读量超 10000；选填）</span></label>
        <input name="ext_link" value="{ext_link}" placeholder="https://mp.weixin.qq.com/s/...">
        <label>选择要发布的账号（可多选，将按顺序逐号发布）</label>
        <div class="accs">{account_checkboxes}</div>
        <p class="muted">发布由 AI Agent 操作网页完成，以上配置会逐号自动填写；默认有头浏览器可肉眼确认；发布期间对应账号的私信监控会暂停（文件锁互斥）。</p>
        <button class="btn" type="submit">开始同步发布</button>
      </form>
    </div>
  </div>
</div>
<div class="card">
  <h3>最近一次发布任务 <span id="autoref" class="muted" style="font-size:12px"></span></h3>
  <div id="jobwrap">{job_status}</div>
</div>
<script>
/* 智能填写：字段 -> input id（发布页没有标题字段，留空即可） */
var AF_MAP={{title:'', short_title:'p_short', desc:'p_desc', tags:'p_tags', ai:''}};
AF_FALLBACK_NAME={mat_name_json};
/* 生成后同步刷新左侧预览 */
function afterAutofill(d){{
  var t=document.getElementById('pv_title'), s=document.getElementById('pv_desc');
  if(t && d.short_title) t.textContent=d.short_title;
  if(s && d.desc) s.textContent=d.desc;
  checkShort();
}}
{{form_js}}
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
var _jobRunning = {job_running};
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
</script>
</body></html>"""

REPLY_TPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>自动回复规则</title>
<style>body{{background:#0f1115;color:#e6e9ef;font:14px system-ui;padding:24px;max-width:none}}
a{{color:#3b82f6;text-decoration:none}}label{{display:block;margin:8px 0 4px;color:#9aa3b2;font-size:12px}}
input,textarea,select{{width:100%;background:#0f1115;border:1px solid #262b36;color:#e6e9ef;border-radius:8px;padding:8px;font:inherit}}
.card{{background:#181b22;border:1px solid #262b36;border-radius:12px;padding:16px;margin-bottom:16px}}
.btn{{display:inline-block;padding:7px 12px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;border:none;cursor:pointer;font-size:13px;margin-right:6px}}
.ghost{{background:#222936;border:1px solid #262b36;color:#e6e9ef}}
table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:8px;border-bottom:1px solid #262b36;vertical-align:top}}
.grid{{display:grid;grid-template-columns:1fr 2fr 140px;gap:12px}}.muted{{color:#9aa3b2}}
.badge{{display:inline-block;padding:1px 8px;border-radius:6px;font-size:12px}}.nowrap{{white-space:nowrap}}
.on{{background:rgba(34,197,94,.15);color:#22c55e}}.off{{background:rgba(239,68,68,.15);color:#ef4444}}
.note{{padding:10px 12px;border-radius:8px;font-size:13px;margin:10px 0}}
.ok{{background:rgba(34,197,94,.12);color:#22c55e}}.warn{{background:rgba(245,158,11,.14);color:#f59e0b}}</style></head><body>
<h2>固定规则自动回复</h2>
<p><a href="/">返回面板</a> <span class="muted">| 总开关 AUTO_REPLY_ENABLED：{enabled} ｜ 评论回复 AUTO_REPLY_COMMENTS：{comment_enabled}</span></p>
{default_note}
<div class="card">
  <h3>统一后缀 <span class="muted" style="font-weight:400;font-size:13px">（每条自动回复都会自动带上；留空 = 不追加）</span></h3>
  <form method="post" action="/reply/suffix">
    <label>后缀文案</label>
    <textarea name="suffix" rows="2" placeholder="方便的话你把联系方式发我（平台这边发号码容易被限制），我看到马上加你">{suffix}</textarea>
    <p style="margin:10px 0 0">
      <label style="display:inline-block;margin:0"><input type="checkbox" name="enabled" value="1" {suffix_checked} style="width:auto;vertical-align:middle"> 启用统一后缀</label>
      <button class="btn" type="submit" style="margin-left:14px">保存后缀</button>
    </p>
    <p class="muted" style="font-size:12px;margin:8px 0 0">已自带「联系方式 / 号码 / 发我 / 加你」等同类引导的规则不会重复追加，避免一句话说两遍。</p>
  </form>
</div>
<div class="card">
  <h3>新增规则（私信 / 评论包含关键词 → 自动发送固定回复）</h3>
  <form method="post" action="/reply/new">
    <div class="grid">
      <div><label>触发关键词（多个用英文逗号分隔；填 <b>*</b> 或留空 = 默认回复，任何消息都回）</label><input name="keyword" placeholder="价格,多少钱,怎么卖"></div>
      <div><label>固定回复内容</label><input name="reply" required placeholder="您好，具体价格私信我帮您报~"></div>
      <div><label>适用范围</label><select name="scope"><option value="both">私信+评论</option><option value="dm">仅私信</option><option value="comment">仅评论</option></select></div>
    </div>
    <p><button class="btn" type="submit">添加规则</button></p>
  </form>
  <form method="post" action="/reply/seed" style="margin-top:6px;display:inline-block">
    <button class="btn ghost" type="submit">⬇ 一键导入推荐规则（共 {preset_count} 条模板，只补缺失）</button>
  </form>
  <form method="post" action="/reply/refresh" style="margin-top:6px;display:inline-block">
    <button class="btn ghost" type="submit" onclick="return confirm('把未手动改过的规则刷新为最新优化文案？你自己改过的不会被覆盖。')">🔄 刷新为最新优化文案（不动你改过的）</button>
  </form>
</div>
<div class="card">
  <h3>已有规则（{count} 条，按顺序匹配，命中第一条即回复；可直接在行内修改后点「保存」）</h3>
  <table><tr><th style="width:22%">关键词</th><th>回复内容</th><th style="width:120px">范围</th><th style="width:78px">状态</th><th style="width:196px">操作</th></tr>
  {rows}
  </table>
</div>
</body></html>"""

REPLY_ROW = """<tr>
  <td><input name="keyword" form="rf{id}" value="{keyword}" placeholder="* = 默认回复" style="min-width:120px"></td>
  <td><input name="reply" form="rf{id}" value="{reply}" style="min-width:260px"></td>
  <td><select name="scope" form="rf{id}">{scope_opts}</select></td>
  <td class="nowrap"><span class="badge {cls}">{state}</span></td>
  <td class="nowrap">
    <button class="btn" type="submit" form="rf{id}">保存</button>
    <a class="ghost btn" href="/reply/{id}/toggle">{toggle}</a>
    <a class="ghost btn" href="/reply/{id}/delete" onclick="return confirm('删除该规则？')">删除</a>
    <form id="rf{id}" method="post" action="/reply/{id}/update"></form>
  </td>
</tr>"""

MP_TPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>公众号管理</title>
<style>body{{background:#0f1115;color:#e6e9ef;font:14px system-ui;padding:24px;max-width:none}}
a{{color:#3b82f6;text-decoration:none}}label{{display:block;margin:8px 0 4px;color:#9aa3b2;font-size:12px}}
input,textarea{{width:100%;background:#0f1115;border:1px solid #262b36;color:#e6e9ef;border-radius:8px;padding:8px;font:inherit}}
textarea{{min-height:140px;resize:vertical}}
.card{{background:#181b22;border:1px solid #262b36;border-radius:12px;padding:16px;margin-bottom:16px}}
.btn{{display:inline-block;padding:7px 12px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;border:none;cursor:pointer;font-size:13px;margin-right:6px}}
.ghost{{background:#222936;border:1px solid #262b36;color:#e6e9ef}}
table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:8px;border-bottom:1px solid #262b36}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.muted{{color:#9aa3b2}}
.msg{{padding:10px 12px;border-radius:8px;margin:10px 0;font-size:13px}}
.ok{{background:rgba(34,197,94,.12);color:#22c55e}}.bad{{background:rgba(239,68,68,.12);color:#ef4444}}</style></head><body>
<h2>公众号管理（官方 API）</h2>
<p><a href="/">返回面板</a></p>
{msg}
<div class="card">
  <h3>绑定公众号 {bound}</h3>
  <p class="muted">在公众平台「设置与开发 → 基本配置」获取 AppID / AppSecret，并把本机 IP 加入 IP 白名单。</p>
  <form method="post" action="/mp/bind">
    <div class="grid">
      <div><label>AppID</label><input name="appid" value="{appid}" required></div>
      <div><label>AppSecret</label><input name="secret" value="{secret}" required></div>
    </div>
    <p><button class="btn" type="submit">保存绑定</button></p>
  </form>
</div>
{content}
</body></html>"""

MP_CONTENT = """<div class="card">
  <h3>账号概览</h3>
  <p>粉丝数（关注总数）：<b>{followers}</b></p>
</div>
<div class="card">
  <h3>新建图文草稿</h3>
  <form method="post" action="/mp/draft/new">
    <div class="grid">
      <div><label>标题 *</label><input name="title" required></div>
      <div><label>作者</label><input name="author"></div>
    </div>
    <label>封面图本地路径 *（公众号图文必须有封面）</label>
    <input name="cover_path" placeholder="d:/images/cover.jpg">
    <label>摘要（留空则取标题）</label>
    <input name="digest">
    <label>正文（纯文本会自动分段；也支持简单 HTML）</label>
    <textarea name="content" placeholder="第一段…&#10;第二段…"></textarea>
    <p><button class="btn" type="submit">保存到草稿箱</button></p>
  </form>
</div>
<div class="card">
  <h3>草稿箱（最新 10 篇）</h3>
  <table><tr><th>标题</th><th>更新时间</th><th>操作</th></tr>
  {draft_rows}
  </table>
</div>"""


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _account_options() -> str:
    cur = accounts.current_account_id()
    return "".join(
        f'<option value="{a["id"]}"{" selected" if a["id"]==cur else ""}>{a["name"]} ({a["id"]})</option>'
        for a in accounts.load_accounts()
    )


def _thumb_html(cover: str, video: str) -> str:
    if cover:
        ap = cover if os.path.isabs(cover) else os.path.join(BASE, cover)
        if os.path.exists(ap):
            return f'<img src="/serve?p={quote(ap)}" loading="lazy" alt="">'
    if video:
        ap = video if os.path.isabs(video) else os.path.join(BASE, video)
        if os.path.exists(ap):
            ext = ap.rsplit(".", 1)[-1].lower() if "." in ap else ""
            if ext in ("mp4", "webm", "mov", "m4v"):
                return f'<video src="/serve?p={quote(ap)}" preload="metadata" muted playsinline></video>'
            if ext in materials.MEDIA_EXTS:
                return f'<img src="/serve?p={quote(ap)}" loading="lazy" alt="">'
    return '<div class="thumb-ph">🎬</div>'


def _login_badge(aid: str) -> str:
    """账号登录态徽章。

    两层信息：
      1. 本地启发式（档案 Cookie 新鲜度）——告诉你「大概有没有登录」；
      2. **实测身份**（state/login_probe_<id>.json）——告诉你「这个档案实际登的是哪个视频号」。
    两个账号如果都登成了同一个视频号，第 2 条一眼就能看出来（这是「两个账号冲突」最隐蔽的形态）。
    """
    acc = accounts.get_account(aid)
    if not acc:
        return ""
    st = login_state.status(accounts.profile_path(acc))
    cls = "ok" if (st["logged_in"] and st["fresh"]) else ("warn" if st["logged_in"] else "bad")
    txt = "已登录" if (st["logged_in"] and st["fresh"]) else ("可能失效" if st["logged_in"] else "未登录")
    out = f'<span class="dot {cls}" title="{st["detail"]}"></span><span class="muted" style="font-size:11px">{txt}</span>'
    try:
        from wechannels import loginprobe

        ident = loginprobe.read_identity(aid)
    except Exception:
        ident = {}
    if ident:
        if ident.get("logged_in"):
            nick = ident.get("nickname") or "（昵称读取失败）"
            _br = ident.get("browser")
            _brh = (
                f' · 内核 <span title="上次实测该账号所用浏览器内核">{html.escape(str(_br))}</span>'
                if _br else ""
            )
            out += (
                f'<div class="muted" style="font-size:11px">实测视频号：<b>{html.escape(str(nick))}</b>'
                f'<span title="探测时间">（{html.escape(str(ident.get("at", "")))}）</span>{_brh}</div>'
            )
        else:
            out += '<div style="font-size:11px;color:#ef4444">实测：未登录（请扫码）</div>'
    pz = proclock.paused_desc(aid)
    if pz:
        out += f'<div style="font-size:11px;color:#f59e0b">{html.escape(pz)}</div>'
    return out


def _material_badges(m: dict) -> str:
    """素材卡上的配置徽章：AI 声明 / 原创 / 定时。"""
    out = []
    ai = (m.get("ai_declare") or "").strip()
    if ai:
        out.append(f'<span class="abadge ai" title="视频标注（依法需声明）">🤖 {ai}</span>')
    if m.get("original"):
        out.append('<span class="abadge orig" title="发布时声明原创">原创</span>')
    sched = (m.get("scheduled_at") or "").strip()
    if sched:
        out.append(f'<span class="abadge sched" title="定时发表">⏰ {sched[:16].replace("T", " ")}</span>')
    return "".join(out)


def _material_history_html(m: dict) -> str:
    """最近发布记录摘要（最多显示 2 条）。"""
    hist = m.get("publish_history") or []
    if not hist:
        return ""
    lines = []
    for h in hist[-2:][::-1]:
        mark = "✅" if h.get("ok") else "❌"
        lines.append(
            f'<div class="hist">{mark} {h.get("time","")} → {h.get("account_name","")}'
            f' <span class="{"ok" if h.get("ok") else "bad"}">{(h.get("result") or "")[:40]}</span></div>'
        )
    total = len(hist)
    more = f'<div class="hist muted">共 {total} 次发布记录</div>' if total > 2 else ""
    return "".join(lines) + more


def _material_card_html(m: dict) -> str:
    mid = m["id"]
    status = m.get("status", "待发布")
    scls = {"待发布": "warn", "已发布": "ok", "草稿": "acc", "发布中": "acc", "失败": "bad"}.get(status, "acc")
    cover = (m.get("cover_path") or "").strip()
    video = (m.get("video_path") or "").strip()
    thumb = _thumb_html(cover, video)
    tags = "".join(
        f'<span class="tag">#{t.strip()}</span>' for t in (m.get("tags") or "").split(",") if t.strip()
    )
    pathtxt = video or cover or "（未设置文件）"
    copy = (materials.copy_text(m) or "").replace('"', "&quot;")
    return CARD.format(
        status=status, statuscls=scls, thumb=thumb,
        title=(m.get("title") or "(无标题)").replace('"', "&quot;"),
        tags=tags, copy=copy, id=mid, path=pathtxt,
        badges=_material_badges(m), history=_material_history_html(m),
    )


def _match_material(m: dict, q: str) -> bool:
    q = (q or "").strip().lower()
    if not q:
        return True
    hay = " ".join([m.get("title", ""), m.get("short_title", ""), m.get("desc", ""), m.get("tags", "")])
    return q in hay.lower()


def _materials_grid(status_filter: str | None, q: str = "", sort: str = "new") -> str:
    acc_id = accounts.current_account_id()
    items = materials.load_materials(acc_id)
    if status_filter:
        items = [it for it in items if it.get("status") == status_filter]
    if q:
        items = [it for it in items if _match_material(it, q)]
    if sort == "updated":
        items.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    elif sort == "status":
        order = {s: i for i, s in enumerate(materials.STATUSES)}
        items.sort(key=lambda m: order.get(m.get("status", ""), 99))
    else:
        items.reverse()  # 最新创建在前（load 顺序即创建顺序）
    if not items:
        hint = "没有匹配的素材。" if (q or status_filter) else "还没有素材，先在上面新建一条（或用「目录导入」批量导入）。"
        return f'<p class="muted">{hint}</p>'
    return '<div class="grid-mat">' + "".join(_material_card_html(m) for m in items) + "</div>"


def _spawn_open_publish(account_id: str, login: bool = False) -> None:
    """拉起 open_publish.py 子进程（新控制台窗口，方便看扫码提示）。

    login=True 时打开视频号登录页（未登录自动出二维码），扫码后登录态
    持久化到该账号档案；完成后在面板重新启动监控即可。
    """
    env = dict(os.environ, ACCOUNT_ID=account_id)
    args = [sys.executable, os.path.join(BASE, "open_publish.py")]
    if login:
        args.append("--login")
    subprocess.Popen(
        args,
        cwd=BASE,
        env=env,
        creationflags=CREATE_NEW_CONSOLE,
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    sf = request.args.get("status")
    q = request.args.get("q", "")
    sort = request.args.get("sort", "new")
    html = TEMPLATE
    html = html.replace("{account_options}", _account_options())
    html = html.replace("{account_count}", str(len(accounts.load_accounts())))
    _cur_acc = accounts.current_account()
    _cur_acc_name = (_cur_acc.get("name") or _cur_acc["id"]).replace("<", "&lt;").replace(">", "&gt;")
    html = html.replace("{cur_account_id}", _cur_acc["id"])
    html = html.replace("{cur_account_name}", _cur_acc_name)
    html = html.replace("{count}", str(len(materials.load_materials(accounts.current_account_id()))))
    html = html.replace("{materials_grid}", _materials_grid(sf, q, sort))
    html = html.replace("{new_material_form}", NEW_FORM)
    html = html.replace("{q}", q)
    html = html.replace("{sf}", sf or "")
    html = html.replace("{s_new}", "selected" if sort == "new" else "")
    html = html.replace("{s_updated}", "selected" if sort == "updated" else "")
    html = html.replace("{s_status}", "selected" if sort == "status" else "")
    html = html.replace("{f_all}", "on" if not sf else "")
    html = html.replace("{f_待发布}", "on" if sf == "待发布" else "")
    html = html.replace("{f_草稿}", "on" if sf == "草稿" else "")
    html = html.replace("{f_已发布}", "on" if sf == "已发布" else "")
    return html


@app.route("/switch_account", methods=["POST"])
def switch_account():
    aid = request.form.get("account_id")
    if accounts.get_account(aid):
        accounts.set_current_account(aid)
    return redirect(url_for("index"))


def _apply_uploads(data: dict) -> dict:
    """把表单里上传的 video_file / cover_file 落盘到 media/，并把路径写回 data。"""
    for field, key in (("video_file", "video_path"), ("cover_file", "cover_path")):
        f = request.files.get(field)
        if f and f.filename:
            fn = _safe_name(f.filename)
            dest = os.path.join(materials.media_dir(), fn)
            f.save(dest)
            data[key] = os.path.join("media", fn)
    return data


def _thumb_preview(p: str, kind: str = "image") -> str:
    """编辑页的本地缩略预览（视频用 <video>，图片用 <img>）；没有则返回空。"""
    p = (p or "").strip()
    if not p:
        return ""
    ap = p if os.path.isabs(p) else os.path.join(BASE, p)
    if not os.path.exists(ap):
        return ""
    url = f"/serve?p={quote(ap)}"
    if kind == "video":
        return f'<video src="{url}" preload="metadata" muted playsinline></video>'
    return f'<img src="{url}" loading="lazy" alt="">'


@app.route("/materials/new", methods=["POST"])
def new_material():
    data = _apply_uploads(request.form.to_dict())
    materials.add_material(data)  # 自动归属当前账号
    return redirect(url_for("index"))


@app.route("/materials/autofill")
def materials_autofill():
    """智能填写：根据文件名/提示用 LLM 生成素材字段建议（JSON）。"""
    import json as _json

    from wechannels import autofill as af

    name = request.args.get("name", "")
    hint = request.args.get("hint", "")
    field = request.args.get("field", "")
    out = af.autofill(name, hint, field)
    out.setdefault("_source", "llm" if config.LLM_API_KEY else "local")
    if "_note" not in out and not config.LLM_API_KEY:
        out["_note"] = "未配置 LLM_API_KEY，用的是本地规则；在 .env 配置后可 AI 生成"
    return app.response_class(
        _json.dumps(out, ensure_ascii=False),
        mimetype="application/json",
    )


@app.route("/materials/<mid>/edit", methods=["GET", "POST"])
def edit_material(mid):
    m = materials.get_material(mid)
    if not m:
        return redirect(url_for("index"))
    if request.method == "POST":
        data = request.form.to_dict()
        _apply_uploads(data)
        materials.update_material(mid, data)
        return redirect(url_for("index"))
    video_thumb = _thumb_preview(m.get("video_path"), "video")
    cover_thumb = _thumb_preview(m.get("cover_path"), "image")
    opts = "".join(
        f'<option{" selected" if m.get("status") == s else ""}>{s}</option>' for s in materials.STATUSES
    )
    cur_ai = m.get("ai_declare") or ""
    ai_opts = "".join(
        f'<option value="{v}"{" selected" if v == cur_ai else ""}>{v or "不标注（非 AI 内容）"}</option>'
        for v in materials.AI_DECLARE_OPTIONS if v != "非AI内容"
    )
    cur_vis = m.get("visibility") or "公开"
    vis_opts = "".join(
        f'<option{" selected" if v == cur_vis else ""}>{v}</option>' for v in materials.VISIBILITY_OPTIONS
    )
    hist = m.get("publish_history") or []
    if hist:
        history_html = "".join(
            f'<div class="hist">{"✅" if h.get("ok") else "❌"} {h.get("time","")} → {h.get("account_name","")} '
            f'<span class="{"ok" if h.get("ok") else "bad"}">{(h.get("result") or "")[:80]}</span></div>'
            for h in reversed(hist)
        )
    else:
        history_html = '<p class="muted">还没有发布记录。每次同步发布的结果会自动记录在这里。</p>'
    html = EDIT_TPL
    for k in ("id", "title", "short_title", "desc", "tags", "video_path", "cover_path", "ext_link"):
        html = html.replace("{" + k + "}", (m.get(k) or "").replace('"', "&quot;"))
    sched = (m.get("scheduled_at") or "").replace(" ", "T")[:16]
    html = html.replace("{scheduled_at}", sched)
    html = html.replace("{ai_opts}", ai_opts)
    html = html.replace("{vis_opts}", vis_opts)
    html = html.replace("{orig_no}", "" if m.get("original") else "selected")
    html = html.replace("{orig_yes}", "selected" if m.get("original") else "")
    html = html.replace("{history_html}", history_html)
    html = html.replace("{opts}", opts)
    html = html.replace("{video_thumb}", video_thumb)
    html = html.replace("{cover_thumb}", cover_thumb)
    return html


@app.route("/materials/<mid>/cover", methods=["POST"])
def upload_cover(mid):
    """发布中心/编辑页上传封面 → 落盘 media/ 并写回素材。"""
    m = materials.get_material(mid)
    if not m:
        return redirect(url_for("index"))
    cf = request.files.get("cover_file")
    if cf and cf.filename:
        fn = _safe_name(cf.filename)
        dest = os.path.join(materials.media_dir(), fn)
        cf.save(dest)
        materials.update_material(mid, {"cover_path": os.path.join("media", fn)})
    nxt = request.form.get("next") or request.args.get("next") or ""
    if nxt == "publish":
        return redirect(f"/publish?material={mid}")
    return redirect(url_for("index"))


@app.route("/materials/<mid>/delete")
def delete_material_route(mid):
    materials.delete_material(mid)
    return redirect(url_for("index"))


@app.route("/materials/batch", methods=["POST"])
def materials_batch():
    mids = request.form.getlist("mids")
    op = request.form.get("op", "")
    if not mids:
        return redirect(url_for("index"))
    if op == "delete":
        materials.delete_many(mids)
    elif op.startswith("mark_"):
        st = op[len("mark_"):]
        if st in materials.STATUSES:
            materials.set_status_many(mids, st)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# 本地目录导入（连接本地目录，可视化挑选视频批量入库）
# ---------------------------------------------------------------------------
IMPORT_TPL = """<!doctype html><html lang="zh-CN" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>目录导入</title>
<script>
  (function(){{try{{var t=localStorage.getItem('theme')||'dark';document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}}})();
</script>
<style>
  :root{{--bg:#0f1115;--card:#181b22;--line:#262b36;--fg:#e6e9ef;--mut:#9aa3b2;
    --acc:#3b82f6;--ok:#22c55e;--elev:#1f232b;--elev2:#262b34;}}
  [data-theme="light"]{{--bg:#f4f6f9;--card:#ffffff;--line:#dee2e6;--fg:#1f2933;--mut:#5b6675;
    --acc:#2563eb;--ok:#16a34a;--elev:#f8fafc;--elev2:#eef2f7;}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}}
  .wrap{{max-width:1180px;margin:0 auto;padding:22px clamp(14px,3vw,32px) 60px}}
  header{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:14px}}
  h2{{font-size:17px;margin:0}}
  h3{{font-size:14px;margin:0 0 10px}}
  a{{color:var(--acc);text-decoration:none}}
  label{{display:block;margin:10px 0 4px;color:var(--mut);font-size:12px}}
  input,textarea{{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px;font:inherit}}
  textarea{{resize:vertical;min-height:92px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}}
  .btn{{display:inline-block;padding:8px 14px;background:var(--acc);color:#fff;border-radius:8px;text-decoration:none;border:none;cursor:pointer;font-size:13px}}
  .btn.ghost{{background:var(--elev2);color:var(--fg);border:1px solid var(--line)}}
  .theme-btn{{border:1px solid var(--line);background:var(--elev);color:var(--fg);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:13px}}
  .muted{{color:var(--mut);font-size:12px}}
  .ways{{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}}
  @media (max-width:860px){{ .ways{{grid-template-columns:1fr}} }}
  .way{{border:1px solid var(--line);border-radius:10px;padding:12px;background:var(--elev)}}
  .way .tagline{{display:inline-block;background:var(--elev2);border:1px solid var(--line);border-radius:20px;
    padding:1px 10px;font-size:11px;color:var(--mut);margin-bottom:6px}}
  .msg{{padding:10px 12px;border-radius:8px;margin:10px 0;font-size:13px}}
  .ok{{background:rgba(34,197,94,.12);color:var(--ok)}}
  /* 文件列表：勾选框 / 文件名 / 大小 / 时间 —— 四列网格对齐，文件名过长省略号 */
  .filelist{{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:10px;margin-top:10px}}
  .fhead,.frow{{display:grid;grid-template-columns:26px minmax(0,1fr) 96px 152px;align-items:center;gap:12px;padding:8px 12px}}
  .fhead{{position:sticky;top:0;background:var(--elev2);color:var(--mut);font-size:12px;border-bottom:1px solid var(--line);z-index:1}}
  .frow{{border-bottom:1px solid var(--line);cursor:pointer;margin:0;color:var(--fg);font-size:13px}}
  .frow:last-child{{border-bottom:none}}
  .frow:hover{{background:var(--elev)}}
  .fname{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .fsize,.ftime{{color:var(--mut);font-size:12px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
  .listbar{{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:12px}}
</style></head><body>
<div class="wrap">
<header>
  <h2>从本地目录导入素材</h2>
  <div><a class="btn ghost" href="/">← 返回面板</a>
    <button class="theme-btn" id="themeBtn" onclick="toggleTheme()">🌙 夜视</button></div>
</header>
<p class="muted">把本地视频文件夹连接到素材库，勾选批量导入（标题默认取文件名）。支持 mp4/mov/webm/mkv/avi/flv/ts。</p>
{msg}

<div class="card">
  <div class="ways">
    <div class="way">
      <span class="tagline">方式一</span>
      <h3>扫描本地目录</h3>
      <form method="get" action="/import">
        <label>目录路径（不递归，只列出该目录下的视频文件）</label>
        <input name="dir" value="{dir}" placeholder="d:/videos/待发布">
        <p><button class="btn" type="submit">扫描目录</button></p>
      </form>
    </div>
    <div class="way">
      <span class="tagline">方式二</span>
      <h3>粘贴文件路径批量导入</h3>
      <form method="post" action="/import/do">
        <label>每行一个路径，支持任意位置的文件</label>
        <textarea name="paths" placeholder="d:/videos/a.mp4&#10;d:/videos/b.mov&#10;e:/素材/新品上架.mp4"></textarea>
        <p><button class="btn" type="submit">按路径批量导入到当前账号</button></p>
      </form>
    </div>
  </div>
</div>

{filelist}
</div>
<script>
function toggleTheme(){{
  var cur=document.documentElement.getAttribute('data-theme');
  var next=cur==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',next);
  try{{localStorage.setItem('theme',next);}}catch(e){{}}
  document.getElementById('themeBtn').textContent = next==='dark'?'🌙 夜视':'☀️ 日间';
}}
document.getElementById('themeBtn').textContent =
  document.documentElement.getAttribute('data-theme')==='dark'?'🌙 夜视':'☀️ 日间';
</script>
</body></html>"""

FILE_ROW = """<label class="frow"><input type="checkbox" name="files" value="{name}">
  <span class="fname" title="{name}">{name}</span>
  <span class="fsize">{size_mb} MB</span>
  <span class="ftime">{mtime}</span></label>"""


@app.route("/import")
def import_page():
    d = request.args.get("dir", "")
    done = request.args.get("done", "")
    msg = ""
    if done:
        msg = f'<div class="msg ok">已导入 {done} 条素材到当前账号素材库。</div>'
    files_html = ""
    if d:
        vids = materials.list_dir_videos(d)
        if not vids:
            files_html = (
                '<div class="card"><h3>视频列表</h3>'
                '<p class="muted">该目录下没有找到视频文件（支持 mp4/mov/webm/mkv/avi/flv/ts）。</p></div>'
            )
        else:
            rows = "".join(FILE_ROW.format(name=v["name"], size_mb=v["size_mb"], mtime=v["mtime"]) for v in vids)
            files_html = (
                '<div class="card">'
                "<h3>视频列表 <span class=\"muted\">"
                f"（{d}）</span></h3>"
                '<form method="post" action="/import/do">'
                f'<input type="hidden" name="dir" value="{d}">'
                '<div class="filelist">'
                '<div class="fhead"><span>选择</span><span>文件名</span>'
                '<span class="fsize">大小</span><span class="ftime">修改时间</span></div>'
                f"{rows}</div>"
                '<div class="listbar">'
                f'<span class="muted">共 {len(vids)} 个视频，已选 <b id="nSel">0</b> 个。'
                '<a href="javascript:void(0)" style="margin-left:8px" onclick="setAll(true)">全选</a> /'
                ' <a href="javascript:void(0)" onclick="setAll(false)">取消</a></span>'
                '<button class="btn" type="submit">导入所选到当前账号素材库</button>'
                "</div></form></div>"
                "<script>"
                "function setAll(on){document.querySelectorAll('.frow input').forEach(function(x){x.checked=on});countSel();}"
                "function countSel(){var n=document.querySelectorAll('.frow input:checked').length;"
                "var e=document.getElementById('nSel');if(e)e.textContent=n;}"
                "document.addEventListener('DOMContentLoaded',function(){"
                "document.querySelectorAll('.frow input').forEach(function(x){x.addEventListener('change',countSel)});countSel();});"
                "</script>"
            )
    return IMPORT_TPL.format(dir=d, filelist=files_html, msg=msg)


@app.route("/import/do", methods=["POST"])
def import_do():
    d = request.form.get("dir", "")
    files = request.form.getlist("files")
    paths_raw = request.form.get("paths", "")
    n = 0
    if files and d:
        n += materials.import_from_dir(d, files)
    if paths_raw.strip():
        paths = [ln.strip() for ln in paths_raw.splitlines() if ln.strip()]
        n += materials.import_paths(paths)
    return redirect(f"/import?dir={quote(d)}&done={n}")


@app.route("/materials/<mid>/status")
def status_route(mid):
    status = request.args.get("status", "已发布")
    materials.set_status(mid, status)
    return redirect(url_for("index"))


@app.route("/materials/<mid>/clone", methods=["GET", "POST"])
def clone_material_route(mid):
    m = materials.get_material(mid)
    if not m:
        return redirect(url_for("index"))
    if request.method == "POST":
        tgt = request.form.get("target")
        if tgt and accounts.get_account(tgt):
            materials.clone_material(mid, tgt)
        return redirect(url_for("index"))
    cur = accounts.current_account_id()
    src_acc = m.get("account_id", cur)
    opts = "".join(
        f'<option value="{a["id"]}">{(a["name"])}（{a["id"]}）</option>'
        for a in accounts.load_accounts() if a["id"] != src_acc
    ) or '<option value="">（没有其它账号可复用）</option>'
    return CLONE_TPL.format(title=(m.get("title") or ""), opts=opts, id=mid)


@app.route("/open_publish")
def open_publish():
    """"打开发表页"：先给该账号打「手动操作暂停」标记，再打开浏览器。

    为什么要暂停：后台监控每隔 MONITOR_INTERVAL 秒会用**有头**浏览器打开同一个
    账号档案（抓评论必须非无头）。若两边同时打开同一档案，手动这边会拿不到登录态，
    页面只剩二维码 —— 用户看到的就是「刚刚两个账号都登录过了，打开发表页却没登录」。
    """
    aid = request.args.get("account") or accounts.current_account_id()
    acc = accounts.get_account(aid) or accounts.current_account()
    proclock.pause(acc["id"], reason="打开发表页")
    _spawn_open_publish(acc["id"])
    return redirect(url_for("index"))


# 各通知渠道的配置步骤（未配置时显示在测试通知结果页）


# ---------------------------------------------------------------------------
# 监控启停（聚合到面板）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 媒体服务（缩略图/封面预览）
# ---------------------------------------------------------------------------
@app.route("/serve")
def serve_file():
    p = request.args.get("p", "")
    if not p:
        return "", 404
    ap = os.path.abspath(p)
    if not os.path.isfile(ap):
        return "", 404
    ext = ap.rsplit(".", 1)[-1].lower() if "." in ap else ""
    if ext not in materials.MEDIA_EXTS:
        return "不支持的媒体类型", 403
    try:
        return send_file(ap)
    except Exception:
        return "", 404


@app.route("/media/<path:name>")
def media_file(name):
    p = os.path.join(materials.media_dir(), os.path.basename(name))
    if not os.path.isfile(p):
        return "", 404
    try:
        return send_file(p)
    except Exception:
        return "", 404


def _safe_name(orig: str) -> str:
    fn = secure_filename(orig)
    if not fn:
        fn = f"file_{int(time.time())}"
    dest = os.path.join(materials.media_dir(), fn)
    if os.path.exists(dest):
        fn = f"{int(time.time())}_{fn}"
    return fn


@app.route("/accounts")
def accounts_page():
    cur = accounts.current_account_id()
    rows = "".join(
        ACCOUNT_ROW.format(
            id=a["id"],
            name=a["name"],
            profile_dir=a.get("profile_dir", ""),
            browser=_channel_select(a["id"], accounts.channel_of(a)),
            enabled="✔" if a.get("enabled", True) else "—",
            current="●" if a["id"] == cur else "",
            login=_login_badge(a["id"]),
        )
        for a in accounts.load_accounts()
    )
    msg = ""
    m = request.args.get("msg")
    if m == "ok":
        msg = '<div class="msg ok">账号已添加（浏览器档案目录已创建）。点击「扫码登录」用对应微信号登录后即可使用。</div>'
    elif m == "login":
        msg = '<div class="msg ok">已打开扫码窗口（新弹出的控制台窗口有操作提示）。用微信扫描页面上的二维码并确认，控制台出现「登录成功」即可；之后可在面板重新启动监控。</div>'
    elif m == "login_paused":
        msg = '<div class="msg ok">已自动暂停监控（扫码登录需要独占浏览器档案），并打开扫码窗口。扫码成功后回到本页点「启动监控」恢复。</div>'
    elif m == "probe":
        msg = '<div class="msg ok">已开始检测登录态：会弹出该账号的浏览器窗口，几秒后本页「登录态 / 实际视频号」一列会显示实测结果（刷新即可看到）。探测期间该账号监控暂停 10 分钟。</div>'
    elif m == "channel":
        msg = '<div class="msg ok">已更新该账号的浏览器内核。下一个新打开的窗口（扫码登录 / 打开发表页 / 监控）就会用新内核；正在运行的窗口不受影响。</div>'
    elif m and m.startswith("err:"):
        msg = f'<div class="msg bad">添加失败：{m[4:]}</div>'
    return ACCOUNTS_TPL.replace("{rows}", rows).replace("{msg}", msg)


@app.route("/accounts/new", methods=["POST"])
def accounts_new():
    data = request.form.to_dict()
    try:
        acc = accounts.add_account(
            {
                "id": data.get("id", "").strip(),
                "name": data.get("name", "").strip(),
                "profile_dir": data.get("profile_dir", "").strip(),
                "channel": (data.get("channel", "") or "").strip().lower(),
                "enabled": data.get("enabled", "true").lower() == "true",
            }
        )
        # 立即创建浏览器档案目录，确保“扫码登录”可用
        try:
            accounts.profile_path(acc)
        except Exception:
            pass
        return redirect(url_for("accounts_page", msg="ok"))
    except ValueError as e:
        return redirect(url_for("accounts_page", msg="err:" + str(e)))


@app.route("/accounts/<aid>/login")
def accounts_login(aid):
    if not accounts.get_account(aid):
        return redirect(url_for("accounts_page"))
    # 扫码登录需要独占该账号的浏览器档案：
    # 给该账号打「手动操作暂停」标记，正在进行的发布任务会看到标记并避开它。
    # （发布侧 acquire_account 会检查暂停窗口，见 proclock）
    proclock.pause(aid, reason="扫码登录")
    _spawn_open_publish(aid, login=True)
    return redirect(url_for("accounts_page", msg="login"))


@app.route("/accounts/<aid>/probe")
def accounts_probe(aid):
    """无头实测该账号档案的登录态，并读取「实际登录的视频号昵称」写回面板。

    多账号的头号事故是两个账号登成了同一个视频号，这一步就是用来一眼看出这点的。
    """
    acc = accounts.get_account(aid)
    if not acc:
        return redirect(url_for("accounts_page"))
    try:
        res = loginprobe.probe_account(acc)
    except Exception as e:
        return redirect(url_for("accounts_page", msg="err:" + str(e)[:60]))
    tip = {
        "ok": "已登录：" + (res.get("nickname") or "（读不到昵称）"),
        "bad": "未登录，请扫码",
    }.get("ok" if res.get("logged_in") else "bad", res.get("detail", ""))
    return redirect(url_for("accounts_page", msg="probe:" + tip))


@app.route("/accounts/<aid>/resume")
def accounts_resume(aid):
    proclock.resume(aid)
    return redirect(url_for("accounts_page"))


@app.route("/accounts/<aid>/channel", methods=["POST"])
def accounts_set_channel(aid):
    """设置该账号使用的浏览器内核（让不同账号用不同浏览器，可同时打开使用）。"""
    if not accounts.get_account(aid):
        return redirect(url_for("accounts_page"))
    ch = (request.form.get("channel") or "").strip().lower()
    if ch not in accounts.VALID_CHANNELS:
        ch = ""
    accounts.update_account(aid, {"channel": ch})
    return redirect(url_for("accounts_page", msg="channel"))


@app.route("/accounts/<aid>/set_current")
def accounts_set_current(aid):
    if accounts.get_account(aid):
        accounts.set_current_account(aid)
    return redirect(url_for("accounts_page"))


@app.route("/accounts/<aid>/delete")
def accounts_delete(aid):
    accounts.delete_account(aid)
    return redirect(url_for("accounts_page"))


# ----------------------------------------------------------------------------
# 同步发布
# ----------------------------------------------------------------------------
@app.route("/publish")
def publish_page():
    acc_id = accounts.current_account_id()
    valid = [m for m in materials.load_materials(acc_id) if materials.video_exists(m) is True]
    pre = request.args.get("material", "")
    mat = materials.get_material(pre) if pre else None
    if not mat:
        mat = valid[0] if valid else None
    opts = "".join(
        f'<option value="{m["id"]}"{" selected" if mat and m["id"] == mat["id"] else ""}>{m["title"]}（{m.get("video_path","")}）</option>'
        for m in reversed(valid)
    ) or '<option value="">（当前账号还没有可用素材，先回面板新建）</option>'

    # 左侧预览（仿官方发布窗口）
    cover = (mat.get("cover_path") or "").strip() if mat else ""
    video = (mat.get("video_path") or "").strip() if mat else ""
    preview_thumb = _thumb_html(cover, video)
    preview_title = (mat.get("short_title") or mat.get("title") or "（未选择素材）") if mat else "（未选择素材）"
    preview_desc = (materials.copy_text(mat) if mat else "") or "（暂无文案）"

    # 表单预填（素材自身配置；发布时未改动的字段自动沿用）
    short_title = (mat.get("short_title") or "") if mat else ""
    desc = (mat.get("desc") or "") if mat else ""
    tags = (mat.get("tags") or "") if mat else ""
    cur_ai = (mat.get("ai_declare") or "") if mat else ""
    ai_opts = "".join(
        f'<option value="{v}"{" selected" if v == cur_ai else ""}>{v or "不标注（非 AI 内容）"}</option>'
        for v in materials.AI_DECLARE_OPTIONS if v != "非AI内容"
    )
    cur_vis = (mat.get("visibility") or "公开") if mat else "公开"
    vis_opts = "".join(
        f'<option{" selected" if v == cur_vis else ""}>{v}</option>' for v in materials.VISIBILITY_OPTIONS
    )
    # 声明原创：默认勾选（素材里没明确关掉就勾上）
    orig_checked = "checked" if (not mat or mat.get("original", True)) else ""
    dl_checked = "checked" if (mat and mat.get("allow_download")) else ""
    cm_checked = "checked" if (not mat or mat.get("allow_comment", True)) else ""
    scheduled_at = ((mat.get("scheduled_at") or "").replace(" ", "T")[:16]) if mat else ""
    ext_link = (mat.get("ext_link") or "") if mat else ""

    boxes = "".join(
        f'<label title="{login_state.status(accounts.profile_path(a))["detail"]}">'
        f'<input type="checkbox" name="accounts" value="{a["id"]}"{" checked" if a["id"] == acc_id else ""}>'
        f'{a["name"]}（{a["id"]}）{_login_badge(a["id"])}</label>'
        for a in accounts.load_accounts()
        if a.get("enabled", True)
    )
    # 视频合规预检（ffprobe）
    probe_html = ""
    if mat and not (mat.get("video_path") or "").strip():
        probe_html = '<div class="probe bad">⚠️ 该素材还没绑定视频文件，请点「编辑」上传视频或填写本地路径。</div>'
    elif mat and materials.video_exists(mat) is not True:
        probe_html = f'<div class="probe bad">⚠️ 视频文件不存在或不可读：{mat.get("video_path","")}（发布前请先修正路径）</div>'
    elif mat and (mat.get("video_path") or "").strip():
        info = media_probe.probe(mat["video_path"])
        if info:
            res = f'{info.get("width") or "?"}×{info.get("height") or "?"}'
            dur_s = info.get("duration") or 0
            dur = f'{int(dur_s//60)}分{int(dur_s%60)}秒' if dur_s else "—"
            warns = media_probe.check_compliance(info)
            cls = "bad" if warns else "ok"
            probe_html = (
                f'<div class="probe {cls}">视频：{res} · {dur} · {info.get("size_mb")}MB'
                + ("".join(f'<div>⚠️ {w}</div>' for w in warns) if warns else '<div>✅ 符合平台规格（≥720p / ≤4GB / ≤2小时）</div>')
                + "</div>"
            )
    job = pubmod.read_job()
    job_running = "false"
    if job:
        rows = "".join(
            f'<tr><td>{a["name"]}</td><td>{a["status"]}</td><td class="muted">{a["result"][:120]}</td>'
            f'<td>{"<a class=\'mini\' href=\'/publish/retry/" + job.get("material_id", "") + "/" + a["id"] + "\'>重发</a>" if "失败" in a["status"] else ""}</td></tr>'
            for a in job.get("accounts", [])
        )
        job_running = "true" if job.get("running") else "false"
        cfg = job.get("config") or {}
        cfg_s = " ｜ ".join(
            f"{k}：{v}" for k, v in cfg.items() if v
        )
        job_html = (
            f'<p>素材：<b>{job.get("material_title","")}</b> ｜ 状态：{"进行中" if job.get("running") else "已结束"}'
            f' ｜ 开始：{job.get("started_at","")} ｜ 结束：{job.get("finished_at","") or "—"}</p>'
            + (f'<p class="muted">发布配置：{cfg_s}</p>' if cfg_s else "")
            + f"<table><tr><th>账号</th><th>状态</th><th>结果</th><th>操作</th></tr>{rows}</table>"
        )
        if job.get("summary"):
            job_html += f'<p class="muted">{job["summary"]}</p>'
    else:
        job_html = '<p class="muted">还没有发布任务。</p>'
    # 封面上传（独立表单，放在左侧预览下方；不放进发布表单里避免嵌套 form）
    cover_upload = ""
    if mat:
        cover_upload = (
            f'<form method="post" action="/materials/{mat["id"]}/cover" enctype="multipart/form-data"'
            ' onsubmit="return coverReady()">'
            '<label>封面上传 <span class="muted">（保存到该素材，发布时自动使用）</span></label>'
            '<div class="drop" id="pvc_drop">点击选择 或 拖拽图片到这里</div>'
            '<input type="file" name="cover_file" id="pvc_file" accept="image/*" style="display:none">'
            '<div class="thumbs" id="pvc_prev"></div>'
            '<input type="hidden" name="next" value="publish">'
            '<button class="btn ghost" type="submit" style="margin-top:8px;width:100%">上传封面</button>'
            "</form>"
        )
    mat_name = (mat.get("short_title") or mat.get("title") or video) if mat else ""
    return PUBLISH_TPL.format(
        material_options=opts, account_checkboxes=boxes, job_status=job_html,
        preview_thumb=preview_thumb, preview_title=preview_title, preview_desc=preview_desc,
        short_title=short_title, desc=desc, tags=tags,
        ai_opts=ai_opts, vis_opts=vis_opts,
        orig_checked=orig_checked, dl_checked=dl_checked, cm_checked=cm_checked,
        scheduled_at=scheduled_at, ext_link=ext_link, probe_html=probe_html,
        job_running=job_running, cover_upload=cover_upload,
        mat_name_json=_json_literal(mat_name),
    ).replace("{form_js}", FORM_JS)


def _json_literal(v) -> str:
    """把字符串安全嵌进页面内联 JS（JSON 化并转义 </ 防止提前闭合 script）。"""
    import json as _json

    return _json.dumps(v or "", ensure_ascii=False).replace("</", "<\\/")


def _job_status_html() -> str:
    """发布任务状态片段（供页面首屏与 /publish/status 自动刷新复用）。"""
    job = pubmod.read_job()
    if not job:
        return '<p class="muted">还没有发布任务。</p>'
    rows = "".join(
        f'<tr><td>{a["name"]}</td><td>{a["status"]}</td><td class="muted">{a["result"][:120]}</td>'
        f'<td>{"<a class=\'mini\' href=\'/publish/retry/" + job.get("material_id", "") + "/" + a["id"] + "\'>重发</a>" if "失败" in a["status"] else ""}</td></tr>'
        for a in job.get("accounts", [])
    )
    cfg = job.get("config") or {}
    cfg_s = " ｜ ".join(f"{k}：{v}" for k, v in cfg.items() if v)
    html = (
        f'<p>素材：<b>{job.get("material_title","")}</b> ｜ 状态：{"进行中" if job.get("running") else "已结束"}'
        f' ｜ 开始：{job.get("started_at","")} ｜ 结束：{job.get("finished_at","") or "—"}</p>'
        + (f'<p class="muted">发布配置：{cfg_s}</p>' if cfg_s else "")
        + f"<table><tr><th>账号</th><th>状态</th><th>结果</th><th>操作</th></tr>{rows}</table>"
    )
    if job.get("summary"):
        html += f'<p class="muted">{job["summary"]}</p>'
    return html


@app.route("/publish/status")
def publish_status():
    """发布任务状态 JSON（供发布页 5 秒自动刷新）。"""
    import json as _json

    job = pubmod.read_job() or {}
    return app.response_class(
        _json.dumps({"running": bool(job.get("running")), "html": _job_status_html()}, ensure_ascii=False),
        mimetype="application/json",
    )


@app.route("/publish/retry/<mid>/<aid>")
def publish_retry(mid, aid):
    """对失败账号单独重发（沿用上次的发布配置）。"""
    mat = materials.get_material(mid)
    acc = accounts.get_account(aid)
    if not mat or not acc:
        return redirect(url_for("publish_page"))
    job = pubmod.read_job() or {}
    ov = {
        "short_title": mat.get("short_title", ""),
        "desc": mat.get("desc", ""),
        "tags": mat.get("tags", ""),
        "ai_declare": mat.get("ai_declare", ""),
        "original": "true" if mat.get("original") else "",
        "visibility": mat.get("visibility", "公开"),
        "scheduled_at": mat.get("scheduled_at", ""),
        "ext_link": mat.get("ext_link", ""),
    }
    pubmod.start_publish_job(mid, [aid], overrides=ov)
    return redirect(url_for("publish_page"))


@app.route("/publish/start", methods=["POST"])
def publish_start():
    mid = request.form.get("material_id", "")
    aids = request.form.getlist("accounts")
    overrides = {
        "short_title": request.form.get("short_title", "").strip(),
        "desc": request.form.get("desc", "").strip(),
        "tags": request.form.get("tags", "").strip(),
        "ai_declare": request.form.get("ai_declare", ""),
        "original": request.form.get("original", ""),
        "visibility": request.form.get("visibility", "公开"),
        "allow_download": request.form.get("allow_download", ""),
        "allow_comment": request.form.get("allow_comment", ""),
        "scheduled_at": request.form.get("scheduled_at", "").strip(),
        "ext_link": request.form.get("ext_link", "").strip(),
    }
    # 发布前校验：短标题少于 6 字平台会直接发布失败
    st = (overrides.get("short_title") or "").strip()
    if st and len(st) < 6:
        return '<script>alert("短标题少于 6 个字，平台会拒绝发布，请补齐后再发（或留空）");location.href="/publish"</script>'

    # 发布前校验：未登录账号直接拦下（避免登录后才发现要扫码）
    not_logged = []
    for aid in aids:
        acc = accounts.get_account(aid)
        if acc and not login_state.status(accounts.profile_path(acc))["fresh"]:
            not_logged.append(acc["name"])
    if not_logged:
        names = "、".join(not_logged)
        return (
            '<script>alert("以下账号登录态可能已失效，请先扫码登录再发布：'
            + names
            + '\\n（视频号登录态最多维持 24 小时）");location.href="/accounts"</script>'
        )

    # 发布配置同步回素材（下次发布默认沿用，也作为素材状态记录）
    materials.update_material(mid, overrides)
    try:
        pubmod.start_publish_job(mid, aids, overrides=overrides)
    except ValueError as e:
        return f'<script>alert("{e}");location.href="/publish"</script>'
    return redirect(url_for("publish_page"))


# ----------------------------------------------------------------------------
# 固定规则自动回复
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# 公众号管理（官方 API）
# ----------------------------------------------------------------------------
@app.route("/mp")
def mp_page():
    cfg = mpmod.load_config()
    bound = mpmod.is_bound()
    msg = ""
    if request.args.get("msg"):
        cls = "ok" if request.args.get("ok") == "1" else "bad"
        msg = f'<div class="msg {cls}">{request.args.get("msg")}</div>'
    if not bound:
        return MP_TPL.format(
            bound='<span class="muted">（未绑定）</span>', appid=cfg.get("appid", ""),
            secret="", msg=msg, content="",
        )
    followers = mpmod.follower_count()
    drafts = mpmod.list_drafts()
    draft_rows = "".join(
        f'<tr><td>{d["title"]}</td><td class="muted">{d["update_time"]}</td>'
        f'<td><a class="btn" href="/mp/draft/{d["media_id"]}/publish" '
        f'onclick="return confirm(\'确认发布这篇图文？\')">发布</a></td></tr>'
        for d in drafts
    ) or '<tr><td colspan="3" class="muted">草稿箱为空。</td></tr>'
    content = MP_CONTENT.format(
        followers=followers if followers is not None else "获取失败（检查 IP 白名单）",
        draft_rows=draft_rows,
    )
    return MP_TPL.format(
        bound='<span style="color:#22c55e">✔ 已绑定</span>', appid=cfg.get("appid", ""),
        secret=cfg.get("secret", ""), msg=msg, content=content,
    )


@app.route("/mp/bind", methods=["POST"])
def mp_bind():
    mpmod.save_config(request.form.get("appid", ""), request.form.get("secret", ""))
    try:
        mpmod.get_access_token(force=True)
        return redirect("/mp?ok=1&msg=绑定成功，access_token 获取正常")
    except Exception as e:
        return redirect(f"/mp?ok=0&msg=绑定已保存，但获取 token 失败：{str(e)[:150]}")


@app.route("/mp/draft/new", methods=["POST"])
def mp_draft_new():
    try:
        mpmod.add_draft(
            title=request.form.get("title", ""),
            content=request.form.get("content", ""),
            cover_path=request.form.get("cover_path", "").strip(),
            author=request.form.get("author", ""),
            digest=request.form.get("digest", ""),
        )
        return redirect("/mp?ok=1&msg=草稿已保存到公众号草稿箱")
    except Exception as e:
        return redirect(f"/mp?ok=0&msg=保存草稿失败：{str(e)[:150]}")


@app.route("/mp/draft/<path:mid>/publish")
def mp_draft_publish(mid):
    try:
        mpmod.publish_draft(mid)
        return redirect("/mp?ok=1&msg=已提交发布，可在公众号后台「发表记录」查看")
    except Exception as e:
        return redirect(f"/mp?ok=0&msg=发布失败：{str(e)[:150]}")


if __name__ == "__main__":
    print("[INFO] 管理面板已启动： http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
