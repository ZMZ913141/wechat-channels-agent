/* 目录导入页：勾选统计与全选/全不选 */
(function () {
  'use strict';

  function boxes() {
    return document.querySelectorAll('input[name="files"]');
  }

  function updateBar() {
    var all = boxes();
    var n = 0;
    for (var i = 0; i < all.length; i++) {
      if (all[i].checked) n++;
    }
    var counter = document.getElementById('counter');
    if (counter) counter.textContent = '已选 ' + n + ' / ' + all.length;
  }

  function toggleAll(force) {
    var all = boxes();
    // 已全选时再次点击=取消全选
    var allChecked = true;
    for (var i = 0; i < all.length; i++) {
      if (!all[i].checked) { allChecked = false; break; }
    }
    var target = (typeof force === 'boolean') ? force : !allChecked;
    for (var j = 0; j < all.length; j++) all[j].checked = target;
    updateBar();
  }

  window.updateBar = updateBar;
  window.toggleAll = toggleAll;

  document.addEventListener('DOMContentLoaded', function () {
    updateBar();
  });
})();