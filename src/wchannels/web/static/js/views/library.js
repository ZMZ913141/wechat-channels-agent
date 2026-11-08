/** 素材库：卡片网格 + 分页 + 拖拽上传 + 分账号配置面板。

分账号配置是核心：同一条素材发不同账号，标题/合集/话题/是否原创可以完全不同。
点击「配置」打开面板，左侧选账号，右侧编该账号专属配置；发布时优先用账号配置。
*/
import { api } from '../api.js?v=3.21';
import { $, $$, esc, toast, formModal, confirmModal, pickPlatform } from '../ui.js?v=3.21';

const STATUS_CN = { inbox: '待处理', ready: '待发布', published: '已发布', archived: '已归档', trash: '回收站' };
const PLATFORM_CN = { wechat: '视频号', douyin: '抖音', xiaohongshu: '小红书' };
const PAGE_SIZE = 24;

/* 各平台官方 AI 内容声明选项（以平台当前发布页为准，用户也可手动填写） */
const AI_DISCLOSURE_OPTS = {
  wechat: ['内容为AI生成', '含AI生成内容', '此内容为人工智能技术生成'],
  douyin: ['作品含AI生成内容', '内容由AI生成'],
  xiaohongshu: ['笔记含AI合成内容', '内容由AI生成'],
};

let state = { status: 'all', keyword: '', page: 1, tag: '', scope: 'mine' };
let myId = null;

export async function load() {
  const root = $('#page-library');
  root.innerHTML = `
    <div class="section-head">
      <h2>素材库</h2>
      <div class="spacer"></div>
      <select id="lib-scope" class="mini-select" title="按归属用户分区：我的/全部/某个用户">
        <option value="mine">我的素材（仅自己）</option>
        <option value="all">全部素材（含他人·只读）</option>
      </select>
      <input id="lib-search" placeholder="搜索标题/描述" style="width:180px;">
      <button class="btn btn-ghost btn-sm" id="lib-scan">📥 扫描收件箱</button>
    </div>
    <div class="filter-bar" id="lib-filters"></div>
    <div class="filter-bar" id="lib-tags"></div>
    <div id="lib-scope-tip" class="scope-tip" hidden></div>
    <div class="mat-grid" id="lib-grid" style="min-height:140px;"></div>
    <div id="lib-pager" class="pager"></div>`;

  try { const me = await api('/auth/me'); myId = me.id; } catch (e) { myId = null; }

  // 归属用户下拉：我的素材 / 全部用户 / 具体某个用户（带素材数量）
  const scopeSel = $('#lib-scope');
  try {
    const o = await api('/materials/owners');
    (o.owners || []).filter(x => x.user_id !== myId).forEach(x => {
      const opt = document.createElement('option');
      opt.value = String(x.user_id);
      opt.textContent = `👤 ${x.name}（${x.count}）`;
      scopeSel.appendChild(opt);
    });
  } catch (e) { /* 归属人列表失败不影响主流程 */ }
  scopeSel.value = state.scope;
  if (!scopeSel.value) { state.scope = 'mine'; scopeSel.value = 'mine'; }
  updateScopeTip();
  scopeSel.onchange = e => {
    state.scope = e.target.value; state.tag = ''; state.page = 1;
    updateScopeTip();
    loadList(); refreshTags();
  };
  $('#lib-scan').onclick = async () => {
    const r = await api('/materials/scan-inbox', { method: 'POST' });
    toast(`导入 ${r.added} 个新素材`);
    state.page = 1; loadList(); refreshTags();
  };
  let searchTimer = null;
  $('#lib-search').oninput = e => {
    state.keyword = e.target.value.trim(); state.page = 1;
    // 防抖：素材多时不必每敲一个键就请求+重绘一次
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadList, 250);
  };

  bindDragDrop();
  renderFilters();
  refreshTags();
  loadList();
}

export function refresh() { load(); }

/** 顶部提示：当前看的是谁家的素材、能不能编辑，避免把别人的素材当自己的操作。 */
function updateScopeTip() {
  const tip = $('#lib-scope-tip');
  if (!tip) return;
  if (state.scope === 'mine') { tip.hidden = true; return; }
  const sel = $('#lib-scope');
  const label = sel && sel.selectedOptions[0] ? sel.selectedOptions[0].textContent.trim() : '';
  tip.hidden = false;
  tip.textContent = state.scope === 'all'
    ? '当前查看：全部素材 · 卡片右上角 👤 是上传人；别人的素材只读（能预览播放，不能改配置/删除/加入待发布）'
    : `当前查看：${label} · 只读（只能预览播放）`;
}

