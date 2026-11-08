/** 审计日志：筛选 + 汇总 KPI + 流水表 + CSV 导出。 */
import { api } from '../api.js?v=3.21';
import { $, $$, esc, toast } from '../ui.js?v=3.21';

const SRC_LABEL = { manual: '手动', scheduler: '自动', skipped: '跳过' };
const PLATFORM_CN = { wechat: '视频号', douyin: '抖音', xiaohongshu: '小红书' };

export async function load() {
  const root = $('#page-audit');
  root.innerHTML = `
    <div class="section-head"><h2>发布审计日志</h2>
      <span class="hint">每一次发布尝试的谁 / 平台 / 素材 / 时间 / 结果 / 耗时</span>
      <div class="spacer"></div>
      <select id="audit-platform">
        <option value="">全部平台</option>
        <option value="wechat">视频号</option>
        <option value="douyin">抖音</option>
        <option value="xiaohongshu">小红书</option>
      </select>
      <select id="audit-ok">
        <option value="">全部结果</option>
        <option value="true">仅成功</option>
        <option value="false">仅失败</option>
      </select>
      <button class="btn btn-ghost btn-sm" id="btn-audit-refresh">刷新</button>
      <button class="btn btn-ghost btn-sm" id="btn-audit-export">导出 CSV</button>
    </div>
    <div class="kpi-row stagger">
      <div class="card kpi"><div class="label">近 7 天发布</div><div class="num" id="a-total">–</div></div>
      <div class="card kpi k-jade"><div class="label">成功</div><div class="num c-jade" id="a-success">–</div></div>
      <div class="card kpi k-danger"><div class="label">失败</div><div class="num c-danger" id="a-failed">–</div></div>
      <div class="card kpi"><div class="label">成功率</div><div class="num" id="a-rate">–</div></div>
    </div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>时间</th><th>账号</th><th>平台</th><th>素材</th><th>结果</th><th>来源</th><th>耗时</th><th>说明</th></tr></thead>
      <tbody id="audit-body"></tbody>
    </table></div>`;

  $('#btn-audit-refresh').onclick = () => loadRows();
  $('#audit-platform').onchange = () => loadRows();
  $('#audit-ok').onchange = () => loadRows();
  $('#btn-audit-export').onclick = exportCsv;

  await loadRows();
}

function query() {
  const p = $('#audit-platform').value, o = $('#audit-ok').value;
  const qs = [];
  if (p) qs.push('platform=' + p);
  if (o) qs.push('ok=' + o);
  return qs.length ? '?' + qs.join('&') : '';
}

async function loadRows() {
  try {
    const [items, sum] = await Promise.all([api('/audit' + query()), api('/audit/summary')]);
    $('#a-total').textContent = sum.total;
    $('#a-success').textContent = sum.success;
    $('#a-failed').textContent = sum.failed;
    $('#a-rate').textContent = sum.success_rate + '%';
    $('#audit-body').innerHTML = items.map(r => `
      <tr>
        <td class="mono">${r.created_at ? new Date(r.created_at).toLocaleString() : '–'}</td>
        <td>${esc(r.account)}</td>
        <td><span class="pf">${PLATFORM_CN[r.platform] || esc(r.platform)}</span></td>
        <td>${esc(r.material)}</td>
        <td><span class="status ${r.ok ? 'success' : 'failed'}">${esc(r.status)}</span></td>
        <td>${SRC_LABEL[r.source] || esc(r.source)}</td>
        <td class="mono">${r.duration_ms ? (r.duration_ms / 1000).toFixed(1) + 's' : '–'}</td>
        <td style="max-width:340px;font-size:12px;color:var(--paper-2);">${esc(r.message || '')}</td>
      </tr>`).join('')
      || '<tr><td colspan="8" style="color:var(--paper-3);text-align:center;padding:30px;">暂无发布记录</td></tr>';
  } catch (e) { toast('审计加载失败：' + e.message, true); }
}

function exportCsv() {
  // Cookie 会话同源，直接链接触发下载即可
  const a = document.createElement('a');
  a.href = '/api/audit/export.csv' + query();
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}
