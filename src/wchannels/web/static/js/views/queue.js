/** 发布队列：新建排期（智能 / 一键）+ 队列表格 + 立即执行 / 重试。 */
import { api } from '../api.js?v=3.21';
import { $, $$, esc, toast } from '../ui.js?v=3.21';

const QUEUE_CN = { queued: '排队中', wait_time: '等待到点', running: '发布中', success: '成功', failed: '失败', skipped: '已跳过' };
const PLATFORM_CN = { wechat: '视频号', douyin: '抖音', xiaohongshu: '小红书' };
const QUEUE_PAGE = 30;
const qstate = { status: '', page: 1 };

export async function load() {
  const root = $('#page-queue');
  root.innerHTML = `
    <div id="queue-alert"></div>
    <div class="card" style="margin-bottom:18px;">
      <div class="section-head">
        <h2>新建发布排期</h2>
        <span class="hint">勾选素材与账号；智能排期 = 未来 3 天黄金时段错峰，一键发布 = 立即排队、调度器 30 秒内开始</span>
      </div>
      <div class="row" style="align-items:flex-start;">
        <div style="flex:1;min-width:240px;">
          <div class="hint" style="font-size:12px;color:var(--paper-2);margin-bottom:6px;">选择素材（仅「待发布」状态）</div>
          <div class="pick-box" id="pick-materials"></div>
        </div>
        <div style="flex:1;min-width:240px;">
          <div class="hint" style="font-size:12px;color:var(--paper-2);margin-bottom:6px;">选择账号</div>
          <div class="pick-box" id="pick-accounts"></div>
        </div>
      </div>
      <div class="row" style="margin-top:12px;">
        <button class="btn btn-primary" id="btn-smart">智能排期（黄金时段）</button>
        <button class="btn btn-jade" id="btn-now">一键发布（尽快执行）</button>
        <span id="queue-hint" style="font-size:12px;color:var(--danger);"></span>
      </div>
    </div>
    <div class="section-head"><h2>发布队列</h2><div class="spacer"></div>
      <button class="btn btn-ghost btn-sm" id="btn-refresh">刷新</button></div>
    <div class="filter-bar" id="queue-filters"></div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>素材</th><th>账号</th><th>平台</th><th>计划时间</th><th>状态</th><th>结果</th><th>操作</th></tr></thead>
      <tbody id="queue-body"></tbody>
    </table></div>
    <div id="queue-pager" class="pager"></div>`;

  $('#btn-refresh').onclick = () => load();
  $('#btn-smart').onclick = () => createSchedule('smart');
  $('#btn-now').onclick = () => createSchedule('now');
  renderQueueFilters();
  const goLib = $('#btn-go-library');
  if (goLib) goLib.onclick = () => $('.nav-item[data-page="library"]').click();

  await Promise.all([loadPickers(), loadQueue()]);
}

/* 状态筛选：任务多了以后只看失败/只看排队中 */
function renderQueueFilters() {
  const opts = [['', '全部'], ['queued', '排队中'], ['wait_time', '等待到点'], ['running', '发布中'],
                ['success', '成功'], ['failed', '失败'], ['skipped', '已跳过']];
  const bar = $('#queue-filters');
  bar.innerHTML = opts.map(([v, label]) =>
    `<button class="filter-pill ${qstate.status === v ? 'sel' : ''}" data-s="${v}">${label}</button>`).join('');
  $$('#queue-filters .filter-pill').forEach(b => b.onclick = () => {
    qstate.status = b.dataset.s; qstate.page = 1;
    renderQueueFilters(); loadQueue();
  });
}

/* 队列分页：历史任务累计后不再一次性全加载 */
function renderQueuePager(total) {
  const pager = $('#queue-pager');
  const pages = Math.max(1, Math.ceil(total / QUEUE_PAGE));
  pager.innerHTML = `<button class="btn btn-ghost btn-sm" data-p="prev" ${qstate.page <= 1 ? 'disabled' : ''}>上一页</button>
    <span class="mono" style="font-size:12px;color:var(--paper-2);">第 ${qstate.page} / ${pages} 页 · 共 ${total} 条</span>
    <button class="btn btn-ghost btn-sm" data-p="next" ${qstate.page >= pages ? 'disabled' : ''}>下一页</button>`;
  $$('#queue-pager [data-p]').forEach(b => b.onclick = () => {
    qstate.page += b.dataset.p === 'next' ? 1 : -1;
    loadQueue();
  });
}

