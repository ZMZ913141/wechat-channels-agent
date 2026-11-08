/** 账号管理：列表 + 新增（名称 + 平台卡片）+ 扫码登录 / 检测 / 删除。 */
import { api } from '../api.js?v=3.21';
import { $, esc, toast, confirmModal, formModal, pickPlatform } from '../ui.js?v=3.21';

const PLATFORM_CN = { wechat: '视频号', douyin: '抖音', xiaohongshu: '小红书' };

export async function load() {
  const root = $('#page-accounts');
  root.innerHTML = `
    <div class="section-head"><h2>账号管理</h2>
      <span class="hint">每个账号独立浏览器档案，互不串号；登录态过期需重新扫码</span>
      <div class="spacer"></div>
      <button class="btn btn-primary btn-sm" id="btn-add-acc">＋ 新增账号</button>
    </div>
    <div class="acc-grid stagger" id="acc-list"></div>`;
  $('#btn-add-acc').onclick = addAccount;

  let items;
  try {
    items = await api('/accounts');
  } catch (e) { toast('账号加载失败：' + e.message, true); return; }

  const grid = $('#acc-list-root') || $('#page-accounts .acc-grid');
  if (!items.length) {
    grid.innerHTML = `
      <div class="empty" style="grid-column:1/-1;">
        <span class="glyph">号</span>
        <p>还没有账号——点右上 <b>「＋ 新增账号」</b>，选平台后点「登录（扫码）」。</p>
      </div>`;
    return;
  }
  // 按平台分组展示，组内按名称排序；多号时不再混在一起
  const groups = {};
  items.forEach(a => { (groups[a.platform] ||= []).push(a); });
  const order = ['wechat', 'douyin', 'xiaohongshu']
    .concat(Object.keys(groups).filter(p => !['wechat', 'douyin', 'xiaohongshu'].includes(p)));
  const cardHTML = a => `
    <div class="card acc-card" data-id="${a.id}">
      <h3>${esc(a.name)}</h3>
      <div class="acc-meta">${esc(a.group || '未分组')} · <span class="pf">${PLATFORM_CN[a.platform] || esc(a.platform)}</span>
        ${a.nickname_detected ? `<br>已登录：${esc(a.nickname_detected)}` : ''}</div>
      <div class="row">
        <span class="status ${a.status === 'active' ? 'success' : 'failed'}">${esc(a.status)}</span>
        <span style="font-family:var(--mono);font-size:11px;color:var(--paper-3);">今日 ${a.daily_publish_count}/${a.daily_limit} 条</span>
      </div>
      <div class="row" style="margin-bottom:0;">
        <button class="btn btn-primary btn-sm" data-act="login">登录（扫码）</button>
        <button class="btn btn-ghost btn-sm" data-act="probe">检测登录</button>
        <button class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
        <button class="btn btn-ghost btn-sm" data-act="del">删除</button>
      </div>
    </div>`;
  grid.innerHTML = order.filter(p => groups[p] && groups[p].length).map(p => `
    <div class="acc-sec" style="grid-column:1/-1;">
      ${PLATFORM_CN[p] || esc(p)} <span>${groups[p].length}</span>
    </div>
    ${groups[p].slice().sort((a, b) => a.name.localeCompare(b.name, 'zh')).map(cardHTML).join('')}
  `).join('');

  grid.querySelectorAll('[data-act]').forEach(btn => {
    const card = btn.closest('.acc-card');
    const acc = items.find(x => x.id === +card.dataset.id);
    btn.onclick = () => act(btn.dataset.act, acc);
  });
}

async function act(kind, acc) {
  try {
    switch (kind) {
      case 'login':
        toast('请在弹出的浏览器窗口中扫码登录（最长约 150 秒）…');
        {
          const r = await api(`/accounts/${acc.id}/login`, { method: 'POST' });
          toast(r.logged_in ? '登录成功：' + r.nickname : '超时未登录，请重试', !r.logged_in);
        }
        break;
      case 'probe': {
        const r = await api(`/accounts/${acc.id}/login-probe`, { method: 'POST' });
        toast(r.logged_in ? '登录正常：' + r.nickname : '未登录或登录已失效，请点「登录（扫码）」重新扫码', !r.logged_in);
        break;
      }
      case 'edit': {
        const v = await formModal('编辑账号', [
          { key: 'name', label: '账号名称', value: acc.name, placeholder: '如：珠珠 · 雅太社' },
          { key: 'group', label: '分组', value: acc.group || '', placeholder: '如：雅太社组（留空为未分组）' },
        ]);
        if (!v) return;
        await api(`/accounts/${acc.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ name: (v.name || '').trim() || acc.name, group: (v.group || '').trim() }),
        });
        toast('已保存');
        break;
      }
      case 'del': {
        const ok = await confirmModal(`删除账号「${acc.name}」？`,
          '只删工作台里的记录，不影响平台上的账号；重新添加需再次扫码。', '删除');
        if (!ok) return;
        await api(`/accounts/${acc.id}`, { method: 'DELETE' });
        toast('已删除');
        break;
      }
    }
  } catch (e) { toast(e.message, true); return; }
  load();
}

async function addAccount() {
  const v = await formModal('新增账号', [
    { key: 'name', label: '账号名称', placeholder: '如：珠珠 · 雅太社' },
  ]);
  if (!v || !v.name.trim()) return;
  const platform = await pickPlatform('选择平台', '发布时按账号所属平台自动选择对应适配器');
  if (!platform) return;
  try {
    await api('/accounts', { method: 'POST', body: JSON.stringify({ name: v.name.trim(), group: '', platform }) });
    toast('已添加，点「登录（扫码）」完成授权');
    load();
  } catch (e) { toast(e.message, true); }
}
