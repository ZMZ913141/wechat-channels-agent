/* 主题切换 —— 所有页面共用。
   暗色是默认（"夜视版"），亮色通过 <html data-theme="light"> 切换。
   读取放在 head 内联脚本里执行，避免首屏闪白；切换逻辑放这里复用。 */
(function () {
  'use strict';
  window.toggleTheme = function () {
    var cur = document.documentElement.getAttribute('data-theme');
    var next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) { /* 隐私模式下忽略 */ }
  };

  /* 同步所有页面上的主题按钮文案 */
  document.addEventListener('DOMContentLoaded', function () {
    syncThemeBtn();
  });

  function syncThemeBtn() {
    var isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    var btns = document.querySelectorAll('.theme-btn');
    for (var i = 0; i < btns.length; i++) {
      btns[i].textContent = isDark ? '🌙 夜视' : '☀️ 日间';
    }
  }
  window.syncThemeBtn = syncThemeBtn;
})();