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
  };

  function setPrev(el, html) {
    if (el) el.innerHTML = html;
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
        // 预览条：点某一张 → 把抓帧滑块跳到对应时间点
        // （用户直觉是"点哪张就抓哪张"，不做会让人以为预览条只是装饰）
        var pts = stripPoints(d);
        var strip = (d.frames || []).map(function (u, i) {
          return '<img src="' + u + '" alt="第' + (pts[i] || 0).toFixed(1) +
                 '秒" title="点此把抓帧位置移到 ' + (pts[i] || 0).toFixed(1) +
                 ' 秒" onclick="jumpSeek(' + (pts[i] || 0) + ')">';
        }).join('');
        setPrev(prev,
          '<div class="vp-main"><img src="' + d.main + '" alt=""></div>' +
          (strip ? '<div class="vp-strip">' + strip + '</div>' : '') +
          '<div class="vp-meta">' +
            (d.width ? d.width + '×' + d.height + ' · ' : '') +
            (state.duration ? state.duration.toFixed(1) + ' 秒 · ' : '') +
            (d.size_mb ? d.size_mb + ' MB' : '') +
          '</div>');

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
        setPrev(cp, '<img src="' + d.url + '" alt="">' +
          '<div class="vp-meta">✓ 已设为封面（第 ' +
          parseFloat(d.at).toFixed(1) + ' 秒）</div>');
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
        '" controls muted playsinline></video></div>' +
        '<div class="vp-meta">✓ ' + escapeHtml(f.name) +
        ' · ' + (f.size / 1048576).toFixed(1) + ' MB（保存后即可抓帧）</div>');
      // 已选了文件就不该再显示"抓帧"（此时服务端还没有这个文件）
      $('grabbox').style.display = 'none';
    });
  });
})();