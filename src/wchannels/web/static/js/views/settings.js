/** 设置：AI 供应商切换（云端各家 + 自定义 OpenAI 兼容）。 */
import { api } from '../api.js?v=3.21';
import { $, esc, toast } from '../ui.js?v=3.21';

export async function load() {
  const root = $('#page-settings');
  root.innerHTML = `
    <div class="section-head"><h2>AI 接口</h2>
      <span class="hint">选供应商、填 API Key 即可用；保存后立即生效，也会写入 .env</span>
    </div>
    <div class="card" style="max-width:560px;padding:20px">
      <div style="display:grid;gap:14px">
        <label>供应商
          <select id="ai-provider" style="width:100%"></select>
        </label>
        <label>API Key
          <input id="ai-key" type="password" style="width:100%"
                 placeholder="sk-...（已配置过可留空）" autocomplete="off">
        </label>
        <label>接口地址
          <input id="ai-base" style="width:100%" placeholder="https://...">
        </label>
        <label>模型名
          <input id="ai-model" list="ai-models" style="width:100%" placeholder="模型名称">
          <datalist id="ai-models"></datalist>
        </label>
        <div style="display:flex;gap:10px;align-items:center">
          <button class="btn btn-primary btn-sm" id="btn-ai-save">保存</button>
          <button class="btn btn-ghost btn-sm" id="btn-ai-test">测试连接</button>
          <span id="ai-state" class="hint"></span>
        </div>
        <div id="ai-test-result" class="hint"></div>
      </div>
    </div>`;

  const cfg = await api('/ai/config');
  const sel = $('#ai-provider');
  sel.innerHTML = cfg.presets.map(p =>
    `<option value="${p.key}" ${p.key === cfg.provider ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
  $('#ai-base').value = cfg.base_url;
  $('#ai-model').value = cfg.model;
  $('#ai-state').textContent = cfg.has_key ? '已配置 API Key' : '尚未配置 API Key';

  // 拉取当前接口的可用模型，做成下拉建议（仍可手动输入）
  if (cfg.has_key) {
    api('/ai/models').then(r => {
      if (r.models && r.models.length) {
        $('#ai-models').innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join('');
      }
    }).catch(() => {});
  }

  sel.onchange = () => {
    const p = cfg.presets.find(x => x.key === sel.value);
    if (p && p.key !== 'custom') { $('#ai-base').value = p.base_url; $('#ai-model').value = p.model; }
  };

  $('#btn-ai-save').onclick = async () => {
    try {
      const r = await api('/ai/config', { method: 'POST', body: JSON.stringify({
        provider: sel.value, api_key: $('#ai-key').value.trim(),
        base_url: $('#ai-base').value.trim(), model: $('#ai-model').value.trim(),
      }) });
      $('#ai-key').value = '';
      $('#ai-state').textContent = '已保存并生效' + (r.has_key ? '（Key 已配置）' : '');
      toast('AI 配置已保存');
    } catch (e) { toast('保存失败：' + e.message, 'err'); }
  };

  $('#btn-ai-test').onclick = async () => {
    $('#ai-test-result').textContent = '测试调用中…';
    try {
      const r = await api('/ai/test', { method: 'POST' });
      $('#ai-test-result').textContent = r.ok
        ? `连接成功，示例输出：${r.sample.short_title}`
        : `测试失败：${r.reason}`;
    } catch (e) { $('#ai-test-result').textContent = '测试失败：' + e.message; }
  };
}
