/** 私信管理：各平台私信会话摘要（谁、最后一条、几条未读）。
 *
 * 视频号支持一键发送（草稿可直接发到平台会话，约 20~40 秒）；
 * 抖音/小红书只读，回复请用平台 App（IM 长连接/签名接口，站外发送不稳）。
 */
import { api } from '../api.js?v=3.21';
import { $, toast } from '../ui.js?v=3.21';

const PLAT = { wechat: '视频号', douyin: '抖音', xiaohongshu: '小红书' };
const TAB = [
  { key: '', label: '全部' },
  { key: 'unread', label: '有未读' },
];
let curTab = '';
const collapsed = new Set(JSON.parse(localStorage.getItem('dm-collapsed') || '[]'));

export async function load() {
  const root = $('#page-messages');
  root.innerHTML = `
    <div class="works-panel">
      <div class="works-head">
        <div>
          <div class="works-title">私信会话</div>
          <div class="works-sub">私信来自各平台公开入口；有新私信会发飞书通知。视频号会话可在这里直接发送回复，抖音/小红书请到平台 App 回复。</div>
        </div>
        <div class="cmt-head-btns">
          <button class="btn btn-ghost btn-sm" id="dm-toggle-all">全部展开</button>
          <button class="btn btn-ghost btn-sm" id="dm-refresh">抓取新私信</button>
        </div>
      </div>
      <div class="cmt-tabs" id="dm-tabs"></div>
      <div id="dm-list"><div class="works-empty">正在读取…</div></div>
    </div>`;
  $('#dm-toggle-all').onclick = () => {
    const groups = [...$('#dm-list').querySelectorAll('.cmt-group')];
    const anyOpen = groups.some(g => !g.classList.contains('closed'));
    groups.forEach(g => g.classList.toggle('closed', anyOpen));
    collapsed.clear();
    if (anyOpen) groups.forEach(g => collapsed.add(g.dataset.acc));
    localStorage.setItem('dm-collapsed', JSON.stringify([...collapsed]));
    $('#dm-toggle-all').textContent = anyOpen ? '全部展开' : '全部收起';
  };
  $('#dm-refresh').onclick = async e => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = '抓取中…';
    try {
      const r = await api('/messages/refresh', { method: 'POST' });
      toast(r.message || '已开始抓取');
      setTimeout(() => { btn.disabled = false; btn.textContent = '抓取新私信'; loadList(); }, 65000);
    } catch (err) {
      btn.disabled = false; btn.textContent = '抓取新私信';
      toast(err.message, true);
    }
  };
  loadList();
}

async function loadList() {
  const box = $('#dm-list');
  const tabsBox = $('#dm-tabs');
  if (!box) return;
  try {
    const r = await api('/messages?limit=300' + (curTab ? '&only_unread=true' : ''));
    const counts = r.counts || {};
    tabsBox.innerHTML = TAB.map(t => `
      <button class="cmt-tab ${t.key === curTab ? 'on' : ''}" data-tab="${t.key}">
        ${t.label}<i>${t.key ? (counts.unread || 0) : (counts.total || 0)}</i>
      </button>`).join('');
    tabsBox.querySelectorAll('.cmt-tab').forEach(b => {
      b.onclick = () => { curTab = b.dataset.tab; loadList(); };
    });

    const items = r.items || [];
    if (!items.length) {
      box.innerHTML = `<div class="works-empty">还没有私信。点右上角「抓取新私信」，
        或等每天数据采集时自动抓取（需要账号处于登录状态）。</div>`;
      return;
    }
    box.innerHTML = groupByAccount(items);
    bindGroups(box);
    bind(box);
  } catch (e) {
    box.innerHTML = `<div class="works-empty">读取私信失败：${esc(e.message)}</div>`;
  }
}

