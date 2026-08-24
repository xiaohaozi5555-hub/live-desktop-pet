/* verify_character.js — 表现层逻辑离线验证（Node，无需 GUI）：
 *   1) action-map 契约映射正确；2) edge-tts 能生成有效 mp3。
 * 运行: node verify_character.js   （退出码 0=全过）
 */
const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');
const { mapAction } = require('./action-map');

let pass = 0, fail = 0;
const check = (name, ok, detail = '') => { ok ? pass++ : fail++; console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`); };

// 1) 契约映射
const t1 = mapAction({ channel: 'action', type: 'play_motion', ts: 1, data: { motion: 'scared' } });
check('play_motion:scared -> 动作+被吓表情', t1.motion === 'scared' && t1.expression === 'scared', JSON.stringify(t1));
check('未知动作回落 idle', mapAction({ channel: 'action', type: 'play_motion', ts: 1, data: { motion: 'zzz' } }).motion === 'idle');
const t3 = mapAction({ channel: 'action', type: 'speak', ts: 1, data: { text: '欢迎' } });
check('speak 带默认音色+同步气泡', !!t3.speak && t3.speak.voice === 'zh-CN-XiaoyiNeural' && t3.bubble.text === '欢迎');
const t4 = mapAction({ channel: 'action', type: 'stop', ts: 1, data: {} });
check('stop -> idle + 清气泡（闭嘴/休息）', t4.stop === true && t4.motion === 'idle' && t4.clearBubble === true);
const t5 = mapAction({ channel: 'action', type: 'show_bubble', ts: 1, data: { text: 'hi', duration_ms: 1000 } });
check('show_bubble 文本+时长', t5.bubble.text === 'hi' && t5.bubble.duration === 1000);
// 纯文字气泡是弹幕回复的主通道，缺省停留 8 秒；跟后端 dialogue.py 的 BUBBLE_MS 对齐。
const t6 = mapAction({ channel: 'action', type: 'show_bubble', ts: 1, data: { text: 'hi' } });
check('show_bubble 缺省停留 8 秒', t6.bubble.duration === 8000, String(t6.bubble.duration));
check('非 action 频道被忽略', mapAction({ channel: 'perception', type: 'x', ts: 1, data: {} }).ignored === true);

// 2) edge-tts 出声
function ttsCmd() {
  const exe = path.resolve(__dirname, '..', '..', '.venv', 'Scripts', 'edge-tts.exe');
  if (fs.existsSync(exe)) return { cmd: exe, pre: [] };
  return { cmd: path.resolve(__dirname, '..', '..', '.venv', 'Scripts', 'python.exe'), pre: ['-m', 'edge_tts'] };
}
const out = path.join(__dirname, '.cache', 'verify-tts.mp3');
fs.mkdirSync(path.dirname(out), { recursive: true });
const t = ttsCmd();
const r = spawnSync(t.cmd, [...t.pre, '--voice', 'zh-CN-XiaoyiNeural', '--text', '测试语音，欢迎来到直播间', '--write-media', out], { encoding: 'buffer' });
const okTTS = r.status === 0 && fs.existsSync(out) && fs.statSync(out).size > 1000;
check('edge-tts 生成有效 mp3', okTTS, okTTS ? `${fs.statSync(out).size} bytes` : (r.stderr ? r.stderr.toString('utf8').slice(0, 120) : 'no file'));

console.log(`\n==== 表现层逻辑验证: ${pass}/${pass + fail} 通过 ====`);
process.exit(fail ? 1 : 0);
