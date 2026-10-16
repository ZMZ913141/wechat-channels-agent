/* 缩略图懒加载 —— 素材库的性能关键
 *
 * 问题：卡片里的 <video preload="metadata"> 即使只取元数据也是一次 HTTP 请求。
 * 一次渲染 60 张卡 = 60 并发请求，1000 条素材会直接把浏览器打死。
 *
 * 方案：IntersectionObserver 在卡片进入视口时才把 data-src 填进 src。
 * 首屏通常只请求 6-10 个，滚到哪加载到哪。
 *
 * 兼容：浏览器不支持 IntersectionObserver 时退化为「立即全部加载」，
 * 也就是旧行为——功能不会坏，只是慢。
 */
(function () {
  'use strict';

  function loadOne(el) {
    var src = el.getAttribute('data-src');
    if (!src) return;
    el.setAttribute('src', src);
    el.removeAttribute('data-src');
  }

  function loadIn(root) {
    var nodes = (root || document).querySelectorAll('[data-src]');
    for (var i = 0; i < nodes.length; i++) loadOne(nodes[i]);
  }

  function init() {
    if (!('IntersectionObserver' in window)) {
      // 老浏览器：直接全量加载，功能不受影响
      loadIn();
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        loadOne(e.target);
        io.unobserve(e.target);      // 加载一次就停止观察
      });
    }, {
      // 提前 300px 开始加载，滚动时不会出现空白
      rootMargin: '300px 0px',
      threshold: 0.01
    });

    var nodes = document.querySelectorAll('[data-src]');
    for (var i = 0; i < nodes.length; i++) io.observe(nodes[i]);

    // 视频卡片：加载后如果浏览器没渲染出首帧，退回占位（避免空白黑块）
    setTimeout(function () {
      var vids = document.querySelectorAll('video[data-kind="video"]');
      for (var i = 0; i < vids.length; i++) {
        (function (v) {
          v.addEventListener('loadeddata', function () {
            try {
              if (v.videoWidth === 0) v.parentNode.classList.add('noimg');
            } catch (e) { /* 跨域读不到就跳过 */ }
          }, { once: true });
          // 兜底：2 秒后还没画面就当无效
          setTimeout(function () {
            try {
              if (v.readyState < 1) v.parentNode.classList.add('noimg');
            } catch (e) { }
          }, 2000);
        })(vids[i]);
      }
    }, 300);
  }

  window.lazyLoadThumbs = loadIn;
  document.addEventListener('DOMContentLoaded', init);
})();