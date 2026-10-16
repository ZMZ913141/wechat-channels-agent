/* 首页素材库的交互逻辑 */
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
