/* 素材表单：视频画面预览 + 选帧做封面
 *
 * 用户反馈演进：
 *   1) 视频区只有路径文字，看不到画面      → 服务端抽帧预览
 *   2) 封面只能自己截屏上传                → 预览条点帧直接抓帧
 *   3) 竖版视频两侧留大片空白              → 按真实宽高比收窄容器
 *   4) 可选帧太少、只想要前 2-4 秒的帧     → 时间窗 + 可变帧数
 *   5) 选中帧后大图不刷新，无法确认对不对   → 点帧即换大图（滑动即所见）
 *
 * 设计要点：
 *  - 抽帧时间点由后端返回（/media/thumbs 的 times 字段），
 *    前端不再复算取点规则——两份实现必然漂移，点帧就会跳错秒数
 *  - 大图与"待抓帧画面"始终一致：点帧 → 大图换成该帧 → 滑块同步
 *  - 任何一步失败都只提示、不阻断，用户仍可手动填路径
 */
(function () {
  'use strict';

  var state = {
    duration: 0,
    path: '',
    times: [],
    ranges: null,   // {dur, ranges:[{key,label,start,end,count}]}
    busy: false
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return window.escapeHtml ? window.escapeHtml(s) : s; }

  /* 点预览条某帧 → 换大图 + 移滑块。
     大图必须同步换，否则用户点完帧看不到变化，无从确认是不是要的那张。 */
  window.jumpSeek = function (t, alsoPreview) {
    var bar = $('seekBar');
    if (bar) {
      bar.value = t;
      window.seekLabel();
    }
    highlightFrame(nearestIdx(t));
    if (alsoPreview !== false) showFrameAt(t);
  };

  function times() {
    var box = $('vprev');
    if (!box) return [];
    return (box.getAttribute('data-times') || '').split(',')
      .filter(function (x) { return x !== ''; }).map(parseFloat);
  }

  function nearestIdx(t) {
    var ts = times(), best = 0, bd = Infinity;
    ts.forEach(function (v, i) {
      var d = Math.abs(v - t);
      if (d < bd) { bd = d; best = i; }
    });
    return ts.length ? best : -1;
  }

  function setPrev(el, html) {
    if (el) el.innerHTML = html;
  }

  /* 竖版视频（高>宽）要按真实宽高比收窄容器，
     否则宽容器里只放中间一条竖片，两侧全是刺眼的空白（用户截图标注的问题） */
  function markOrientation(el, w, h) {
    if (!el) return;
    el.classList.remove('vp-portrait');
    if (w > 0 && h > 0) {
      el.style.setProperty('--vp-ratio', (w / h).toFixed(4));
      if (h > w) el.classList.add('vp-portrait');
    }
  }

  function highlightFrame(idx) {
    var box = $('vprev');
    if (!box) return;
    // 每帧现在包在 .vpfr 里（带秒数标签），高亮整格而不是只高亮 img
    var cells = box.querySelectorAll('.vp-strip .vpfr');
    if (!cells.length) return;
    for (var i = 0; i < cells.length; i++) {
      if (i === idx) cells[i].classList.add('on');
      else cells[i].classList.remove('on');
    }
  }

  /* ---------- 把大图换成指定时间点的那一帧 ---------- */
  function showFrameAt(t) {
    var main = document.querySelector('#vprev .vp-main img');
    if (!main || !state.path) return;
    if (main.getAttribute('data-t') === String(t)) return;  // 已是这一帧
    main.setAttribute('data-loading', '1');
    main.style.opacity = '.45';
    fetch('/media/frame?path=' + encodeURIComponent(state.path) +
          '&t=' + encodeURIComponent(t))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) return;                 // 失败就保持原图，不清空
        main.src = d.url;
        main.setAttribute('data-t', d.at);
      })
      .catch(function () { /* 静默：保留原图 */ })
      .finally(function () {
        main.removeAttribute('data-loading');
        main.style.opacity = '';
      });
  }

  /* ---------- 时间窗档位 ----------
     用户要"前 2-4 秒"这种短窗密集帧；只给全时长均匀抽样选不到想要的画面。 */
  function buildRanges(dur) {
    var r = [{ key: 'all', label: '全片', start: null, end: null, count: 8 }];
    if (dur > 0) {
      if (dur <= 12) {
        // 短视频：直接全片密集抽帧
        r[0] = { key: 'all', label: '全片·密集', start: null, end: null, count: 12 };
      } else {
        r.push({ key: '0-2', label: '前 2 秒', start: 0, end: 2, count: 6 });
        r.push({ key: '0-4', label: '前 4 秒', start: 0, end: 4, count: 8 });
        r.push({ key: '0-6', label: '前 6 秒', start: 0, end: 6, count: 9 });
        r.push({ key: '0-10', label: '前 10 秒', start: 0, end: 10, count: 10 });
      }
    }
    return r;
  }

  function renderRangeTabs(cur) {
    var box = $('vpranges');
    if (!box) return;
    box.style.display = state.ranges && state.ranges.length > 1 ? 'flex' : 'none';
    box.innerHTML = (state.ranges || []).map(function (r) {
      return '<button type="button" class="vprange' +
        (r.key === cur ? ' on' : '') + '" data-key="' + r.key +
        '" onclick="pickRange(\'' + r.key + '\')">' + r.label + '</button>';
    }).join('');
  }

  window.pickRange = function (key) {
    var r = (state.ranges || []).filter(function (x) { return x.key === key; })[0];
    if (!r || state.busy) return;
    loadStrip(r, true);
  };

  /* 只重抽预览条，保留大图与滑块上下文（切档位不该让大图闪回原帧） */
  function loadStrip(range, keepMain) {
    if (!state.path || state.busy) return;
    state.busy = true;
    var stripBox = document.querySelector('#vprev .vp-strip');
    if (stripBox) stripBox.style.opacity = '.4';
    var q = '/media/thumbs?path=' + encodeURIComponent(state.path) +
            '&count=' + range.count +
            (range.start != null ? '&start=' + range.start : '') +
            (range.end != null ? '&end=' + range.end : '');
    fetch(q)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.busy = false;
        if (!d || !d.ok) {
          if (stripBox) stripBox.style.opacity = '';
          return;
        }
        state.times = d.times || [];
        renderStrip(stripBox, d);
        if (!keepMain) {
          var img = document.querySelector('#vprev .vp-main img');
          if (img) {
            img.src = d.main;
            img.setAttribute('data-t', d.main_t);
          }
        }
        renderRangeTabs(range.key);
        // 大图与高亮帧对齐到当前档位的第一帧
        highlightFrame(0);
        if (keepMain) {
          var bar = $('seekBar');
          if (bar && state.times.length) bar.value = state.times[0];
          window.seekLabel();
        }
      })
      .catch(function () {
        state.busy = false;
        if (stripBox) stripBox.style.opacity = '';
      });
  }

  function renderStrip(box, d) {
    if (!box) return;
    var ts = d.times || [];
    var n = (d.frames || []).length;
    box.style.opacity = '';
    // 列数交给 CSS 的 auto-fill(minmax(52px,1fr)) 自适应，
    // 前端写死列数会把竖版小卡里的帧压成看不清的细缝
    if (n > 8) box.setAttribute('data-many', '1');
    else box.removeAttribute('data-many');
    box.innerHTML = (d.frames || []).map(function (u, i) {
      var t = ts[i] != null ? ts[i] : 0;
      // 每帧标出秒数：用户要的是"前 2-4 秒里的某一帧"，
      // 光看小图猜不出是第几秒，还得能直接读出来
      return '<span class="vpfr"><img src="' + u + '" alt="第' + t.toFixed(1) +
        '秒" title="第 ' + t.toFixed(1) + ' 秒" onclick="jumpSeek(' + t +
        ')"><i>' + t.toFixed(1) + 's</i></span>';
    }).join('');
    $('vprev').setAttribute('data-times', ts.join(','));
  }

  /* ---------- 视频路径预览（服务端抽帧） ---------- */
  window.probeVideo = function (path) {
    var prev = $('vprev');
    path = (path || '').trim();
    if (!path) {
      setPrev(prev, '<div class="vp-empty">填入路径或选择文件后，这里会显示视频画面</div>');
      $('grabbox').style.display = 'none';
      state.duration = 0;
      state.path = '';
      renderRangeTabs(null);
      return;
    }
    state.path = path;
    var q = encodeURIComponent(path);
    setPrev(prev, '<div class="vp-loading">正在读取视频画面…</div>');
    fetch('/media/thumbs?path=' + q + '&count=8')
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
        state.ranges = buildRanges(state.duration);
        markOrientation(prev, d.width, d.height);
        state.times = d.times || [];
        var strip = (d.frames || []).length
          ? '<div class="vp-strip"></div>' : '';
        var meta = [];
        if (d.width) meta.push('<span>' + d.width + '×' + d.height + '</span>');
        if (state.duration) meta.push('<span>' + state.duration.toFixed(1) + ' 秒</span>');
        if (d.size_mb) meta.push('<span>' + d.size_mb + ' MB</span>');
        meta.push('<span>' + (d.height > d.width ? '竖版' : '横版') + '</span>');
        setPrev(prev,
          '<div class="vp-main"><img src="' + d.main +
            '" data-t="' + (d.main_t != null ? d.main_t : '') + '" alt=""></div>' +
          strip +
          '<div class="vp-meta">' + meta.join('') + '</div>');
        renderStrip(prev.querySelector('.vp-strip'), d);
        renderRangeTabs(state.ranges[0].key);

        // 显示抓帧工具
        var gb = $('grabbox');
        gb.style.display = 'flex';
        var bar = $('seekBar');
        if (state.duration > 0) {
          bar.max = Math.max(1, Math.round(state.duration * 10) / 10);
          bar.step = '0.1';                       // 0.1 秒精度，够挑表情
          bar.value = d.main_t != null ? d.main_t
                    : Math.round(state.duration * 0.3 * 10) / 10;
        } else {
          bar.max = 60;
          bar.step = '0.5';
          bar.value = 1;
        }
        window.seekLabel();
      })
      .catch(function () {
        setPrev(prev, '<div class="vp-err">读取失败，请检查路径是否正确</div>');
      });
  };

  /* ---------- 抓帧：滑块 ----------
     拖动时同步刷新大图：用户要能"所见即所得"地确认这一帧长什么样，
     否则拖到某个时间点只能盲抓，抓完才发现选错了。 */
  window.seekLabel = function () {
    var bar = $('seekBar');
    var v = parseFloat(bar.value) || 0;
    var mm = Math.floor(v / 60);
    var ss = (v - mm * 60).toFixed(1);
    $('seekVal').textContent = (mm > 0 ? mm + '分' : '') + ss + ' 秒';
    highlightFrame(nearestIdx(v));
    // 节流：拖动时频繁触发，用 120ms 尾随避免每像素都发请求
    if (state._pt) clearTimeout(state._pt);
    state._pt = setTimeout(function () { showFrameAt(v); }, 120);
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