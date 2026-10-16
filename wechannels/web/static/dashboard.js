/* 数据看板：ECharts 图表（本地静态文件，无 CDN 依赖）
 *
 * 三个设计原则：
 *  1. 账号颜色由服务端按账号顺序固定分配，绝不按数据动态生成——
 *     颜色是账号的持久标识，用户靠它建立"这个蓝的就是号 A"的认知。
 *  2. 状态色（绿/红/黄）与账号色分离，二者绝不混用。
 *  3. 无数据时不初始化图表，避免空图报错。
 */
(function () {
  'use strict';

  var el = document.getElementById('dashData');
  if (!el) return;
  var D;
  try {
    D = JSON.parse(el.textContent);
  } catch (e) {
    console.error('看板数据解析失败', e);
    return;
  }

  // 主题色：跟随页面的明暗色切换
  function theme() {
    var dark = document.documentElement.getAttribute('data-theme') !== 'light';
    return {
      fg: dark ? '#e6e9ef' : '#1f2933',
      mut: dark ? '#9aa3b2' : '#5b6675',
      line: dark ? '#262b36' : '#dee2e6',
      bg: 'transparent'
    };
  }
  var T = theme();

  var charts = [];
  function mk(id, option) {
    var box = document.getElementById(id);
    if (!box || typeof echarts === 'undefined') return;
    var c = echarts.init(box, null, { renderer: 'canvas' });
    c.setOption(option);
    charts.push(c);
    return c;
  }

  function base() {
    return {
      backgroundColor: 'transparent',
      textStyle: { color: T.fg, fontFamily: 'system-ui,"Microsoft YaHei",sans-serif' },
      tooltip: {
        trigger: 'axis',
        backgroundColor: T.bg === 'transparent' ? (document.documentElement.getAttribute('data-theme') === 'light' ? '#fff' : '#1f232b') : '#1f232b',
        borderColor: T.line,
        textStyle: { color: T.fg, fontSize: 12 }
      }
    };
  }

  // 图例/系列名用**实测昵称**（辨识度最高），并在未确认时加标记。
  // 颜色仍是账号固定色；状态另用 t-* 类表达，二者不混用。
  var acctSeries = D.accounts.map(function (a) {
    var mark = a.verified ? '' : (a.tone === 'bad' ? ' ⚠未登录' : ' ⚠未检测');
    return { name: a.name + mark, real: a.name, color: a.color, id: a.account_id };
  });

  /* ---------- ① 发布趋势：堆叠柱（账号）+ 成功率折线 ---------- */
  function trendChart() {
    var days = D.trend.map(function (t) { return t.day.slice(5); });   // MM-DD
    var bars = acctSeries.map(function (a) {
      return {
        name: a.name,
        type: 'bar',
        stack: 'total',
        itemStyle: { color: a.color },
        emphasis: { focus: 'series' },
        data: D.trend.map(function (t) { return t.by_acc[a.id] || 0; })
      };
    });
    var line = {
      name: '成功率',
      type: 'line',
      yAxisIndex: 1,
      smooth: true,
      symbolSize: 5,
      itemStyle: { color: '#22c55e' },
      lineStyle: { width: 2, color: '#22c55e' },
      data: D.trend.map(function (t) { return t.success_rate; })
    };
    var o = base();
    o.legend = { top: 0, textStyle: { color: T.mut, fontSize: 11 }, itemWidth: 12, itemHeight: 8 };
    o.grid = { left: 42, right: 44, top: 34, bottom: 26 };
    o.xAxis = { type: 'category', data: days, axisLine: { lineStyle: { color: T.line } }, axisLabel: { color: T.mut, fontSize: 10 } };
    o.yAxis = [
      { type: 'value', name: '条', nameTextStyle: { color: T.mut, fontSize: 10 },
        splitLine: { lineStyle: { color: T.line, type: 'dashed' } }, axisLabel: { color: T.mut, fontSize: 10 } },
      { type: 'value', name: '%', min: 0, max: 100, nameTextStyle: { color: T.mut, fontSize: 10 },
        splitLine: { show: false }, axisLabel: { color: T.mut, fontSize: 10, formatter: '{value}%' } }
    ];
    o.series = bars.concat([line]);
    mk('chartTrend', o);
  }

  /* ---------- ② 时段分布：分组柱 ---------- */
  function hourChart() {
    var o = base();
    o.legend = { top: 0, textStyle: { color: T.mut, fontSize: 11 }, itemWidth: 12, itemHeight: 8 };
    o.grid = { left: 38, right: 16, top: 34, bottom: 26 };
    o.xAxis = {
      type: 'category',
      data: D.hour_dist.map(function (h) { return h.hour + '时'; }),
      axisLine: { lineStyle: { color: T.line } },
      axisLabel: { color: T.mut, fontSize: 9, interval: 1 }
    };
    o.yAxis = { type: 'value', splitLine: { lineStyle: { color: T.line, type: 'dashed' } }, axisLabel: { color: T.mut, fontSize: 10 } };
    o.series = [
      { name: '成功+其他', type: 'bar', stack: 'h', itemStyle: { color: '#3b82f6' },
        data: D.hour_dist.map(function (h) { return h.total - h.failed; }) },
      { name: '失败', type: 'bar', stack: 'h', itemStyle: { color: '#ef4444' },
        data: D.hour_dist.map(function (h) { return h.failed; }) }
    ];
    mk('chartHour', o);
  }

  /* ---------- ③ 失败原因：横向堆叠柱（按账号）---------- */
  function errChart() {
    var labels = D.error_dist.map(function (e) { return e.label; });
    var o = base();
    o.tooltip.trigger = 'axis';
    o.tooltip.axisPointer = { type: 'shadow' };
    o.legend = { top: 0, textStyle: { color: T.mut, fontSize: 11 }, itemWidth: 12, itemHeight: 8 };
    o.grid = { left: 8, right: 30, top: 34, bottom: 8, containLabel: true };
    o.xAxis = { type: 'value', splitLine: { lineStyle: { color: T.line, type: 'dashed' } }, axisLabel: { color: T.mut, fontSize: 10 } };
    o.yAxis = {
      type: 'category', data: labels.slice().reverse(),
      axisLine: { lineStyle: { color: T.line } },
      axisLabel: { color: T.mut, fontSize: 10 }
    };
    o.series = acctSeries.map(function (a) {
      return {
        name: a.name, type: 'bar', stack: 'e',
        itemStyle: { color: a.color },
        emphasis: { focus: 'series' },
        data: D.error_dist.map(function (e) { return e.by_acc[a.id] || 0; }).reverse()
      };
    });
    mk('chartErr', o);
  }

  /* ---------- ④ 一稿多发覆盖率：横向柱 ---------- */
  function coverChart() {
    var items = D.coverage.slice(0, 8);
    var o = base();
    o.tooltip.trigger = 'axis';
    o.tooltip.axisPointer = { type: 'shadow' };
    o.grid = { left: 8, right: 40, top: 10, bottom: 8, containLabel: true };
    o.xAxis = { type: 'value', max: acctSeries.length, minInterval: 1,
      splitLine: { lineStyle: { color: T.line, type: 'dashed' } }, axisLabel: { color: T.mut, fontSize: 10 } };
    o.yAxis = {
      type: 'category',
      data: items.map(function (c) { return c.title || c.material_id; }).slice().reverse(),
      axisLine: { lineStyle: { color: T.line } },
      axisLabel: { color: T.mut, fontSize: 10, width: 120, overflow: 'truncate' }
    };
    o.series = [{
      type: 'bar', barWidth: '55%',
      itemStyle: {
        color: function (p) { return acctSeries[p.dataIndex % acctSeries.length].color; }
      },
      label: { show: true, position: 'right', color: T.mut, fontSize: 10, formatter: '{c} 个号' },
      data: items.map(function (c) { return c.accounts; }).reverse()
    }];
    mk('chartCover', o);
  }

  function renderAll() {
    charts.forEach(function (c) { c.dispose(); });
    charts = [];
    T = theme();
    if (!D.has_data) return;
    try {
      trendChart();
      hourChart();
      if (D.error_dist && D.error_dist.length) errChart();
      if (D.coverage && D.coverage.length) coverChart();
    } catch (e) {
      console.error('图表渲染失败', e);
    }
  }

  document.addEventListener('DOMContentLoaded', renderAll);

  // 明细表：点击账号色块快速筛选
  document.addEventListener('click', function (e) {
    var dot = e.target.closest ? e.target.closest('.adot') : null;
    if (dot) e.stopPropagation();
  });

  // 明细表按状态过滤（前端即时，不请求后端）
  var tbl = document.querySelector('.tablewrap .tbl');
  if (tbl) {
    var head = tbl.querySelector('thead tr');
    var ths = head ? head.querySelectorAll('th') : [];
    if (ths.length > 3) {
      ths[3].style.cursor = 'pointer';
      ths[3].title = '点击按状态筛选';
      ths[3].addEventListener('click', function () {
        var want = ths[3].getAttribute('data-filter');
        var cur = ths[3].getAttribute('data-filter');
        var rows = tbl.querySelectorAll('tbody tr');
        if (cur) {
          rows.forEach(function (r) { r.style.display = ''; });
          ths[3].removeAttribute('data-filter');
          return;
        }
        want = prompt('按状态筛选：输入 success / failed / unverified（留空=全部）');
        if (!want) return;
        rows.forEach(function (r) {
          var pill = r.querySelector('.pill');
          var txt = pill ? pill.textContent.trim() : '';
          var ok = (want === 'success' && txt === '成功')
                || (want === 'failed' && txt === '失败')
                || (want === 'unverified' && txt === '未确认');
          r.style.display = ok ? '' : 'none';
        });
        ths[3].setAttribute('data-filter', want);
      });
    }
  }
})();