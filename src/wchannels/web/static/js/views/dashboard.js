/** 数据看板：本周发布 KPI + 已发布作品的数据（每天自动采集）。 */
import { api } from '../api.js?v=3.21';
import { $, toast } from '../ui.js?v=3.21';

const PLAT = { wechat: '视频号', douyin: '抖音', xiaohongshu: '小红书' };
// 账号分组开合状态记在本地，跟评论管理同一套交互
const collapsed = new Set(JSON.parse(localStorage.getItem('dash-collapsed') || '[]'));

export async function load() {
  const root = $('#page-dashboard');
  root.innerHTML = `
    <div class="kpi-row stagger">
      <div class="card kpi"><div class="label">本周发布</div><div class="num" id="d-total">–</div></div>
      <div class="card kpi k-jade"><div class="label">成功</div><div class="num c-jade" id="d-success">–</div></div>
      <div class="card kpi k-danger"><div class="label">失败</div><div class="num c-danger" id="d-failed">–</div></div>
      <div class="card kpi"><div class="label">成功率</div><div class="num" id="d-rate">–</div></div>
    </div>
    <div class="empty" id="d-empty" hidden>
      <span class="glyph">数</span>
      <p>本周还没有发布记录。<br>到「发布队列」勾选素材与账号，一键发布后这里会有数据。</p>
      <button class="btn btn-jade btn-sm" id="d-go-queue" style="margin-top:10px;">去发布队列</button>
    </div>
    <div class="works-panel" id="d-charts" hidden>
      <div class="works-head">
        <div>
          <div class="works-title">数据趋势</div>
          <div class="works-sub">近 14 天全部作品总播放 / 总点赞（平台累计口径，每天采集一次）</div>
        </div>
      </div>
      <div class="chart-wrap" id="d-trend-chart"></div>
      <div class="works-title" style="margin:18px 0 4px;">账号对比</div>
      <div class="works-sub" style="margin-bottom:8px;">各账号作品当前总播放</div>
      <div class="chart-wrap" id="d-acc-chart"></div>
    </div>
    <div class="works-panel">
      <div class="works-head">
        <div>
          <div class="works-title">已发布作品的数据</div>
          <div class="works-sub">每天 9 点自动采集一次；播放/点赞/评论/转发/收藏来自平台创作者后台</div>
        </div>
        <div class="cmt-head-btns">
          <button class="btn btn-ghost btn-sm" id="d-toggle-all">全部收起</button>
          <button class="btn btn-ghost btn-sm" id="d-works-refresh">立即刷新</button>
        </div>
      </div>
      <div id="d-works"><div class="works-empty">正在读取…</div></div>
    </div>`;
  const goQueue = $('#d-go-queue');
  if (goQueue) goQueue.onclick = () => document.querySelector('.nav-item[data-page="queue"]').click();
  $('#d-toggle-all').onclick = () => {
    const groups = [...$('#d-works').querySelectorAll('.cmt-group')];
    // 与按钮文案保持一致：只要有开着的就全部收起，全关了就全部展开
    const anyOpen = groups.some(g => !g.classList.contains('closed'));
    groups.forEach(g => g.classList.toggle('closed', anyOpen));
    collapsed.clear();
    if (anyOpen) groups.forEach(g => collapsed.add(g.dataset.acc));
    localStorage.setItem('dash-collapsed', JSON.stringify([...collapsed]));
    $('#d-toggle-all').textContent = anyOpen ? '全部展开' : '全部收起';
  };
  $('#d-works-refresh').onclick = async e => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = '采集中…';
    try {
      const r = await api('/works/refresh', { method: 'POST' });
      toast(r.message || '已开始采集');
      // 采集是后台线程，等 25 秒再拉一次结果
      setTimeout(() => { btn.disabled = false; btn.textContent = '立即刷新'; loadWorks(); }, 25000);
    } catch (err) { btn.disabled = false; btn.textContent = '立即刷新'; toast(err.message, true); }
  };
  try {
    const d = await api('/dashboard');
    $('#d-total').textContent = d.week.total;
    $('#d-success').textContent = d.week.success;
    $('#d-failed').textContent = d.week.failed;
    $('#d-rate').textContent = d.week.success_rate + '%';
    if (!d.week.total) $('#d-empty').hidden = false;
  } catch (e) { /* 静默，顶栏已提示 */ }
  loadCharts();
  loadWorks();
}

async function loadCharts() {
  const panel = $('#d-charts');
  if (!panel) return;
  try {
    const t = await api('/dashboard/trend?days=14');
    if (!t.days.some(d => d.views || d.likes) && !t.accounts.length) return;
    panel.hidden = false;
    $('#d-trend-chart').innerHTML = lineChart(t.days, [
      { key: 'views', label: '播放', color: 'var(--jade, #2f9e7e)' },
      { key: 'likes', label: '点赞', color: 'var(--danger, #d4584e)' },
    ]);
    $('#d-acc-chart').innerHTML = barChart(t.accounts);
    bindChartTips(panel);
  } catch (e) { /* 图表失败不影响下面表格 */ }
}

