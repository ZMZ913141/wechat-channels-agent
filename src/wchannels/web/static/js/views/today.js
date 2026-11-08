/** 今日排期：KPI 总览 + 新手步骤引导（步骤卡点击直达对应页面）。 */
import { api } from '../api.js?v=3.21';
import { $, $$, esc } from '../ui.js?v=3.21';

const STEPS = [
  { no: 'STEP 01', title: '导入素材', go: 'library', desc: '点顶栏「上传素材」直传视频，或把视频丢进收件箱后点「扫描收件箱」。' },
  { no: 'STEP 02', title: 'AI 完善', go: 'library', desc: '在「素材库」给待处理素材点「AI 完善」，按平台风格生成标题与话题。' },
  { no: 'STEP 03', title: '加入待发布', go: 'library', desc: '确认文案后点「加入待发布」（用 AI 完善会自动加入），可在「配置」里设合集、封面、AI 标识。' },
  { no: 'STEP 04', title: '排期发布', go: 'queue', desc: '在「发布队列」勾选素材与账号，智能排期错峰发，调度器到点自动执行。' },
];

export async function load() {
  const root = $('#page-today');
  root.innerHTML = `
    <div class="kpi-row stagger" id="todo-row" hidden>
      <div class="card kpi k-amber todo" data-go="comments" title="点这里去「评论管理」处理">
        <div class="label">新评论</div><div class="num" id="todo-comments">–</div><div class="sub">待回复 / 待处理</div></div>
      <div class="card kpi k-jade todo" data-go="messages" title="点这里去「私信管理」查看">
        <div class="label">未读私信</div><div class="num c-jade" id="todo-messages">–</div><div class="sub">有未读的会话</div></div>
    </div>
    <div class="kpi-row stagger">
      <div class="card kpi"><div class="label">素材总数</div><div class="num" id="kpi-total">–</div><div class="sub">全部素材</div></div>
      <div class="card kpi k-amber"><div class="label">待处理</div><div class="num" id="kpi-inbox">–</div><div class="sub">需要 AI 完善</div></div>
      <div class="card kpi"><div class="label">待发布</div><div class="num" id="kpi-ready">–</div><div class="sub">文案已就绪</div></div>
      <div class="card kpi k-jade"><div class="label">本周发布</div><div class="num c-jade" id="kpi-week">–</div><div class="sub" id="kpi-rate">–</div></div>
    </div>
    <div class="section-head"><h2>下一步</h2><span class="hint">四步走完一条素材的完整生命周期 · 点卡片直达对应页面</span></div>
    <div class="steps stagger">
      ${STEPS.map(s => `
        <div class="card step" data-go="${s.go}" title="点这里去「${s.title}」对应页面">
          <div class="step-no">${s.no}</div>
          <div class="step-title">${s.title}</div>
          <div class="step-desc">${s.desc}</div>
        </div>`).join('')}
    </div>`;
  // 步骤卡/待办卡都不是装饰：点一下真的跳到对应页面
  $$('.step[data-go], .todo[data-go]').forEach(el => {
    el.onclick = () => {
      const nav = document.querySelector(`.nav-item[data-page="${el.dataset.go}"]`);
      if (nav) nav.click();
    };
  });
  try {
    const d = await api('/dashboard');
    $('#kpi-total').textContent = d.materials.total;
    $('#kpi-inbox').textContent = d.materials.inbox;
    $('#kpi-ready').textContent = d.materials.ready;
    $('#kpi-week').textContent = d.week.success;
    $('#kpi-rate').textContent = '成功率 ' + d.week.success_rate + '%';
    const it = d.interaction || { new_comments: 0, unread_messages: 0 };
    if (it.new_comments > 0 || it.unread_messages > 0) {
      $('#todo-comments').textContent = it.new_comments;
      $('#todo-messages').textContent = it.unread_messages;
      $('#todo-row').hidden = false;
    }
  } catch (e) { /* 顶栏已有全局错误提示 */ }
}
