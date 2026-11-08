/** 评论管理：抓取平台评论、AI 回复草稿、标记已回复。 */
import { api } from '../api.js?v=3.21';
import { $, toast } from '../ui.js?v=3.21';

const PLAT = { wechat: '视频号', douyin: '抖音', xiaohongshu: '小红书' };
const TAB = [
  { key: '', label: '全部' },
  { key: 'new', label: '待回复' },
  { key: 'drafted', label: '已起草' },
  { key: 'replied', label: '已回复' },
];
let curTab = '';
// 账号分组默认展开还是收起记在本地，下次进来不再乱
const collapsed = new Set(JSON.parse(localStorage.getItem('cmt-collapsed') || '[]'));
// 批量勾选集合（跨分组）；切 tab 或批量操作成功后清空
const selected = new Set();

export async function load() {
  const root = $('#page-comments');
  root.innerHTML = `
    <div class="works-panel">
      <div class="works-head">
        <div>
          <div class="works-title">观众评论</div>
          <div class="works-sub">评论区来自平台创作者后台；有新评论会发飞书通知。回复动作请在平台里完成，这里只做草稿。</div>
        </div>
        <div class="cmt-head-btns">
          <button class="btn btn-ghost btn-sm" id="cmt-toggle-all">全部展开</button>
          <button class="btn btn-ghost btn-sm" id="cmt-refresh">抓取新评论</button>
        </div>
      </div>
      <div class="cmt-tabs" id="cmt-tabs"></div>
      <div class="cmt-batchbar" id="cmt-batch" hidden></div>
      <div id="cmt-list"><div class="works-empty">正在读取…</div></div>
    </div>`;
  $('#cmt-toggle-all').onclick = () => {
    const groups = [...$('#cmt-list').querySelectorAll('.cmt-group')];
    // 与按钮文案保持一致：只要有开着的就全部收起，全关了就全部展开
    const anyOpen = groups.some(g => !g.classList.contains('closed'));
    groups.forEach(g => g.classList.toggle('closed', anyOpen));
    collapsed.clear();
    if (anyOpen) groups.forEach(g => collapsed.add(g.dataset.acc));
    localStorage.setItem('cmt-collapsed', JSON.stringify([...collapsed]));
    $('#cmt-toggle-all').textContent = anyOpen ? '全部展开' : '全部收起';
  };
  $('#cmt-refresh').onclick = async e => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = '抓取中…';
    try {
      const r = await api('/comments/refresh', { method: 'POST' });
      toast(r.message || '已开始抓取');
      setTimeout(() => { btn.disabled = false; btn.textContent = '抓取新评论'; loadList(); }, 65000);
    } catch (err) {
      btn.disabled = false; btn.textContent = '抓取新评论';
      toast(err.message, true);
    }
  };
  loadList();
}

async function loadList() {
  const box = $('#cmt-list');
  const tabsBox = $('#cmt-tabs');
  if (!box) return;
  try {
    const r = await api('/comments?limit=300' + (curTab ? '&status=' + curTab : ''));
    const counts = r.counts || {};
    tabsBox.innerHTML = TAB.map(t => `
      <button class="cmt-tab ${t.key === curTab ? 'on' : ''}" data-tab="${t.key}">
        ${t.label}<i>${t.key ? (counts[t.key] || 0) : (counts.total || 0)}</i>
      </button>`).join('');
    tabsBox.querySelectorAll('.cmt-tab').forEach(b => {
      b.onclick = () => { curTab = b.dataset.tab; selected.clear(); loadList(); };
    });

    const items = r.items || [];
    if (!items.length) {
      box.innerHTML = `<div class="works-empty">还没有评论。点右上角「抓取新评论」，
        或等每天数据采集时自动抓取（需要账号处于登录状态）。</div>`;
      return;
    }
    box.innerHTML = groupByAccount(items);
    bindGroups(box);
    bind(box);
    renderBatchBar(box);
  } catch (e) {
    box.innerHTML = `<div class="works-empty">读取评论失败：${esc(e.message)}</div>`;
  }
}

