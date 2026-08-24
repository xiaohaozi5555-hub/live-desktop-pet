/* dev-tools.js — 只用 renderer.js/action-map.js 已经暴露出来的公共接口
 * (window.__injectAction / window.ActionMap.MOTIONS / window.petAPI)，
 * 补回本项目原有、但这次交付没覆盖到的两个开发期能力：
 *   1) 演示回放：main.js --demo 会发 {kind:'demo'}，renderer.js 只接 kind==='render'。
 *   2) 调试面板：双击桌宠开关（不用 D 键），手动注入 action 测试。
 * 不改 renderer.js/action-map.js 内部一行代码，方便以后覆盖更新这两个文件时不冲突。 */
(function () {
  const AM = window.ActionMap;
  const inject = window.__injectAction;
  if (!AM || !inject) return;

  const stage = document.getElementById('stage');
  const debugPanel = document.getElementById('debug');
  const dm = document.getElementById('dbg-motions');
  const da = document.getElementById('dbg-actions');

  if (stage && debugPanel) {
    stage.addEventListener('dblclick', () => { debugPanel.hidden = !debugPanel.hidden; });
  }

  const act = (type, data) => ({ channel: 'action', type, ts: Date.now(), data });

  if (dm) {
    AM.MOTIONS.forEach((m) => {
      const b = document.createElement('button');
      b.textContent = m;
      b.onclick = () => inject(act('play_motion', { motion: m }));
      dm.appendChild(b);
    });
  }
  if (da) {
    const samples = [
      ['进场欢迎', act('play_motion', { motion: 'wave' })],
      ['被吓', act('play_motion', { motion: 'scared' })],
      ['大礼答谢', act('play_motion', { motion: 'thank_big' })],
      ['说话', act('speak', { text: '主播加油鸭！' })],
      ['关注答谢', act('play_motion', { motion: 'beg' })],
      ['闭嘴/停', act('stop', {})],
    ];
    samples.forEach(([label, msg]) => {
      const b = document.createElement('button');
      b.textContent = label;
      b.onclick = () => inject(msg);
      da.appendChild(b);
    });
  }

  const DEMO = [
    [0, act('play_motion', { motion: 'wave' })],
    [200, act('speak', { text: '欢迎 夜行猫 进入直播间~', voice: 'zh-CN-XiaoyiNeural', emotion: 'cheerful' })],
    [3200, act('play_motion', { motion: 'scared' })],
    [3400, act('show_bubble', { text: '呀啊——吓死宝宝了！', duration_ms: 2500 })],
    [6200, act('play_motion', { motion: 'thank_big' })],
    [6400, act('speak', { text: '谢谢神秘大哥的嘉年华！', emotion: 'excited' })],
    [9600, act('stop', {})],
  ];
  const runDemo = () => DEMO.forEach(([t, a]) => setTimeout(() => inject(a), t));

  if (window.petAPI && window.petAPI.onCommand) {
    window.petAPI.onCommand((msg) => { if (msg.kind === 'demo') runDemo(); });
  }
}());