function renderFilters() {
  const filters = [
    { k: 'all', label: '全部' }, { k: 'inbox', label: '待处理' },
    { k: 'ready', label: '待发布' }, { k: 'published', label: '已发布' },
    { k: 'trash', label: '回收站' },
  ];
  $('#lib-filters').innerHTML = filters.map(f =>
    `<button class="filter-pill ${state.status === f.k ? 'sel' : ''}" data-s="${f.k}">${f.label}</button>`).join('');
  $$('#lib-filters .filter-pill').forEach(b => b.onclick = () => {
    state.status = b.dataset.s; state.page = 1; renderFilters(); loadList();
  });
}

async function loadList() {
  const grid = $('#lib-grid');
  const qs = new URLSearchParams({ page: state.page, size: PAGE_SIZE, scope: state.scope });
  if (state.status !== 'all') qs.set('status', state.status);
  if (state.keyword) qs.set('keyword', state.keyword);
  if (state.tag) qs.set('tag', state.tag);
  try {
    const resp = await api('/materials?' + qs);
    const items = resp.items || resp;
    // 删掉当前页最后一条后页码会越界，自动退回最后一页
    if (!items.length && state.page > 1 && resp.total) {
      state.page = Math.max(1, Math.ceil(resp.total / PAGE_SIZE));
      return loadList();
    }
    grid.innerHTML = items.length ? items.map(card).join('')
      : `<div class="empty">素材库是空的——点「📥 扫描收件箱」把视频捞进来，或把视频文件拖进这里</div>`;
    bindCardEvents(items);
    renderPager(resp.total || items.length);
  } catch (e) {
    grid.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

/* 分区标签条：点标签只看该分区，再点一次取消 */
async function refreshTags() {
  const bar = $('#lib-tags');
  if (!bar) return;
  try {
    const r = await api('/materials/tags?scope=' + encodeURIComponent(state.scope));
    const tags = r.tags || [];
    bar.innerHTML = tags.length
      ? `<span class="tag-label">分区：</span>` + tags.map(t =>
          `<button class="filter-pill ${state.tag === t ? 'sel' : ''}" data-t="${esc(t)}">${esc(t)}</button>`).join('')
      : '';
    $$('#lib-tags .filter-pill').forEach(b => b.onclick = () => {
      state.tag = state.tag === b.dataset.t ? '' : b.dataset.t;
      state.page = 1; refreshTags(); loadList();
    });
  } catch (e) { bar.innerHTML = ''; }
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pager = $('#lib-pager');
  if (pages <= 1) { pager.innerHTML = ''; return; }
  let html = `<button class="btn btn-ghost btn-sm" ${state.page <= 1 ? 'disabled' : ''} data-p="${state.page - 1}">上一页</button>`;
  html += `<span style="margin:0 12px;font-size:12px;color:var(--paper-2);">第 ${state.page} / ${pages} 页 · 共 ${total} 条</span>`;
  html += `<button class="btn btn-ghost btn-sm" ${state.page >= pages ? 'disabled' : ''} data-p="${state.page + 1}">下一页</button>`;
  pager.innerHTML = html;
  $$('#lib-pager button[data-p]').forEach(b => b.onclick = () => { state.page = +b.dataset.p; loadList(); });
}

function card(m) {
  const mine = myId == null || m.user_id === myId;
  const tags = (m.tags || '').split(',').map(t => t.trim()).filter(Boolean);
  return `
    <div class="mat-card" data-id="${m.id}">
      <div class="mat-thumb" data-act="preview" title="点击播放预览">
        <img loading="lazy" decoding="async" fetchpriority="low" src="/api/media/thumb/${m.id}.jpg" onerror="this.style.opacity=.2">
        <span class="play-badge">▶</span>
        <span class="status ${esc(m.status)}">${STATUS_CN[m.status] || esc(m.status)}</span>
        ${state.scope !== 'mine' && m.owner_name ? `<span class="owner-chip">👤 ${esc(m.owner_name)}</span>` : ''}
      </div>
      <div class="mat-body">
        <div class="mat-title">${esc(m.short_title || m.title)}</div>
        <div class="mat-meta">${m.width}×${m.height} · ${Math.round(m.duration_sec)}s${m.collection ? ' · 合集 ' + esc(m.collection) : ''}</div>
        ${tags.length ? `<div class="mat-tags">${tags.map(t => `<span class="tag-chip">${esc(t)}</span>`).join('')}</div>` : ''}
        ${m.status === 'inbox' ? '<div class="mat-meta" style="color:var(--paper-3);">填好文案后点「加入待发布」即可排期</div>' : ''}
        <div class="mat-actions">
          ${!mine ? '<span class="mat-meta" style="color:var(--paper-3);">其他用户的素材 · 只读</span>'
            : m.status === 'inbox' ? `<button class="btn btn-jade btn-sm" data-act="ready">加入待发布</button>
               <button class="btn btn-ghost btn-sm" data-act="config">配置</button>
               <button class="btn btn-ghost btn-sm" data-act="delete">删除</button>`
            : m.status === 'trash' ? `<button class="btn btn-ghost btn-sm" data-act="restore">恢复</button>
               <button class="btn btn-ghost btn-sm" data-act="delete">删除</button>`
            : `${m.status === 'ready' ? '<button class="btn btn-ghost btn-sm" data-act="to-inbox">退回待处理</button>' : ''}
               <button class="btn btn-ghost btn-sm" data-act="config">配置</button>
               <button class="btn btn-ghost btn-sm" data-act="autofill">AI 完善</button>
               <button class="btn btn-ghost btn-sm" data-act="cover">封面</button>
               <button class="btn btn-ghost btn-sm" data-act="delete">删除</button>`}
        </div>
      </div>
    </div>`;
}

function bindCardEvents(items) {
  const map = Object.fromEntries(items.map(m => [m.id, m]));
  $$('#lib-grid .mat-card').forEach(card => {
    const id = +card.dataset.id;
    const m = map[id];
    card.querySelectorAll('[data-act]').forEach(b => {
      b.onclick = e => { e.stopPropagation(); handle(id, b.dataset.act, m); };
    });
  });
}

async function handle(id, act, m) {
  try {
    switch (act) {
    case 'preview': openPreview(m); break;
    case 'config': openConfigPanel(m); break;
    case 'ready':
      await api(`/materials/${id}/to-ready`, { method: 'POST' });
      toast('已加入「待发布」'); loadList(); break;
    case 'restore':
      await api(`/materials/${id}/restore`, { method: 'POST' });
      toast('已恢复为待发布'); loadList(); break;
    case 'to-inbox':
      if (!(await confirmModal('退回「待处理」？',
          '只改本地状态，不会删除已发作品；这条素材会重新回到待完善列表。', '退回待处理'))) return;
      await api(`/materials/${id}/status`, { method: 'POST', body: JSON.stringify({ status: 'inbox' }) });
      toast('已退回「待处理」'); loadList(); break;
    case 'autofill': {
      const platform = await pickPlatform('AI 完善文案', '按目标平台风格生成标题/描述/话题（会自动加入待发布）');
      if (!platform) return;
      await api(`/materials/${id}/autofill`, { method: 'POST', body: JSON.stringify({ extra_hint: '', platform }) });
      toast(`AI 完善完成（${PLATFORM_CN[platform]} 风格）`);
      loadList(); break;
    }
    case 'cover': {
      // 真实接口是 /api/cover/{mid}/extract-frame（此前错调 /materials/{id}/cover，点了没反应）
      await openCoverChooser(m); break;
    }
    case 'delete': {
      openDeleteChooser(m); break;
    }
    }
  } catch (e) {
    toast('操作失败：' + e.message, true);
  }
}

/* 删除二选一：移入回收站（只删列表）/ 彻底删除（连本地视频一起删） */
function openDeleteChooser(m) {
  const inTrash = m.status === 'trash';
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-title">删除「${esc(m.short_title || m.title)}」</div>
      <div class="modal-sub">${inTrash ? '这条素材已在回收站，只能彻底删除：' : '两种方式，按需选一种：'}</div>
      <div class="modal-body" style="display:flex;flex-direction:column;gap:10px;">
        ${inTrash ? '' : `
        <button class="btn btn-ghost" id="del-list" style="text-align:left;padding:12px 14px;line-height:1.6;">
          <strong>① 只删列表（移入回收站）</strong><br>
          <span style="font-size:12px;color:var(--paper-3);">素材从列表消失，本地视频文件保留，可在回收站恢复</span>
        </button>`}
        <button class="btn btn-ghost" id="del-hard" style="text-align:left;padding:12px 14px;line-height:1.6;border-color:var(--danger);">
          <strong style="color:var(--danger);">${inTrash ? '' : '② '}连本地视频一起删（彻底删除）</strong><br>
          <span style="font-size:12px;color:var(--paper-3);">列表记录和本地真实视频文件都删除，不可恢复</span>
        </button>
      </div>
      <div class="modal-actions"><button class="btn btn-ghost" id="del-cancel">取消</button></div>
    </div>`;
  document.getElementById('modal-root').appendChild(mask);
  mask.addEventListener('mousedown', e => { if (e.target === mask) mask.remove(); });
  mask.querySelector('#del-cancel').onclick = () => mask.remove();

  const listBtn = mask.querySelector('#del-list');
  if (listBtn) listBtn.onclick = async () => {
    mask.remove();
    await api(`/materials/${m.id}`, { method: 'DELETE' });
    toast('已移入回收站（本地文件保留）');
    loadList(); refreshTags();
  };

  mask.querySelector('#del-hard').onclick = async () => {
    mask.remove();
    if (!(await confirmModal('确认彻底删除？', '本地视频文件会一起删掉，无法恢复。', '彻底删除'))) return;
    try {
      const r = await api(`/materials/${m.id}/hard`, { method: 'DELETE' });
      toast(r.removed_file ? '已删除列表记录和本地视频' : '已删除列表记录（文件已被其他素材共用，已保留）');
      loadList(); refreshTags();
    } catch (e) { toast('删除失败：' + e.message, true); }
  };
}

/* ---------------- 视频播放预览 / 封面 ---------------- */

/* 卡片缩略图只占小格，点开才播放：竖向视频弹窗按高度自适应，不撑爆面板 */
function openPreview(m) {
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = `
    <div class="modal preview-modal" role="dialog" aria-modal="true">
      <div class="modal-title">${esc(m.short_title || m.title)}</div>
      <div class="modal-sub">${m.width}×${m.height} · ${Math.round(m.duration_sec)}s · 可拖动进度条</div>
      <video class="preview-video" src="/api/media/video/${m.id}" controls autoplay playsinline preload="metadata"></video>
      <div class="modal-actions"><button class="btn btn-primary" id="pv-close">关闭</button></div>
    </div>`;
  document.getElementById('modal-root').appendChild(mask);
  mask.addEventListener('mousedown', e => { if (e.target === mask) mask.remove(); });
  mask.querySelector('#pv-close').onclick = () => mask.remove();
  const video = mask.querySelector('video');
  video.addEventListener('error', () => {
    mask.querySelector('.modal-sub').innerHTML =
      '<span style="color:var(--danger);">视频文件读不到（可能已被移动或删除）</span>';
  });
}

async function openCoverChooser(m) {
  const v = await formModal('生成封面', [
    { key: 'at', label: '从第几秒抽帧（0 = 视频开头）', value: '1' },
  ], { sub: '会用 ffmpeg 从原视频抽一帧设为封面，后面发布时优先用这张', okLabel: '抽帧并设为封面' });
  if (!v) return;
  const at = Math.max(0, Number(v.at) || 0);
  await api(`/cover/${m.id}/extract-frame?at_sec=${at}`, { method: 'POST' });
  toast('封面已生成');
}

/* ---------------- 拖拽上传 ---------------- */

function bindDragDrop() {
  const grid = $('#lib-grid');
  let dragDepth = 0;
  grid.addEventListener('dragenter', e => { e.preventDefault(); dragDepth++; grid.classList.add('drag-over'); });
  grid.addEventListener('dragover', e => e.preventDefault());
  grid.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) grid.classList.remove('drag-over'); });
  grid.addEventListener('drop', async e => {
    e.preventDefault(); dragDepth = 0; grid.classList.remove('drag-over');
    const files = [...(e.dataTransfer?.files || [])].filter(f => f.type.startsWith('video/'));
    if (!files.length) { toast('请拖入视频文件', true); return; }
    let ok = 0;
    for (const f of files) {
      try {
        const fd = new FormData(); fd.append('file', f);
        await api('/materials/upload', { method: 'POST', body: fd });
        ok++;
      } catch (e) { toast(`${f.name} 上传失败：${e.message}`, true); }
    }
    toast(`已上传 ${ok} 个视频`);
    loadList();
  });
}

/* ---------------- 分账号配置面板 ---------------- */

async function openConfigPanel(m) {
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = `
    <div class="modal modal-wide" role="dialog" aria-modal="true">
      <div class="modal-title">素材配置 · ${esc(m.short_title || m.title)}</div>
      <div class="modal-sub">同一素材发不同账号可以配不同的标题/合集/话题/可见范围/是否原创；发布时优先用这里的账号配置。</div>
      <div class="cfg-status-row">
        <span>素材状态</span>
        <select id="cfg-status">
          <option value="inbox">待处理</option>
          <option value="ready">待发布</option>
          <option value="published">已发布</option>
        </select>
        <span class="cfg-status-hint">点错步骤可以在这里改回来；已成功发过的号不会因为改状态而重发</span>
      </div>
      <div class="cfg-pub-bar" id="cfg-pub-bar"></div>
      <div class="modal-body cfg-body">
        <div class="cfg-preview">
          <div class="cfg-mini-card">
            <div class="cfg-mini-thumb" title="点击播放预览">
              <img src="/api/media/thumb/${m.id}.jpg" onerror="this.style.opacity=.3">
              <span class="play-badge">▶</span>
            </div>
            <div class="cfg-mini-info">
              <div class="cfg-mini-title">${esc(m.short_title || m.title)}</div>
              <div class="cfg-mini-meta">${m.width}×${m.height} · ${Math.round(m.duration_sec)}s</div>
              <button class="btn btn-ghost btn-sm" id="btn-cfg-play">▶ 播放预览</button>
            </div>
          </div>
          <div class="ai-assist">
            <div class="ai-assist-title">AI 辅助填写</div>
            <label class="fld">视频文案 / 口播稿（可选）
              <textarea id="ai-src" rows="3" placeholder="粘贴这条视频的文案或口播稿，AI 按平台风格提炼标题/描述/话题；不填则用文件名+已有信息"></textarea>
            </label>
            <div class="fld-row">
              <label class="fld" style="flex:1;">文案风格
                <select id="ai-plat">
                  <option value="wechat">视频号</option>
                  <option value="douyin">抖音</option>
                  <option value="xiaohongshu">小红书</option>
                </select>
              </label>
              <button class="btn btn-jade btn-sm" id="btn-ai-fill" style="margin-top:22px;">AI 生成</button>
            </div>
            <div class="ai-hint">选风格会自动切到该平台账号的专属配置；生成结果只填入表单，勾选多个账号后点保存可一次写入这些号</div>
          </div>
          <details class="cfg-details">
            <summary>素材默认配置 <span class="cfg-details-hint">未单独配置的账号回退到这里</span></summary>
            <label class="fld">默认短标题<input data-d="short_title" value="${esc(m.short_title || '')}"></label>
            <label class="fld">默认描述<textarea data-d="description" rows="2">${esc(m.description || '')}</textarea></label>
            <label class="fld">默认话题（逗号分隔）<input data-d="topics" value="${esc(m.topics || '')}"></label>
            <label class="fld">默认合集<input data-d="collection" value="${esc(m.collection || '')}"></label>
            <label class="fld">分区标签（逗号分隔，如 服装,备用）<input data-d="tags" value="${esc(m.tags || '')}" placeholder="留空=不分区"></label>
            <label class="fld">AI 标识文案（官方选项，留空=不标注）
              <input data-d="ai_disclosure" list="ai-opts-all" value="${esc(m.ai_disclosure || '')}" placeholder="选官方声明，也可手动填写">
            </label>
            <datalist id="ai-opts-all">${aiOptionsHTML(null)}</datalist>
            <button class="btn btn-primary btn-sm" id="btn-save-default">保存默认配置</button>
          </details>
        </div>
        <div class="cfg-accounts">
          <div class="cfg-acc-list" id="cfg-acc-list"></div>
          <div class="cfg-form" id="cfg-form"><div style="color:var(--paper-3);">← 先选左边一个账号</div></div>
        </div>
      </div>
      <div class="modal-actions"><button class="btn btn-primary" id="cfg-close">完成</button></div>
    </div>`;
  document.getElementById('modal-root').appendChild(mask);

  mask.addEventListener('mousedown', e => { if (e.target === mask) mask.remove(); });
  mask.querySelector('#cfg-close').onclick = () => mask.remove();

  // 素材状态可回退：误点「AI 完善 / 加入待发布」后能改回待处理
  const statusSel = mask.querySelector('#cfg-status');
  statusSel.value = m.status;
  statusSel.onchange = async () => {
    const to = statusSel.value;
    const cn = { inbox: '待处理', ready: '待发布', published: '已发布' }[to] || to;
    if (!(await confirmModal(`把素材状态改为「${cn}」？`,
        to === 'inbox' ? '改回待处理不会删除已发作品，只是让它重新回到待完善列表。'
                       : '只改本地状态，不会触发发布。', '确认修改'))) {
      statusSel.value = m.status;
      return;
    }
    try {
      await api(`/materials/${m.id}/status`, { method: 'POST', body: JSON.stringify({ status: to }) });
      m.status = to;
      toast(`状态已改为「${cn}」`);
      loadList();
    } catch (e) { statusSel.value = m.status; toast('改状态失败：' + e.message, true); }
  };

  // 缩略图 / 按钮都能打开播放预览（不再用占满面板的大图）
  const play = () => openPreview(m);
  mask.querySelector('.cfg-mini-thumb').onclick = play;
  mask.querySelector('#btn-cfg-play').onclick = play;

  // AI 辅助填写：可粘贴口播稿，只填入表单、不自动保存
  mask.querySelector('#btn-ai-fill').onclick = async e => {
    const btn = e.currentTarget;
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = '生成中…';
    try {
      const r = await api(`/materials/${m.id}/autofill`, {
        method: 'POST',
        body: JSON.stringify({
          platform: mask.querySelector('#ai-plat').value,
          transcript: mask.querySelector('#ai-src').value.trim(),
          dry_run: true,
        }),
      });
      const where = fillAIResult(mask, r.copy);
      toast(r.copy.ai_used
        ? `AI 文案已填入${where}，检查后点保存`
        : `未配置 LLM，已用本地规则填入${where}，建议手动润色`);
    } catch (err) { toast('AI 生成失败：' + err.message, true); }
    finally { btn.disabled = false; btn.textContent = label; }
  };

  // 保存素材默认配置
  mask.querySelector('#btn-save-default').onclick = async () => {
    const body = {};
    mask.querySelectorAll('[data-d]').forEach(el => { body[el.dataset.d] = el.value; });
    await api(`/materials/${m.id}`, { method: 'PUT', body: JSON.stringify(body) });
    Object.assign(m, body);   // 卡片上的分区标签即时生效
    toast('默认配置已保存');
    loadList(); refreshTags();
  };

  // 加载账号列表
  try {
    const cfgs = await api(`/materials/${m.id}/account-configs`);
    const list = mask.querySelector('#cfg-acc-list');
    // 账号按平台分组（视频号 → 抖音 → 小红书 → 其他），组内按名称排序，
    // 避免多号时列表混在一起、找不到要对哪个号配置
    const groups = {};
    cfgs.forEach(c => { (groups[c.platform] ||= []).push(c); });
    const order = ['wechat', 'douyin', 'xiaohongshu']
      .concat(Object.keys(groups).filter(p => !['wechat', 'douyin', 'xiaohongshu'].includes(p)));
    const PUB_CN = { success: '已发布', failed: '发布失败', running: '发布中', queued: '排队中' };
    const itemHTML = c => `<div class="cfg-acc-item" data-aid="${c.account_id}" data-plat="${esc(c.platform)}" data-status="${esc(c.status)}">
          <input type="checkbox" class="cfg-acc-check" data-aid="${c.account_id}" title="勾选后把当前 AI 文案批量写入该号">
          <div class="cfg-acc-main">
            <div class="cfg-acc-name">${esc(c.account_name)}</div>
            <div class="cfg-acc-pf">
              ${c.status === 'logged_out' ? '<span style="color:var(--danger);font-size:11px;">未登录</span>' : ''}
              ${c.publish_status
                ? `<span class="pub-tag ${esc(c.publish_status)}">${PUB_CN[c.publish_status] || esc(c.publish_status)}</span>`
                : '<span class="pub-tag none">未发布</span>'}
              ${c.original_work ? '<span class="orig-tag">原创</span>' : ''}</div>
          </div>
        </div>`;
    list.innerHTML = cfgs.length
      ? order.filter(p => groups[p] && groups[p].length).map(p => `
          <div class="cfg-acc-group">
            <div class="cfg-acc-group-title">${PLATFORM_CN[p] || esc(p)} <span>${groups[p].length}</span></div>
            ${groups[p].slice().sort((a, b) => a.account_name.localeCompare(b.account_name, 'zh'))
              .map(itemHTML).join('')}
          </div>`).join('')
      : '<div style="color:var(--paper-3);padding:16px;line-height:1.9;">还没有账号<br><button class="btn btn-jade btn-sm" id="cfg-goto-acc" style="margin-top:8px;">去账号管理新增</button></div>';

    // 已发/未发汇总 + 只补发没发成功的号（已成功的不会重复发）
    const done = cfgs.filter(c => c.publish_status === 'success');
    const todo = cfgs.filter(c => (!c.publish_status || c.publish_status === 'failed')
                                  && c.status !== 'logged_out');
    const bar = mask.querySelector('#cfg-pub-bar');
    if (cfgs.length) {
      bar.innerHTML = `
        <span>共 <b>${cfgs.length}</b> 个号：已发 <b>${done.length}</b> 个，未发 <b>${cfgs.length - done.length}</b> 个</span>
        ${todo.length ? `<button class="btn btn-jade btn-sm" id="btn-retry-missing">补发未发的 ${todo.length} 个号</button>` : ''}`;
      const retry = bar.querySelector('#btn-retry-missing');
      if (retry) retry.onclick = async () => {
        if (!(await confirmModal('只补发未成功的账号？',
            `将排期 ${todo.map(c => c.account_name).join('、')}，已成功的号不会重复发布。`, '开始补发'))) return;
        retry.disabled = true;
        try {
          await api('/publish', { method: 'POST', body: JSON.stringify({
            material_ids: [m.id], account_ids: todo.map(c => c.account_id),
          }) });
          toast(`已排入补发队列（${todo.length} 个号）`);
          mask.remove();
          loadList();
        } catch (e) { retry.disabled = false; toast('补发失败：' + e.message, true); }
      };
    }
    const gotoAcc = list.querySelector('#cfg-goto-acc');
    if (gotoAcc) gotoAcc.onclick = () => {
      mask.remove();
      const nav = document.querySelector('.nav-item[data-page="accounts"]');
      if (nav) nav.click();
    };
    list.querySelectorAll('.cfg-acc-item').forEach(el => {
      el.onclick = () => {
        list.querySelectorAll('.cfg-acc-item').forEach(x => x.classList.remove('sel'));
        el.classList.add('sel');
        const cfg = cfgs.find(c => c.account_id === +el.dataset.aid);
        // AI 辅助填写默认跟随该账号的平台风格
        const plat = mask.querySelector('#ai-plat');
        if (plat && cfg) plat.value = cfg.platform;
        renderAccountForm(mask, m, cfg, cfgs);
      };
      // 勾选框只负责批量写入，不触发账号切换
      const chk = el.querySelector('.cfg-acc-check');
      chk.onclick = e => e.stopPropagation();
      chk.onchange = () => updateBatchTip(mask);
    });
    if (cfgs[0]) {
      const first = list.querySelector('.cfg-acc-item');
      first.querySelector('.cfg-acc-check').checked = true;
      first.click();
      updateBatchTip(mask);
    }

    // 切换「文案风格」= 跳转到该平台账号的专属配置，两者双向同步
    const platSel = mask.querySelector('#ai-plat');
    platSel.onchange = () => {
      const p = platSel.value;
      const item = list.querySelector(`.cfg-acc-item[data-plat="${p}"]`);
      if (!item) {
        toast(`还没有${PLATFORM_CN[p] || p}账号，结果只会填进默认配置`, true);
        return;
      }
      item.click();  // 选中账号 → 右侧渲染该号专属配置，同时把风格回填为它的平台
      toast(`已切到「${item.querySelector('.cfg-acc-name').textContent}」的${PLATFORM_CN[p] || p}专属配置`);
    };
  } catch (e) { toast('加载账号配置失败：' + e.message, true); }
}

/** 批量写入提示：显示当前勾选了多少个账号。 */
function updateBatchTip(mask) {
  const tip = mask.querySelector('#cfg-batch-tip');
  if (!tip) return;
  const n = mask.querySelectorAll('.cfg-acc-check:checked').length;
  tip.innerHTML = n > 1
    ? `已勾选 <b>${n}</b> 个号：保存时会把标题 / 描述 / 话题同步写入全部勾选号`
    : '勾选左侧多个账号，可把这份文案一次写入多个号（只同步标题 / 描述 / 话题）';
}

function renderAccountForm(mask, m, cfg, allCfgs) {
  // 同一个视频只能有一个账号声明原创：找出已勾原创的账号，其余账号禁用勾选框
  const originalAid = (allCfgs || []).find(c => c.original_work)?.account_id;
  const otherHasOriginal = !!originalAid && originalAid !== cfg.account_id;
  const box = mask.querySelector('#cfg-form');
  box.innerHTML = `
    <div style="font-weight:600;margin-bottom:6px;">${esc(cfg.account_name)} · ${PLATFORM_CN[cfg.platform] || cfg.platform} 的专属配置</div>
    <div class="cfg-acc-hint">✅ 发布时优先用这份配置（已写入排期任务，留空的项才回退到默认）</div>
    <label class="fld">短标题<input data-k="short_title" value="${esc(cfg.short_title)}" placeholder="留空=用默认"></label>
    <label class="fld">描述<textarea data-k="description" rows="3" placeholder="留空=用默认">${esc(cfg.description)}</textarea></label>
    <label class="fld">话题（逗号分隔，不用写 #）<input data-k="topics" value="${esc(cfg.topics)}" placeholder="留空=用默认"></label>
    <div class="fld-row">
      <label class="fld" style="flex:1;">合集
        <input data-k="collection" value="${esc(cfg.collection)}" placeholder="留空=用默认">
      </label>
      <button class="btn btn-ghost btn-sm" id="btn-fetch-col" style="margin-top:22px;">拉取该号合集</button>
    </div>
    <label class="fld">首条评论<input data-k="comment" value="${esc(cfg.comment)}"></label>
    <label class="fld">可见范围
      <select data-k="visibility">
        <option value="public" ${cfg.visibility !== 'private' ? 'selected' : ''}>公开（所有人可见）</option>
        <option value="private" ${cfg.visibility === 'private' ? 'selected' : ''}>仅自己可见（测试/私密用）</option>
      </select>
    </label>
    <div class="cfg-acc-hint">选「仅自己可见」时，若该平台页面找不到私密选项，发布会自动中止、不会改成公开提交</div>
    <label class="fld">AI 标识文案（官方选项，留空=用默认）
      <input data-k="ai_disclosure" list="ai-opts-${cfg.account_id}" value="${esc(cfg.ai_disclosure)}" placeholder="选官方声明，也可手动填写">
    </label>
    <datalist id="ai-opts-${cfg.account_id}">${aiOptionsHTML(cfg.platform)}</datalist>
    <label class="fld">封面图路径（留空=用默认抽帧）<input data-k="cover_path" value="${esc(cfg.cover_path)}"></label>
    <label class="fld-check"><input type="checkbox" data-k="original_work" ${otherHasOriginal ? 'disabled' : ''} ${otherHasOriginal ? '' : (cfg.original_work ? 'checked' : '')}> 标记为「原创」${otherHasOriginal ? '<span style="color:var(--danger);">（已有其他账号声明原创，本号不能勾选）</span>' : '（部分平台与搬运互斥，按需勾选）'}</label>
    ${otherHasOriginal ? `<div style="font-size:12px;color:var(--danger);margin:0 0 10px 22px;">⚠ 这条视频已有账号声明原创，同一个视频只能有一个账号勾选「原创」</div>` : ''}
    <div class="cfg-batch-tip" id="cfg-batch-tip"></div>
    <button class="btn btn-primary" id="btn-save-acc">保存该账号配置</button>`;

  updateBatchTip(mask);

  mask.querySelector('#btn-fetch-col').onclick = async btn => {
    btn.target.disabled = true; btn.target.textContent = '拉取中…';
    try {
      const r = await api(`/accounts/${cfg.account_id}/collections`);
      const cols = r.collections || [];
      if (!cols.length) { toast('未拉到合集（可能未登录或页面改版），可手动输入'); return; }
      // 用 datalist 做候选下拉，避免把多个合集名拼成一段非法字符串存进合集字段
      let dl = box.querySelector('datalist');
      if (!dl) { dl = document.createElement('datalist'); box.appendChild(dl); }
      dl.id = `col-list-${cfg.account_id}`;
      dl.innerHTML = cols.map(c => `<option value="${esc(c)}">`).join('');
      const input = box.querySelector('[data-k="collection"]');
      input.setAttribute('list', dl.id);
      if (!input.value) input.value = cols[0];
      toast(`拉到 ${cols.length} 个合集，点「合集」输入框可从下拉里选`);
    } catch (e) { toast('拉取失败：' + e.message, true); }
    finally { btn.target.disabled = false; btn.target.textContent = '拉取该号合集'; }
  };

  mask.querySelector('#btn-save-acc').onclick = async () => {
    const body = {};
    box.querySelectorAll('[data-k]').forEach(el => {
      body[el.dataset.k] = el.type === 'checkbox' ? el.checked : el.value;
    });
    await api(`/materials/${m.id}/account-configs/${cfg.account_id}`, { method: 'PUT', body: JSON.stringify(body) });
    // 批量：把当前号的标题/描述/话题同步写入其它已勾选的账号
    const checked = [...mask.querySelectorAll('.cfg-acc-check:checked')]
      .map(el => +el.dataset.aid).filter(id => id !== cfg.account_id);
    const copyFields = {
      short_title: body.short_title, description: body.description, topics: body.topics,
    };
    for (const aid of checked) {
      await api(`/materials/${m.id}/account-configs/${aid}`, {
        method: 'PUT', body: JSON.stringify(copyFields),
      });
    }
    // 从服务端重新拉取该素材全部账号配置，确保原创互斥判断用的是最新状态
    const fresh = await api(`/materials/${m.id}/account-configs`);
    if (Array.isArray(allCfgs)) { allCfgs.splice(0, allCfgs.length, ...fresh); }
    // 同步当前 cfg 引用，使表单重渲染时数据一致
    const refreshed = (allCfgs || []).find(c => c.account_id === cfg.account_id);
    if (refreshed) Object.assign(cfg, refreshed);
    // 账号列表刷新原创标记
    const list = mask.querySelector('#cfg-acc-list');
    list.querySelectorAll('.cfg-acc-item').forEach(el => {
      const tag = el.querySelector('.orig-tag');
      const c = (allCfgs || []).find(x => x.account_id === +el.dataset.aid);
      if (c?.original_work) {
        if (!tag) el.insertAdjacentHTML('beforeend', '<span class="orig-tag">原创</span>');
      } else if (tag) tag.remove();
    });
    toast(checked.length
      ? `已保存，并同步写入另外 ${checked.length} 个号`
      : '该账号配置已保存');
  };
}

/** AI 标识官方选项：传 platform 给该平台的候选，不传则合并三平台。 */
function aiOptionsHTML(platform) {
  const opts = platform
    ? (AI_DISCLOSURE_OPTS[platform] || [])
    : [...new Set(Object.values(AI_DISCLOSURE_OPTS).flat())];
  return opts.map(o => `<option value="${esc(o)}"></option>`).join('');
}

/** 把 AI 结果填进当前可见表单（账号表单优先），返回填入位置说明。 */
function fillAIResult(mask, copy) {
  const inAcc = !!mask.querySelector('#cfg-form [data-k="short_title"]');
  const q = k => `[data-${inAcc ? 'k' : 'd'}="${k}"]`;
  for (const [k, v] of [
    ['short_title', copy.short_title],
    ['description', copy.description],
    ['topics', copy.topics],
  ]) {
    const el = mask.querySelector(q(k));
    if (el) el.value = v || '';
  }
  return inAcc ? '到该账号配置' : '到默认配置';
}
