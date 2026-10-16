/* 素材表单：拖拽上传、AI 智能填写、路径智能识别
 *
 * ⚠️ 重写原因：原文件调用了未定义的 bindDrop()，导致 JS 在第 8 行抛错中断，
 * 拖拽上传实际从未生效；同时还重复了一份主题切换逻辑（已移到 static/theme.js）。
 */
(function () {
  'use strict';

  var VIDEO_EXT = /\.(mp4|mov|m4v|webm|avi|mkv|flv|ts)$/i;
  var IMAGE_EXT = /\.(jpg|jpeg|png|gif|webp|bmp)$/i;

  /* ---------- 路径智能识别 ----------
   * 用户常把路径填进「智能填写」框（如截图里的 d:/videos/A货混搭上新.mp4）。
   * 若不识别，AI 会把整条路径当标题，生成一堆「d:/videos/...」的垃圾文案。
   * 这里主动识别：像路径就自动回填到视频路径框，并只用文件名去生成文案。
   */
  function looksLikePath(s) {
    if (!s) return false;
    return /[\\/]/.test(s) || /^[a-zA-Z]:/.test(s) || /\.[a-zA-Z0-9]{2,5}$/.test(s.trim());
  }

  function baseName(p) {
    return String(p || '').replace(/\\/g, '/').split('/').pop();
  }

  function fillFromPathInput() {
    var src = document.getElementById('af_name');
    var vpath = document.getElementById('f_vpath');
    if (!src || !vpath) return;
    var v = (src.value || '').trim();
    if (!v || !looksLikePath(v)) return;
    // 已经在填路径了就不覆盖（用户可能手动改过）
    if (!vpath.value) {
      vpath.value = v;
      vpath.dispatchEvent(new Event('input', { bubbles: true }));
    }
    // 用文件名替换输入框内容，让 AI 基于文件名而非整条路径生成
    var base = baseName(v);
    if (base && base !== v) {
      src.value = base.replace(VIDEO_EXT, '').replace(IMAGE_EXT, '');
    }
    note('已识别为文件路径并回填：' + v);
  }

  function note(msg) {
    var n = document.getElementById('af_note');
    if (n) n.textContent = msg || '';
  }

  window.fillFromPathInput = fillFromPathInput;
  window.syncName = function (v) {
    // 反向：路径框填了内容 → 同步文件名到 AI 框（仅在 AI 框为空时）
    var el = document.getElementById('af_name');
    if (el && v && !el.value) el.value = baseName(v);
  };

  /* ---------- 拖拽上传 ---------- */
  function bindDrop(zoneId, inputId, prevId, kind) {
    var zone = document.getElementById(zoneId);
    var input = document.getElementById(inputId);
    var prev = document.getElementById(prevId);
    if (!zone || !input) return;

    zone.addEventListener('click', function () { input.click(); });

    ['dragenter', 'dragover'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault(); e.stopPropagation();
        zone.classList.add('over');
      });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault(); e.stopPropagation();
        zone.classList.remove('over');
      });
    });

    zone.addEventListener('drop', function (e) {
      var dt = e.dataTransfer;
      if (!dt || !dt.files || !dt.files.length) return;
      var re = kind === 'video' ? VIDEO_EXT : IMAGE_EXT;
      var f = null;
      for (var i = 0; i < dt.files.length; i++) {
        if (re.test(dt.files[i].name)) { f = dt.files[i]; break; }
      }
      if (!f) {
        note(kind === 'video' ? '⚠ 只支持视频文件' : '⚠ 只支持图片文件');
        return;
      }
      try {
        var dt2 = new DataTransfer();
        dt2.items.add(f);
        input.files = dt2.files;
      } catch (err) {
        note('浏览器不支持该拖拽方式，请改用「点击选择」');
        return;
      }
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    input.addEventListener('change', function () {
      var f = input.files && input.files[0];
      if (!prev) return;
      prev.innerHTML = '';
      if (!f) return;
      var url = URL.createObjectURL(f);
      var el = document.createElement(kind === 'video' ? 'video' : 'img');
      el.src = url;
      if (kind === 'video') { el.muted = true; el.playsInline = true; }
      el.onload = function () { URL.revokeObjectURL(url); };
      prev.appendChild(el);
      // 选文件时自动把文件名填进 AI 框（便于一键生成文案）
      var af = document.getElementById('af_name');
      if (af && !af.value) af.value = baseName(f.name).replace(VIDEO_EXT, '');
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindDrop('vdrop', 'vfile', 'vprev', 'video');
    bindDrop('cdrop', 'cfile', 'cprev', 'image');

    // AI 框失焦时做一次路径识别（用户粘贴路径最常见的时机）
    var af = document.getElementById('af_name');
    if (af) {
      af.addEventListener('blur', fillFromPathInput);
      af.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); fillFromPathInput(); }
      });
    }
  });

  /* ---------- AI 一键生成 ---------- */
  var AF_MAP = { title: 'f_title', short_title: 'f_short', desc: 'f_desc', tags: 'f_tags', ai: 'f_ai' };

  function afterAutofill(d) {
    var noteEl = document.getElementById('af_note');
    var parts = [];
    if (d && d.title) parts.push('标题已生成');
    if (d && d.desc) parts.push('文案已生成');
    if (d && d.tags) parts.push('话题已生成');
    if (noteEl) noteEl.textContent = parts.length ? '✨ ' + parts.join('，') : '已生成';
  }

  window.doAutofill = function () {
    fillFromPathInput();
    var name = (document.getElementById('af_name') || {}).value || '';
    var hint = (document.getElementById('af_hint') || {}).value || '';
    if (!name && !hint) { note('请先填文件名或提示'); return; }
    var btn = document.getElementById('af_btn');
    if (btn) btn.disabled = true;
    note('AI 生成中…');
    fetch('/materials/autofill?field=&base=' + encodeURIComponent(name) +
          '&hint=' + encodeURIComponent(hint))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        Object.keys(AF_MAP).forEach(function (k) {
          var el = document.getElementById(AF_MAP[k]);
          if (el && d && d[k]) el.value = d[k];
        });
        afterAutofill(d);
      })
      .catch(function () { note('AI 调用失败，可手动填写'); })
      .finally(function () { if (btn) btn.disabled = false; });
  };

  window.genField = function (field) {
    fillFromPathInput();
    var name = (document.getElementById('af_name') || {}).value || '';
    var hint = (document.getElementById('af_hint') || {}).value || '';
    if (!name) { note('先填个文件名或提示，AI 才知道生成什么'); return; }
    fetch('/materials/autofill?field=' + encodeURIComponent(field) +
          '&base=' + encodeURIComponent(name) +
          '&hint=' + encodeURIComponent(hint))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var el = document.getElementById(AF_MAP[field]);
        if (el && d && d.value) { el.value = d.value; note('✨ 已生成'); }
        else note('AI 未返回内容，可重试或手写');
      })
      .catch(function () { note('AI 调用失败'); });
  };
})();