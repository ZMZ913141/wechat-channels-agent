/* 新建素材表单：视频画面预览 + 从视频抓帧当封面
 *
 * 解决的问题（用户截图反馈）：
 *   原来视频区只有一行路径文字，完全看不到视频长什么样；
 *   封面区也没法直接从视频取帧，只能自己去外面截屏再上传。
 *
 * 设计要点：
 *  - 填本地路径时也要能预览 → 走服务端 /media/thumbs 抽帧接口，
 *    因为浏览器无法直接读本地磁盘文件（安全限制）
 *  - 抓帧滑块的取值范围是**视频真实时长**（ffprobe 拿），
 *    否则拖到 300 秒而视频只有 12 秒，抽出来是黑屏
 *  - 任何一步失败都只提示、不阻断——用户仍可手动填路径
 */
(function () {
  'use strict';

  var VIDEO_EXT = /\.(mp4|mov|m4v|webm|avi|mkv|flv|ts)$/i;

  var state = { duration: 0, probing: false };

  function $(id) { return document.getElementById(id); }

  /* 复算预览条各帧对应的时间点（与后端 preview_strip 的取点规则一致：
     时长 t 时取 t*(i+1)/(n+1)；时长未知时取 1+2i） */
  function stripPoints(d) {
    var n = (d.frames || []).length || 4;
    if (d.duration > 0) {
      var out = [];
      for (var i = 0; i < n; i++) out.push(d.duration * (i + 1) / (n + 1));
      return out;
    }
    var o2 = [];
    for (var j = 0; j < n; j++) o2.push(1.0 + j * 2.0);
    return o2;
  }

  /* 点预览条某帧 → 滑块跳到那个时间点 */
  window.jumpSeek = function (t) {
    var bar = $('seekBar');
    if (!bar) return;
    bar.value = t;
    seekLabel();
    // 高亮离 t 最近的那一帧，让"预览条 = 可选画面"的对应关系可见
    var box = $('vprev');
    if (!box) return;
    var times = (box.getAttribute('data-times') || '').split(',')
      .filter(function (x) { return x !== ''; })
      .map(parseFloat);
    if (!times.length) return;
    var best = 0, bd = Infinity;
    times.forEach(function (v, i) {
      var d = Math.abs(v - t);
      if (d < bd) { bd = d; best = i; }
    });
    highlightFrame(best);
  };

  function setPrev(el, html) {
    if (el) el.innerHTML = html;
  }

  /* 竖版视频（高>宽）要按真实宽高比收窄容器，
     否则宽容器里只放中间一条竖片，两侧全是刺眼的空白（用户截图标注的问题） */
  function markOrientation(el, w, h) {
    if (!el) return;
    el.classList.remove('vp-portrait');
    if (w > 0 && h > 0) {
      var r = w / h;
      el.style.setProperty('--vp-ratio', r.toFixed(4));
      if (h > w) el.classList.add('vp-portrait');
    }
  }

  /* 预览条：点某帧 → 跳到那个时间点，并高亮当前帧（反馈"我选的是哪一张"） */
  function highlightFrame(idx) {
    var box = document.getElementById('vprev');
    if (!box) return;
    var imgs = box.querySelectorAll('.vp-strip img');
    for (var i = 0; i < imgs.length; i++) {
      if (i === idx) imgs[i].classList.add('on');
      else imgs[i].classList.remove('on');
    }
  }

  /* ---------- 视频路径预览（服务端抽帧） ---------- */
  window.probeVideo = function (path) {
    var prev = $('vprev');
    path = (path || '').trim();
    if (!path) {
      setPrev(prev, '<div class="vp-empty">填入路径或选择文件后，这里会显示视频画面</div>');
      $('grabbox').style.display = 'none';
      state.duration = 0;
      return;
    }
    // Windows 路径里反斜杠要转义，否则 JSON/URL 里出问题
    var q = encodeURIComponent(path);
    setPrev(prev, '<div class="vp-loading">正在读取视频画面…</div>');
    fetch('/media/thumbs?path=' + q)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) {
          setPrev(prev, '<div class="vp-err">' +
            ((d && d.reason) ? escapeHtml(d.reason) : '无法读取该视频') +
            '<br><span class="muted">仍可继续填写，发布时会用你填的路径</span></div>');
          $('grabbox').style.display = 'none';
          return;
        }
        state.duration = d.duration || 0;
        markOrientation(prev, d.width, d.height);
        // 预览条：点某一张 → 把抓帧滑块跳到对应时间点
        // （用户直觉是"点哪张就抓哪张"，不做会让人以为预览条只是装饰）
        var pts = stripPoints(d);
        var strip = (d.frames || []).map(function (u, i) {
          var t = pts[i] || 0;
          return '<img src="' + u + '" alt="第' + t.toFixed(1) +
                 '秒" title="点此把抓帧位置移到 ' + t.toFixed(1) +
                 ' 秒" onclick="jumpSeek(' + t + ')">';
        }).join('');
        var meta = [];
        if (d.width) meta.push('<span>' + d.width + '×' + d.height + '</span>');
        if (state.duration) meta.push('<span>' + state.duration.toFixed(1) + ' 秒</span>');
        if (d.size_mb) meta.push('<span>' + d.size_mb + ' MB</span>');
        meta.push('<span>' + (d.height > d.width ? '竖版' : '横版') + '</span>');
        setPrev(prev,
          '<div class="vp-main"><img src="' + d.main + '" alt=""></div>' +
          (strip ? '<div class="vp-strip">' + strip + '</div>' : '') +
          '<div class="vp-meta">' + meta.join('') + '</div>');
        prev.setAttribute('data-times', pts.join(','));

        // 显示抓帧工具
        var gb = $('grabbox');
        gb.style.display = 'flex';
        var bar = $('seekBar');
        if (state.duration > 0) {
          bar.max = Math.max(1, Math.round(state.duration * 10) / 10);
          bar.step = '0.1';
          bar.value = Math.round(state.duration * 0.3 * 10) / 10;
        } else {
          bar.max = 60;
          bar.step = '0.5';
          bar.value = 1;
        }
        seekLabel();
      })
      .catch(function () {
        setPrev(prev, '<div class="vp-err">读取失败，请检查路径是否正确</div>');
      });
  };

  /* ---------- 抓帧：滑块 ---------- */
  window.seekLabel = function () {
    var bar = $('seekBar');
    var v = parseFloat(bar.value) || 0;
    var mm = Math.floor(v / 60);
    var ss = (v - mm * 60).toFixed(1);
    $('seekVal').textContent = (mm > 0 ? mm + '分' : '') + ss + ' 秒';
    // 拖滑块时同步高亮最近的关键帧（不跳时间，只改高亮）
    var box = $('vprev');
    if (!box) return;
    var times = (box.getAttribute('data-times') || '').split(',')
      .filter(function (x) { return x !== ''; }).map(parseFloat);
    if (!times.length) return;
    var best = 0, bd = Infinity;
    times.forEach(function (x, i) {
      var d = Math.abs(x - v);
      if (d < bd) { bd = d; best = i; }
    });
    highlightFrame(best);
  };

  /* ---------- 抓帧当封面 ---------- */
  window.grabCover = function () {
    var path = ($('f_vpath') || {}).value || '';
    path = path.trim();
    if (!path) { alert('请先填写或选择视频文件'); return; }
    var t = parseFloat(($('seekBar') || {}).value) || 0;
    var cp = $('cprev');
    var btn = document.querySelector('#grabbox .btn');
    var old = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '正在抓取…'; }
    setPrev(cp, '<div class="vp-loading">正在抓取第 ' + t + ' 秒…</div>');

    fetch('/media/grab_cover?path=' + encodeURIComponent(path) +
          '&t=' + encodeURIComponent(t))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) {
          setPrev(cp, '<div class="vp-err">' +
            escapeHtml((d && d.reason) || '抓取失败') + '</div>');
          return;
        }
        // 抓帧后直接填入封面路径（同时保留手动上传能力）
        $('f_cpath').value = d.cover;
        // 包一层 .vp-main，竖版封面才能按比例收窄、不裁切
        cp.classList.remove('vp-portrait');
        setPrev(cp, '<div class="vp-main"><img src="' + d.url + '" alt=""></div>' +
          '<div class="vp-meta"><span>✓ 已设为封面</span>' +
          '<span>第 ' + parseFloat(d.at).toFixed(1) + ' 秒</span></div>');
      })
      .catch(function () {
        setPrev(cp, '<div class="vp-err">抓取请求失败</div>');
      })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = old; }
      });
  };

  window.escapeHtml = function (s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
               '"': '&quot;', "'": '&#39;' }[c];
    });
  };

  /* ---------- 选了本地视频文件 → 立即本地预览 + 上传后刷新 ---------- */
  document.addEventListener('DOMContentLoaded', function () {
    var inp = $('vfile');
    if (!inp) return;
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0];
      if (!f) return;
      // 选文件时先本地预览（还没上传，服务端还拿不到）
      var prev = $('vprev');
      var url = URL.createObjectURL(f);
      setPrev(prev, '<div class="vp-main"><video src="' + url +
        '" controls muted playsinline style="max-height:340px"></video></div>' +
        '<div class="vp-meta"><span>✓ ' + escapeHtml(f.name) + '</span>' +
        '<span>' + (f.size / 1048576).toFixed(1) + ' MB</span>' +
        '<span>保存后即可抓帧</span></div>');
      // 已选了文件就不该再显示"抓帧"（此时服务端还没有这个文件）
      $('grabbox').style.display = 'none';
    });

    // ---- 编辑页：进页面就自动显示已有视频的画面 ----
    // 之前用户反馈"编辑页还是原来的样子"：只改内容不改路径就不会触发预览。
    // 这里在页面加载时主动读 data-autoload 抽帧，省得用户再点一次。
    var prevEl = $('vprev');
    var auto = prevEl && prevEl.getAttribute('data-autoload');
    if (auto && auto.trim() && ($('f_vpath') || {}).value === auto.trim()) {
      // 用户没改过路径 → 直接抽帧显示
      probeVideo(auto.trim());
    } else if (prevEl && !auto) {
      setPrev(prevEl, '<div class="vp-empty">这条素材还没有视频文件。' +
        '填入路径或拖一个视频进来，这里会显示画面</div>');
    }
  });
})();