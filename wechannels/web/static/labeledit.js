/* 卡片内就地编辑分类标签
 *
 * 用户反馈：标签只能进编辑页才能改，太麻烦。
 * 现在直接在卡片上加/删，不用离开当前视图。
 *
 * 用表单 POST 而不是 fetch：标签增删是低频操作，
 * 走表单最简单可靠（刷新后状态一致，也天然支持浏览器后退）。
 * 同标签用 POST 会被浏览器的"重发确认"拦，
 * 所以连续加多个标签时用一个隐藏 iframe 提交，避免打断。
 */
(function () {
  'use strict';

  var iframe = null;

  function postSilent(url, data) {
    // 复用同一个 iframe，避免反复创建
    if (!iframe) {
      iframe = document.createElement('iframe');
      iframe.style.display = 'none';
      iframe.name = 'lp_frame';
      document.body.appendChild(iframe);
    }
    var f = iframe.contentWindow.document;
    f.open();
    f.write('<form method="post" action="' + url + '" target="_self"></form>');
    var form = f.forms[0];
    Object.keys(data).forEach(function (k) {
      var i = document.createElement('input');
      i.type = 'hidden';
      i.name = k;
      i.value = data[k];
      form.appendChild(i);
    });
    form.submit();
  }

  /* ---------- 添加标签 ---------- */
  window.addLabel = function (mid, btn) {
    // 从已有标签里挑最省事：没有就手输
    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'lchip-input';
    input.placeholder = '标签名，回车确认';
    input.maxLength = 12;
    input.style.cssText =
      'width:110px;font-size:11px;padding:1px 6px;border-radius:20px;' +
      'border:1px solid var(--acc);background:var(--bg);color:var(--fg)';
    if (btn && btn.parentNode) btn.parentNode.insertBefore(input, btn);
    input.focus();

    var done = false;
    function commit() {
      if (done) return;
      done = true;
      var v = (input.value || '').trim();
      if (v) postSilent('/materials/' + mid + '/label', { op: 'add', label: v });
      else location.reload();
    }
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { e.preventDefault(); done = true; location.reload(); }
    });
    input.addEventListener('blur', function () { setTimeout(commit, 120); });
  };

  /* ---------- 移除标签 ---------- */
  window.rmLabel = function (mid, label) {
    if (!confirm('移除标签「' + label + '」？\n\n只影响这条素材的分类标签，不会删除素材本身。')) {
      return;
    }
    postSilent('/materials/' + mid + '/label', { op: 'remove', label: label });
    // 服务端处理后静默刷新，让新状态可见
    setTimeout(function () { location.reload(); }, 700);
  };
})();