function groupByAccount(items) {
  const groups = new Map();
  for (const m of items) {
    const g = groups.get(m.account_id) || {
      id: m.account_id, name: m.account_name, platform: m.platform, rows: [],
    };
    g.rows.push(m);
    groups.set(m.account_id, g);
  }
  const list = [...groups.values()];
  list.forEach(g => {
    g.unread = g.rows.reduce((s, m) => s + (m.unread_count || 0), 0);
  });
  return list.map(g => {
    const accKey = String(g.id);
    const closed = collapsed.has(accKey) || (!g.unread && list.length > 1);
    if (!collapsed.has(accKey) && !g.unread && list.length > 1) collapsed.add(accKey);
    return `
      <section class="cmt-group ${closed ? 'closed' : ''}" data-acc="${accKey}">
        <button class="cmt-group-head" type="button">
          <span class="cmt-caret">▾</span>
          <span class="cmt-group-name">${esc(g.name)}</span>
          <i class="cmt-plat">${PLAT[g.platform] || g.platform}</i>
          <span class="cmt-group-meta">${g.unread ? `<b>未读 ${g.unread}</b>` : '无未读'} · 共 ${g.rows.length} 个会话</span>
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
      localStorage.setItem('dm-collapsed', JSON.stringify([...collapsed]));
    };
  });
  const anyOpen = [...box.querySelectorAll('.cmt-group')].some(g => !g.classList.contains('closed'));
  const allBtn = $('#dm-toggle-all');
  if (allBtn) allBtn.textContent = anyOpen ? '全部收起' : '全部展开';
}

function card(m) {
  const unread = m.unread_count > 0
    ? `<span class="dm-unread">${m.unread_count} 条未读</span>` : '';
  const group = m.is_group ? '<i class="cmt-reply-tag">群聊</i>' : '';
  const replied = m.replied_at ? '<i class="cmt-reply-tag">已发送</i>' : '';
  const sendBtn = m.can_send
    ? '<button class="btn btn-jade btn-sm" data-act="send">发送回复</button>' : '';
  const placeholder = m.can_send
    ? 'AI 生成或手动写好回复内容，点「发送回复」直接发到平台'
    : 'AI 生成或手动写好回复内容，复制后到平台 App 粘贴发送';
  return `
    <div class="cmt-item" data-id="${m.id}">
      <div class="cmt-top">
        <span>${esc(m.account_name)}<i>${PLAT[m.platform] || m.platform}</i></span>
        <span class="cmt-status">${unread}${m.created_text ? ' · ' + esc(m.created_text) : ''}</span>
      </div>
      <div class="cmt-body"><span class="cmt-author">${esc(m.sender)}</span>${group}${replied}${esc(m.content)}</div>
      <textarea class="cmt-draft" rows="2" placeholder="${placeholder}">${esc(m.ai_draft)}</textarea>
      <div class="cmt-actions">
        <button class="btn btn-jade btn-sm" data-act="draft">AI 生成回复</button>
        ${sendBtn}
        <button class="btn btn-ghost btn-sm" data-act="copy">复制草稿</button>
      </div>
    </div>`;
}

function bind(box) {
  box.querySelectorAll('.cmt-item').forEach(item => {
    const id = item.dataset.id;
    const area = item.querySelector('.cmt-draft');
    item.querySelectorAll('button[data-act]').forEach(btn => {
      btn.onclick = async () => {
        const act = btn.dataset.act;
        if (act === 'copy') {
          const txt = area.value.trim();
          if (!txt) return toast('草稿是空的', true);
          try { await navigator.clipboard.writeText(txt); toast('已复制，去平台 App 粘贴发送'); }
          catch (err) { area.select(); toast('浏览器不允许自动复制，已选中文本，请按 Ctrl+C', true); }
          return;
        }
        if (act === 'draft') {
          btn.disabled = true; btn.textContent = '生成中…';
          try {
            const r = await api(`/messages/${id}/draft`, { method: 'POST' });
            area.value = r.ai_draft || '';
            toast('草稿已生成');
          } catch (err) { toast(err.message, true); }
          finally { btn.disabled = false; btn.textContent = 'AI 生成回复'; }
        }
        if (act === 'send') {
          const txt = area.value.trim();
          if (!txt) return toast('先写好回复内容（可点 AI 生成）', true);
          btn.disabled = true; btn.textContent = '发送中…';
          try {
            await api(`/messages/${id}/send`, { method: 'POST', body: JSON.stringify({ text: txt }) });
            toast('已发送到平台会话');
            loadList();
          } catch (err) { toast(err.message, true); }
          finally { btn.disabled = false; btn.textContent = '发送回复'; }
        }
      };
    });
  });
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