/** 按账号折叠：账号多/评论多时不至于全铺开一屏。 */
function groupByAccount(items) {
  const groups = new Map();
  for (const c of items) {
    const g = groups.get(c.account_id) || {
      id: c.account_id, name: c.account_name, platform: c.platform, rows: [],
    };
    g.rows.push(c);
    groups.set(c.account_id, g);
  }
  const list = [...groups.values()];
  list.forEach(g => {
    g.newCount = g.rows.filter(c => c.status === 'new').length;
  });
  return list.map(g => {
    const accKey = String(g.id);
    // 第一次进来：有待回复的就展开，全处理过的收起；用户手动开合会记下来
    const closed = collapsed.has(accKey) || (!g.newCount && list.length > 1);
    if (!collapsed.has(accKey) && !g.newCount && list.length > 1) collapsed.add(accKey);
    return `
      <section class="cmt-group ${closed ? 'closed' : ''}" data-acc="${accKey}">
        <button class="cmt-group-head" type="button">
          <span class="cmt-caret">▾</span>
          <span class="cmt-group-name">${esc(g.name)}</span>
          <i class="cmt-plat">${PLAT[g.platform] || g.platform}</i>
          <span class="cmt-group-meta">${g.newCount ? `<b>待回复 ${g.newCount}</b>` : '无待回复'} · 共 ${g.rows.length} 条</span>
        </button>
        <div class="cmt-group-body">${g.rows.map(card).join('')}</div>
      </section>`;
  }).join('');
}

function bindGroups(box) {
  box.querySelectorAll('.cmt-group').forEach(g => {
    const head = g.querySelector('.cmt-group-head');
    head.onclick = () => {
      const closed = g.classList.toggle('closed');
      if (closed) collapsed.add(g.dataset.acc); else collapsed.delete(g.dataset.acc);
      localStorage.setItem('cmt-collapsed', JSON.stringify([...collapsed]));
    };
  });
  const anyOpen = [...box.querySelectorAll('.cmt-group')].some(g => !g.classList.contains('closed'));
  const allBtn = $('#cmt-toggle-all');
  if (allBtn) allBtn.textContent = anyOpen ? '全部收起' : '全部展开';
}

function card(c) {
  const statusCn = { new: '待回复', drafted: '已起草', replied: '已回复', ignored: '已忽略' }[c.status] || c.status;
  const checked = selected.has(c.id) ? 'checked' : '';
  return `
    <div class="cmt-item" data-id="${c.id}">
      <div class="cmt-top">
        <label class="cmt-check"><input type="checkbox" data-check="${c.id}" ${checked}></label>
        <span>${esc(c.account_name)}<i>${PLAT[c.platform] || c.platform}</i> · ${esc(c.work_title)}</span>
        <span class="cmt-status s-${c.status}">${statusCn}${c.created_text ? ' · ' + esc(c.created_text) : ''}</span>
      </div>
      <div class="cmt-body ${c.parent_key ? 'is-reply' : ''}"><span class="cmt-author">${esc(c.author)}</span>${c.parent_key ? '<i class="cmt-reply-tag">回复</i>' : ''}${esc(c.content)}</div>
      <textarea class="cmt-draft" rows="2" placeholder="AI 生成或手动写好回复内容，点「发送回复」直达平台">${esc(c.ai_draft)}</textarea>
      <div class="cmt-actions">
        <button class="btn btn-primary btn-sm" data-act="send">发送回复</button>
        <button class="btn btn-jade btn-sm" data-act="draft">AI 生成回复</button>
        <button class="btn btn-ghost btn-sm" data-act="copy">复制草稿</button>
        <button class="btn btn-ghost btn-sm" data-act="replied">标记已回复</button>
        <button class="btn btn-ghost btn-sm" data-act="ignored">忽略</button>
      </div>
    </div>`;
}

