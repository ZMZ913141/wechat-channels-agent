/* 素材表单：拖拽上传、AI 智能填写 */
(function(){try{var t=localStorage.getItem('theme')||'dark';document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
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
