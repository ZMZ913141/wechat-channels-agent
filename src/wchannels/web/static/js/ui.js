/** UI 基础设施：toast、模态框（confirm / 表单 / 平台选择）、HTML 转义。 */

export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];

/** 插值转义：标题/文件名/AI 文案都是用户可控数据，直接拼 innerHTML 会被注入。 */
export function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

let toastTimer = null;
export function toast(msg, isErr = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (isErr ? ' err' : '');
  t.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.style.display = 'none'; }, 2800);
}

/* ---------------- 模态框 ---------------- */

/* Esc 关闭最上层弹窗（所有弹窗统一行为，避免弹窗卡住页面） */
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const masks = document.querySelectorAll('.modal-mask');
  if (masks.length) masks[masks.length - 1].remove();
});

function openModal({ title, sub = '', bodyHTML, actions }) {
  const root = $('#modal-root');
  return new Promise(resolve => {
    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    mask.innerHTML = `
      <div class="modal rise" role="dialog" aria-modal="true">
        <div class="modal-title">${esc(title)}</div>
        ${sub ? `<div class="modal-sub">${esc(sub)}</div>` : ''}
        <div class="modal-body">${bodyHTML}</div>
        <div class="modal-actions"></div>
      </div>`;
    const close = val => { mask.remove(); resolve(val); };
    mask.addEventListener('mousedown', e => { if (e.target === mask) close(null); });
    const bar = mask.querySelector('.modal-actions');
    for (const a of actions) {
      const b = document.createElement('button');
      b.className = 'btn ' + (a.cls || 'btn-ghost');
      b.textContent = a.label;
      b.onclick = () => {
        if (a.onClick) { a.onClick(mask, close); } else { close(a.value ?? null); }
      };
      bar.appendChild(b);
    }
    root.appendChild(mask);
    const first = mask.querySelector('input, select, textarea');
    if (first) first.focus();
  });
}

/** 确认对话框，resolve true/false。 */
export function confirmModal(title, sub = '', okLabel = '确认') {
  return openModal({
    title, sub, bodyHTML: '',
    actions: [
      { label: '取消', value: false },
      { label: okLabel, cls: 'btn-primary', value: true },
    ],
  }).then(v => v === true);
}

/** 平台三选一卡片，resolve 'wechat' | 'douyin' | 'xiaohongshu' | null。 */
export function pickPlatform(title, sub = '') {
  return openModal({
    title, sub,
    bodyHTML: `
      <div class="platform-cards">
        <div class="platform-card sel" data-p="wechat">视频号<small>WECHAT</small></div>
        <div class="platform-card" data-p="douyin">抖音<small>DOUYIN</small></div>
        <div class="platform-card" data-p="xiaohongshu">小红书<small>XHS</small></div>
      </div>`,
    actions: [
      { label: '取消', value: null },
      {
        label: '确认', cls: 'btn-primary',
        onClick: (mask, close) => {
          const sel = mask.querySelector('.platform-card.sel');
          close(sel ? sel.dataset.p : null);
        },
      },
    ],
  });
}

/** 通用表单模态。fields: [{key,label,value,placeholder,type}]，resolve 值对象或 null。 */
export function formModal(title, fields, { sub = '', okLabel = '保存', extraHTML = '' } = {}) {
  const body = fields.map(f => `
    <label class="fld">${esc(f.label)}
      ${f.type === 'textarea'
        ? `<textarea data-k="${esc(f.key)}" rows="3" placeholder="${esc(f.placeholder || '')}">${esc(f.value ?? '')}</textarea>`
        : `<input data-k="${esc(f.key)}" value="${esc(f.value ?? '')}" placeholder="${esc(f.placeholder || '')}">`}
    </label>`).join('') + extraHTML;
  return openModal({
    title, sub, bodyHTML: body,
    actions: [
      { label: '取消', value: null },
      {
        label: okLabel, cls: 'btn-primary',
        onClick: (mask, close) => {
          const out = {};
          mask.querySelectorAll('[data-k]').forEach(el => { out[el.dataset.k] = el.value; });
          close(out);
        },
      },
    ],
  });
}

/** 平台卡片的选中态切换（事件委托，所有模态共享）。 */
document.addEventListener('click', e => {
  const card = e.target.closest('.platform-card');
  if (!card) return;
  card.parentElement.querySelectorAll('.platform-card').forEach(c => c.classList.remove('sel'));
  card.classList.add('sel');
});