function bind(box) {
  box.querySelectorAll('input[data-check]').forEach(cb => {
    cb.onchange = () => {
      const id = Number(cb.dataset.check);
      if (cb.checked) selected.add(id); else selected.delete(id);
      renderBatchBar(box);
    };
  });
  box.querySelectorAll('.cmt-item').forEach(item => {
    const id = item.dataset.id;
    const area = item.querySelector('.cmt-draft');
    item.querySelectorAll('button[data-act]').forEach(btn => {
      btn.onclick = async e => {
        const act = btn.dataset.act;
        if (act === 'copy') {
          const txt = area.value.trim();
          if (!txt) return toast('草稿是空的', true);
          try { await navigator.clipboard.writeText(txt); toast('已复制，去平台评论区粘贴'); }
          catch (err) { area.select(); toast('浏览器不允许自动复制，已选中文本，请按 Ctrl+C', true); }
          return;
        }
        if (act === 'send') {
          const txt = area.value.trim();
          if (!txt) return toast('先写回复内容（或点 AI 生成）', true);
          if (!confirm(`确定把这条回复发送到平台上？\n\n${txt.slice(0, 120)}`)) return;
          btn.disabled = true; btn.textContent = '发送中…（约 30 秒）';
          try {
            await api(`/comments/${id}/send`, { method: 'POST', body: JSON.stringify({ text: txt }) });
            toast('已发送到平台');
            loadList();
          } catch (err) { toast(err.message, true); }
          finally { btn.disabled = false; btn.textContent = '发送回复'; }
          return;
        }
        if (act === 'draft') {
          btn.disabled = true; btn.textContent = '生成中…';
          try {
            const r = await api(`/comments/${id}/draft`, { method: 'POST' });
            area.value = r.ai_draft || '';
            item.querySelector('.cmt-status').textContent = '已起草';
            toast('草稿已生成');
          } catch (err) { toast(err.message, true); }
          finally { btn.disabled = false; btn.textContent = 'AI 生成回复'; }
          return;
        }
        const status = act === 'replied' ? 'replied' : 'ignored';
        try {
          await api(`/comments/${id}/status`, { method: 'POST', body: JSON.stringify({ status }) });
          toast(status === 'replied' ? '已标记为已回复' : '已忽略');
          loadList();
        } catch (err) { toast(err.message, true); }
      };
    });
  });
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** 批量工具栏：勾选后出现，支持全选/忽略/批量 AI 草稿。 */
function renderBatchBar(box) {
  const bar = $('#cmt-batch');
  if (!bar) return;
  const n = selected.size;
  if (!n) { bar.hidden = true; bar.innerHTML = ''; return; }
  bar.hidden = false;
  bar.innerHTML = `
    <span class="cmt-batch-n">已选 ${n} 条</span>
    <button class="btn btn-ghost btn-sm" data-bact="all">全选本页</button>
    <button class="btn btn-jade btn-sm" data-bact="draft">批量 AI 草稿</button>
    <button class="btn btn-ghost btn-sm" data-bact="ignore">批量忽略</button>
    <button class="btn btn-ghost btn-sm" data-bact="clear">清除选择</button>`;
  bar.querySelectorAll('button[data-bact]').forEach(btn => {
    btn.onclick = async () => {
      const act = btn.dataset.bact;
      if (act === 'clear') {
        selected.clear();
        box.querySelectorAll('input[data-check]').forEach(cb => { cb.checked = false; });
        renderBatchBar(box);
        return;
      }
      if (act === 'all') {
        box.querySelectorAll('input[data-check]').forEach(cb => {
          cb.checked = true; selected.add(Number(cb.dataset.check));
        });
        renderBatchBar(box);
        return;
      }
      const ids = [...selected];
      if (act === 'ignore') {
        btn.disabled = true; btn.textContent = '处理中…';
        try {
          const r = await api('/comments/batch/status', { method: 'POST', body: JSON.stringify({ ids, status: 'ignored' }) });
          toast(`已忽略 ${r.updated} 条`);
          selected.clear();
          loadList();
        } catch (err) { toast(err.message, true); btn.disabled = false; btn.textContent = '批量忽略'; }
        return;
      }
      if (act === 'draft') {
        btn.disabled = true; btn.textContent = '生成中…（每条约 2 秒）';
        try {
          const r = await api('/comments/batch/draft', { method: 'POST', body: JSON.stringify({ ids }) });
          let msg = `已生成 ${r.drafted} 条草稿`;
          if (r.failed) msg += `，${r.failed} 条失败`;
          if (r.capped) msg += '（一次最多 50 条，剩余请再点一次）';
          toast(msg, !!r.failed);
          selected.clear();
          loadList();
        } catch (err) { toast(err.message, true); btn.disabled = false; btn.textContent = '批量 AI 草稿'; }
      }
    };
  });
}