async function loadPickers() {
  try {
    const [matsResp, accs] = await Promise.all([api('/materials?status=ready&size=200'), api('/accounts')]);
    const mats = matsResp.items || matsResp;
    $('#pick-materials').innerHTML = mats.length ? mats.map(m => `
      <label class="pick-item"><input type="checkbox" class="pm" value="${m.id}">
        <span>${esc(m.short_title || m.title)}</span></label>`).join('')
      : `<div style="color:var(--paper-3);font-size:12px;padding:6px 2px;">还没有「待发布」素材。
          <button class="btn btn-ghost btn-sm" id="btn-go-library" style="margin-left:6px;">去素材库处理</button></div>`;
    $('#pick-accounts').innerHTML = accs.length ? accs.map(a => `
      <label class="pick-item"><input type="checkbox" class="pa" value="${a.id}">
        <span>${esc(a.name)} <span class="pf">${PLATFORM_CN[a.platform] || esc(a.platform)}</span></span></label>`).join('')
      : `<div style="color:var(--paper-3);font-size:12px;padding:6px 2px;">还没有账号。
          <button class="btn btn-ghost btn-sm" id="btn-go-acc-pick" style="margin-left:6px;">去账号管理新增</button></div>`;
    const goAccPick = $('#btn-go-acc-pick');
    if (goAccPick) goAccPick.onclick = () => $('.nav-item[data-page="accounts"]').click();

    // 登录态失效的号单独提示：否则小白只会看到任务反复失败，不知道该去哪修
    const bad = accs.filter(a => a.status === 'logged_out' || a.status === 'paused');
    const alert = $('#queue-alert');
    if (alert) {
      alert.innerHTML = bad.length ? `
        <div class="card" style="margin-bottom:14px;border-color:var(--danger);">
          <div style="color:var(--danger);font-size:13px;line-height:1.7;">
            ⚠ 这些账号现在发不出去：${bad.map(a => `${esc(a.name)}（${a.status === 'logged_out' ? '登录已失效' : '正在手动操作'}）`).join('、')}
            <button class="btn btn-ghost btn-sm" id="btn-go-accounts" style="margin-left:8px;">去账号管理处理</button>
          </div>
        </div>` : '';
      const goAcc = $('#btn-go-accounts');
      if (goAcc) goAcc.onclick = () => $('.nav-item[data-page="accounts"]').click();
    }
  } catch (e) { /* 忽略，表格区会报 */ }
}

function checkedIds(cls) {
  return $$('.' + cls + ':checked').map(x => +x.value);
}

async function createSchedule(mode) {
  const mids = checkedIds('pm'), aids = checkedIds('pa');
  const hint = $('#queue-hint');
  if (!mids.length) { hint.textContent = '请先勾选至少 1 个素材'; return; }
  if (!aids.length) { hint.textContent = '请先勾选至少 1 个账号'; return; }
  hint.textContent = '';
  const path = mode === 'smart' ? '/automation/smart-schedule' : '/publish';
  try {
    const r = await api(path, { method: 'POST', body: JSON.stringify({ material_ids: mids, account_ids: aids }) });
    toast(mode === 'smart' ? `已排入未来 3 天黄金时段（批次 ${r.batch_id}）`
                           : `已排入队列，调度器将自动执行（批次 ${r.batch_id}）`);
    load();
  } catch (e) { toast('排期失败：' + e.message, true); }
}

async function loadQueue() {
  try {
    const qs = new URLSearchParams({ page: qstate.page, size: QUEUE_PAGE });
    if (qstate.status) qs.set('status', qstate.status);
    const resp = await api('/publish/queue?' + qs);
    const items = resp.items || resp;
    // 删/切换筛选后页码越界时自动回退
    if (!items.length && qstate.page > 1 && resp.total) {
      qstate.page = Math.max(1, Math.ceil(resp.total / QUEUE_PAGE));
      return loadQueue();
    }
    $('#queue-body').innerHTML = items.map(t => `
      <tr>
        <td>${esc(t.material)}</td><td>${esc(t.account)}</td>
        <td>${t.platform ? `<span class="pf">${PLATFORM_CN[t.platform] || esc(t.platform)}</span>` : ''}</td>
        <td class="mono">${t.scheduled_at ? new Date(t.scheduled_at).toLocaleString() : '–'}</td>
        <td><span class="status ${esc(t.status)}">${QUEUE_CN[t.status] || esc(t.status)}</span></td>
        <td style="max-width:280px;font-size:12px;color:var(--paper-2);">${esc(t.result_message || '')}</td>
        <td>${['queued', 'failed', 'skipped', 'wait_time'].includes(t.status)
          ? `<button class="btn btn-warn btn-sm" data-run="${t.id}">${(t.status === 'queued' || t.status === 'wait_time') ? '立即执行' : '重试'}</button>` : ''}</td>
      </tr>`).join('')
      || '<tr><td colspan="7" style="color:var(--paper-3);text-align:center;padding:30px;">队列是空的——在上方勾选素材与账号开始排期</td></tr>';
    $$('#queue-body [data-run]').forEach(b => { b.onclick = () => runTarget(+b.dataset.run, b); });
    renderQueuePager(resp.total || items.length);
  } catch (e) { toast('队列加载失败：' + e.message, true); }
}

async function runTarget(id, btn) {
  // 发布要真开浏览器上传视频，可能持续几分钟；不给反馈小白会以为程序卡死
  if (btn) { btn.disabled = true; btn.textContent = '发布中…'; }
  toast('已开始发布，后台正在操作浏览器，请耐心等待（通常 1~3 分钟）');
  try {
    const r = await api(`/publish/targets/${id}/run`, { method: 'POST' });
    toast(r.success ? '发布成功' : '失败：' + r.reason, !r.success);
    loadQueue();
  } catch (e) {
    toast(e.message, true);
    if (btn) { btn.disabled = false; btn.textContent = '重试'; }
  }
}
