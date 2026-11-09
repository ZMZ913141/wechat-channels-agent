/** 入口：鉴权引导、导航路由、顶栏全局动作。
 *
 * 注意 import 后面的 ?v= 版本串：浏览器会把模块 URL 整体当作缓存键，
 * 升级时改这一个版本号即可让所有脚本重新下载，避免用户升级后还在跑旧 JS。
 */
import { api, setUnauthorizedHandler } from './api.js?v=3.21';
import { $, $$, toast } from './ui.js?v=3.21';
import * as today from './views/today.js?v=3.26';
import * as library from './views/library.js?v=3.21';
import * as queue from './views/queue.js?v=3.21';
import * as audit from './views/audit.js?v=3.21';
import * as dashboard from './views/dashboard.js?v=3.27';
import * as comments from './views/comments.js?v=3.27';
import * as messages from './views/messages.js?v=3.27';
import * as accounts from './views/accounts.js?v=3.25';
import * as settings from './views/settings.js?v=3.28';

const PAGES = {
  today:    { title: '今日排期', kicker: 'WORKSPACE / 01', load: () => today.load() },
  library:  { title: '素材库',   kicker: 'WORKSPACE / 02', load: () => library.load() },
  queue:    { title: '发布队列', kicker: 'WORKSPACE / 03', load: () => queue.load() },
  audit:    { title: '审计日志', kicker: 'WORKSPACE / 04', load: () => audit.load() },
  dashboard:{ title: '数据看板', kicker: 'WORKSPACE / 05', load: () => dashboard.load() },
  comments: { title: '评论管理', kicker: 'WORKSPACE / 06', load: () => comments.load() },
  messages: { title: '私信管理', kicker: 'WORKSPACE / 07', load: () => messages.load() },
  accounts: { title: '账号管理', kicker: 'SYSTEM / 08',    load: () => accounts.load() },
  settings: { title: '设置',     kicker: 'SYSTEM / 09',    load: () => settings.load() },
};

let currentUser = null;

/* ---------------- 鉴权 ---------------- */

function showLogin() {
  $('#app').hidden = true;
  $('#login').style.display = 'flex';
}

function showApp() {
  $('#login').style.display = 'none';
  $('#app').hidden = false;
  $('#who').textContent = currentUser ? (currentUser.display_name || currentUser.username) : '';
  refreshAiStatus();
  go('today');
}

async function authCheck() {
  try { currentUser = await api('/auth/me'); showApp(); }
  catch (e) { showLogin(); }
}

async function doLogin() {
  try {
    await api('/auth/login', { method: 'POST', body: JSON.stringify({
      username: $('#lg-user').value, password: $('#lg-pass').value }) });
    $('#lg-pass').value = ''; $('#lg-msg').textContent = '';
    authCheck();
  } catch (e) { $('#lg-msg').textContent = '登录失败：' + e.message; }
}

async function doRegister() {
  try {
    await api('/auth/register', { method: 'POST', body: JSON.stringify({
      username: $('#lg-user').value, password: $('#lg-pass').value, display_name: $('#rg-name').value }) });
    $('#lg-msg').textContent = '';
    authCheck();
  } catch (e) { $('#lg-msg').textContent = '注册失败：' + e.message; }
}

async function doLogout() {
  try { await api('/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
  currentUser = null;
  showLogin();
}

function toggleReg() {
  const b = $('#reg-box');
  b.hidden = !b.hidden;
  $('#lg-msg').textContent = '';
}

/* ---------------- 导航 ---------------- */

function go(page) {
  const meta = PAGES[page];
  if (!meta) return;
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === page));
  $$('.page').forEach(p => p.classList.remove('active'));
  $('#page-' + page).classList.add('active');
  $('#page-title').textContent = meta.title;
  $('#page-kicker').textContent = meta.kicker;
  meta.load();
}

/* ---------------- 顶栏 ---------------- */

async function refreshAiStatus() {
  try {
    const d = await api('/ai/status');
    const chip = $('#ai-chip');
    if (d.enabled) {
      chip.textContent = 'AI · 已启用';
      chip.className = 'chip on';
      chip.title = '真 AI 已生效（已配置 LLM_API_KEY）';
    } else {
      chip.textContent = 'AI · 未启用';
      chip.className = 'chip off';
      chip.title = '在 .env 填 LLM_API_KEY 后重启生效；未启用时回退本地规则';
    }
  } catch (e) { /* ignore */ }
}

async function testAI() {
  try {
    const d = await api('/ai/test', { method: 'POST' });
    if (d.ok) toast('AI 可用 ✓ 示例标题：' + d.sample.short_title);
    else toast('AI 不可用：' + d.reason, true);
  } catch (e) { toast('测试失败：' + e.message, true); }
}

async function scanInbox() {
  try {
    const d = await api('/materials/scan-inbox', { method: 'POST' });
    toast(`导入 ${d.added} 个新素材`);
    library.refresh();
  } catch (e) { toast('扫描失败：' + e.message, true); }
}

async function uploadMaterial(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const btn = $('#btn-upload');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = '上传中…';
  // 视频往往几十上百 MB，不给提示用户会以为点了没反应
  toast(`正在上传「${file.name}」，大文件可能要几分钟，请勿关闭页面`);
  try {
    const fd = new FormData();
    fd.append('file', file);
    const d = await api('/materials/upload', { method: 'POST', body: fd });
    toast('上传成功：' + d.file_name);
    input.value = '';
    library.refresh();
  } catch (e) { toast('上传失败：' + e.message, true); input.value = ''; }
  finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

/* ---------------- 装配 ---------------- */

function main() {
  setUnauthorizedHandler(showLogin);

  $('#btn-login').onclick = doLogin;
  $('#lg-pass').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
  $('#btn-register').onclick = doRegister;
  $('#btn-toggle-reg').onclick = toggleReg;
  $('#btn-toggle-reg2').onclick = toggleReg;
  $('#btn-logout').onclick = doLogout;
  $('#btn-test-ai').onclick = testAI;
  // AI 状态胶囊本身也可点：点了就做一次真实连通性测试，不是纯装饰
  $('#ai-chip').onclick = testAI;
  $('#btn-scan').onclick = scanInbox;
  $('#btn-upload').onclick = () => $('#file-in').click();
  $('#file-in').addEventListener('change', () => uploadMaterial($('#file-in')));
  $$('.nav-item').forEach(el => { el.onclick = () => go(el.dataset.page); });

  authCheck();
}

main();
