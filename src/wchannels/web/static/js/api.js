/** API 封装：统一 /api 前缀、JSON 头、401 跳转、错误文本提取。 */

let onUnauthorized = () => {};

export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  // FormData / URLSearchParams 必须由浏览器自己生成 Content-Type 与 boundary，
  // 手动塞 JSON 头会让后端 multipart 解析失败。
  const isForm = (typeof FormData !== 'undefined' && opts.body instanceof FormData)
    || (typeof URLSearchParams !== 'undefined' && opts.body instanceof URLSearchParams);
  if (!isForm && !headers['Content-Type'] && opts.body != null) {
    headers['Content-Type'] = 'application/json';
  }
  const r = await fetch('/api' + path, { headers, credentials: 'same-origin', ...opts });
  if (r.status === 401) {
    // 登录/注册接口的 401 是「用户名或密码错误」，要显示后端 detail；
    // 其他接口的 401 才是「未登录」，需要跳登录页。
    let detail = '';
    if (path.startsWith('/auth/login') || path.startsWith('/auth/register')) {
      try { const j = await r.json(); if (j && j.detail) detail = String(j.detail); } catch (e) { /* ignore */ }
      throw new Error(detail || '用户名或密码错误');
    }
    onUnauthorized();
    throw new Error('未登录');
  }
  if (!r.ok) {
    let t = '';
    try { t = await r.text(); } catch (e) { /* ignore */ }
    // FastAPI 错误体是 {"detail": "..."}，直接展示 JSON 原文对小白不友好
    try { const j = JSON.parse(t); if (j && j.detail) t = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (e) { /* ignore */ }
    throw new Error(t || ('HTTP ' + r.status));
  }
  if (r.status === 204) return null;
  return r.json();
}