/** 纯 SVG 折线图（零依赖）：两条序列共用 X 轴，各自归一到自己的最大值。 */
function lineChart(days, series) {
  const W = 720, H = 220, P = { l: 44, r: 12, t: 24, b: 26 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const n = days.length;
  const x = i => P.l + (n <= 1 ? iw / 2 : i * iw / (n - 1));
  const maxOf = k => Math.max(1, ...days.map(d => d[k] || 0));
  const yOf = (k, v) => P.t + ih - (v / maxOf(k)) * ih;
  const lines = series.map(s => {
    const pts = days.map((d, i) => `${x(i).toFixed(1)},${yOf(s.key, d[s.key] || 0).toFixed(1)}`);
    const dots = days.map((d, i) =>
      `<circle class="chart-dot" cx="${x(i).toFixed(1)}" cy="${yOf(s.key, d[s.key] || 0).toFixed(1)}" r="3.5" fill="${s.color}"><title>${d.date} ${s.label} ${d[s.key] || 0}</title></circle>`).join('');
    return `<polyline points="${pts.join(' ')}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>${dots}`;
  }).join('');
  // 网格线 + Y 轴刻度（按播放量的量程）
  const ymax = maxOf('views');
  const grid = [0, 0.25, 0.5, 0.75, 1].map(f => {
    const gy = (P.t + ih - f * ih).toFixed(1);
    return `<line x1="${P.l}" y1="${gy}" x2="${W - P.r}" y2="${gy}" class="chart-grid"/>
      <text x="${P.l - 6}" y="${(+gy + 3).toFixed(1)}" class="chart-tick" text-anchor="end">${Math.round(f * ymax)}</text>`;
  }).join('');
  const xticks = days.map((d, i) => (i % Math.ceil(n / 7) === 0 || i === n - 1)
    ? `<text x="${x(i).toFixed(1)}" y="${H - 6}" class="chart-tick" text-anchor="middle">${d.date}</text>` : '').join('');
  const legend = series.map((s, i) =>
    `<g transform="translate(${P.l + i * 76},12)"><rect width="10" height="10" rx="2" fill="${s.color}"/><text x="14" y="9" class="chart-legend">${s.label}</text></g>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg" role="img">${grid}${lines}${xticks}${legend}</svg>`;
}

/** 横向条形图：账号对比。 */
function barChart(accounts) {
  if (!accounts.length) return '<div class="works-empty">还没有账号数据</div>';
  const max = Math.max(1, ...accounts.map(a => a.views));
  return accounts.map(a => `
    <div class="bar-row">
      <span class="bar-name" title="${esc(a.name)}">${esc(a.name)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${(a.views / max * 100).toFixed(1)}%"></span></span>
      <span class="bar-val">${a.views}<i class="bar-sub">赞 ${a.likes}</i></span>
    </div>`).join('');
}

/** 悬停圆点时浏览器原生 title 已够用，这里预留给未来自定义提示。 */
function bindChartTips() {}

async function loadWorks() {
  const box = $('#d-works');
  if (!box) return;
  try {
    const r = await api('/works?limit=30');
    const items = r.items || [];
    if (!items.length) {
      box.innerHTML = `<div class="works-empty">还没有作品数据。发布成功后点右上角「立即刷新」，
        或等每天 9 点自动采集（需要该账号处于登录状态）。</div>`;
      return;
    }
    box.innerHTML = groupByAccount(items);
    bindGroups(box);
  } catch (e) {
    box.innerHTML = `<div class="works-empty">读取作品数据失败：${esc(e.message)}</div>`;
  }
}

/** 按账号折叠分组：账号多了不会一屏全铺开，开合状态本地记忆。 */
function groupByAccount(items) {
  const groups = new Map();
  for (const w of items) {
    const g = groups.get(w.account_id) || {
      id: w.account_id, name: w.account_name, platform: w.platform, rows: [], views: 0,
    };
    g.rows.push(w);
    g.views += w.views || 0;
    groups.set(w.account_id, g);
  }
  return [...groups.values()].map(g => {
    const accKey = String(g.id);
    const closed = collapsed.has(accKey);
    return `
      <section class="cmt-group ${closed ? 'closed' : ''}" data-acc="${accKey}">
        <button class="cmt-group-head" type="button">
          <span class="cmt-caret">▾</span>
          <span class="cmt-group-name">${esc(g.name)}</span>
          <i class="cmt-plat">${PLAT[g.platform] || g.platform}</i>
          <span class="cmt-group-meta">共 ${g.rows.length} 条 · 播放 ${g.views}</span>
        </button>
        <div class="cmt-group-body">
          <div class="works-table">
            <div class="works-row works-th">
              <span>作品</span><span>播放</span><span>点赞</span>
              <span>评论</span><span>转发</span><span>收藏</span><span>采集日</span>
            </div>
            ${g.rows.map(w => `
              <div class="works-row">
                <span class="w-title" title="${esc(w.work_title)}">${esc(w.work_title)}</span>
                <span class="w-num">${w.views}</span>
                <span class="w-num">${w.likes}</span>
                <span class="w-num">${w.comments}</span>
                <span class="w-num">${w.shares}</span>
                <span class="w-num">${w.favorites}</span>
                <span class="w-date">${w.stat_date}</span>
              </div>`).join('')}
          </div>
        </div>
      </section>`;
  }).join('');
}

function bindGroups(box) {
  box.querySelectorAll('.cmt-group').forEach(g => {
    const head = g.querySelector('.cmt-group-head');
    head.onclick = () => {
      const closed = g.classList.toggle('closed');
      if (closed) collapsed.add(g.dataset.acc); else collapsed.delete(g.dataset.acc);
      localStorage.setItem('dash-collapsed', JSON.stringify([...collapsed]));
    };
  });
  const anyOpen = [...box.querySelectorAll('.cmt-group')].some(g => !g.classList.contains('closed'));
  const allBtn = $('#d-toggle-all');
  if (allBtn) allBtn.textContent = anyOpen ? '全部收起' : '全部展开';
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
