'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let now = 0;
let nextTimerId = 1;
const timers = new Map();
function fakeSetTimeout(callback, delay = 0) {
  const id = nextTimerId++;
  timers.set(id, { at: now + Number(delay), callback });
  return id;
}
function fakeClearTimeout(id) { timers.delete(id); }
function advance(ms) {
  const target = now + ms;
  while (true) {
    const ready = [...timers.entries()]
      .filter(([, timer]) => timer.at <= target)
      .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
    if (!ready) break;
    const [id, timer] = ready;
    timers.delete(id);
    now = timer.at;
    timer.callback();
  }
  now = target;
}

class ClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  contains(value) { return this.values.has(value); }
}
class Style {
  constructor() { this.values = new Map(); }
  setProperty(name, value) { this.values.set(name, value); }
}
class Element {
  constructor() {
    this.attributes = new Map();
    this.classList = new ClassList();
    this.style = new Style();
    this.textContent = '';
    this._src = '';
  }
  set src(value) { this._src = value; this.attributes.set('src', value); }
  get src() { return this._src; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) || ''; }
  removeAttribute(name) { this.attributes.delete(name); }
  querySelector() { return null; }
  appendChild() {}
}

const stage = new Element();
const root = new Element();
const character = new Element();
const layers = [new Element(), new Element()];
character.querySelector = (selector) => selector.includes('"a"') ? layers[0] : layers[1];
const bubble = new Element();
const bubbleText = new Element();
const voice = new Element();
voice.pause = () => {};
voice.play = async () => {};
const elements = { stage, 'pet-root': root, character, bubble, 'bubble-text': bubbleText, voice };
const document = {
  getElementById: (id) => elements[id] || null,
  addEventListener: () => {},
  createElement: () => new Element(),
};
class PreloadImage { set src(value) { this._src = value; } }

const registry = require('./state-registry.js');
const actionMap = require('./action-map.js');
const deterministicMath = Object.create(Math);
deterministicMath.random = () => 0;
const window = { PetStateRegistry: registry, ActionMap: actionMap, petAPI: null };
const context = {
  window,
  document,
  Image: PreloadImage,
  Math: deterministicMath,
  console,
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
};
const rendererPath = path.join(__dirname, 'renderer.js');
vm.runInNewContext(fs.readFileSync(rendererPath, 'utf8'), context, { filename: rendererPath });

const activeSrc = () => layers[0].src;
const action = (type, data = {}) => window.__injectAction({ channel: 'action', type, ts: now, data });

assert.ok(activeSrc().endsWith('/idle.png'));
advance(2400);
assert.ok(activeSrc().endsWith('/idle_8.png'), activeSrc());
assert.equal(bubbleText.textContent, '好吃好吃');
advance(2600);
assert.equal(stage.getAttribute('data-state'), 'idle_sleep');
assert.ok(activeSrc().endsWith('/idle_sleep_2.png'), activeSrc());
advance(6000);
assert.equal(stage.getAttribute('data-state'), 'idle');

action('play_motion', { motion: 'scared' });
advance(480);
assert.ok(activeSrc().endsWith('/scared_3.png'), activeSrc());
assert.equal(bubbleText.textContent, '吓死宝宝了');
assert.equal(bubble.getAttribute('data-tone'), 'fear');
advance(5200);
assert.equal(stage.getAttribute('data-state'), 'idle');

action('play_motion', { motion: 'beg' });
advance(1400);
assert.ok(activeSrc().endsWith('/beg_5.png'), activeSrc());
assert.equal(bubble.getAttribute('data-mode'), 'heart-grow');
assert.equal(bubble.getAttribute('data-anchor-target'), 'wink-eye');
assert.equal(bubble.getAttribute('data-phase'), 'growing');
assert.equal(bubbleText.textContent, '');
advance(649);
assert.ok(activeSrc().endsWith('/beg_5.png'), activeSrc());
assert.equal(bubbleText.textContent, '');
advance(1);
assert.equal(bubble.getAttribute('data-phase'), 'revealed');
assert.equal(bubbleText.textContent, '爱你哟！');
advance(950);
assert.equal(stage.getAttribute('data-state'), 'idle');

action('set_expression', { expression: 'smug' });
advance(750);
assert.equal(stage.getAttribute('data-state'), 'idle_smug');
assert.equal(bubbleText.textContent, '嘿嘿，主人又被我拿捏啦！');
advance(2250);
assert.equal(stage.getAttribute('data-state'), 'idle');

action('play_motion', { motion: 'sleep' });
advance(1600);
assert.equal(stage.getAttribute('data-state'), 'sleep');
assert.equal(bubble.getAttribute('data-mode'), 'snore-cycle');
advance(12000);
assert.equal(stage.getAttribute('data-state'), 'sleep');
action('play_motion', { motion: 'idle' });
assert.equal(stage.getAttribute('data-state'), 'idle');

assert.deepEqual(Object.keys(registry.states).sort(), [
  'beg', 'idle', 'idle_happy', 'idle_sleep', 'idle_smug', 'idle_surprised',
  'laugh', 'praise', 'scared', 'sleep', 'thank_big', 'thank_small', 'wave',
].sort());

// 语音排队播放：brain 的即时反应和 dialogue 追加的 LLM 回复几乎同时到达时，
// 后一句不该立刻顶掉前一句的音频/气泡——应该排队，等前一句播完再接上。
// 用 __onSpeakStart 钩子记录"真正开始播放"的时刻，不用 bubbleText——idle 自身在
// 第 8 帧会有一次性的"好吃好吃"气泡，跟这里测的排队逻辑无关，用气泡文本判断会被
// 那次无关的气泡覆盖干扰。
const speakStarts = [];
window.__onSpeakStart = (text) => speakStarts.push({ text, at: now });

action('speak', { text: '你好呀' });
assert.deepEqual(speakStarts, [{ text: '你好呀', at: now }], '第一句应立刻开始播放');
action('speak', { text: '谢谢你' });
assert.equal(speakStarts.length, 1, '排队中的第二句不该立刻开始播放');
advance(960); // 600 + '你好呀'.length(3) * 120
assert.equal(speakStarts.length, 2, '第一句播完后应自动接上排队的第二句');
assert.equal(speakStarts[1].text, '谢谢你');
assert.equal(speakStarts[1].at, speakStarts[0].at + 960, '第二句应紧接第一句播完后开始，不提前也不无故延后');

// stop（闭嘴/休息）应该清空排队，不能让排队中的话在 stop 之后又冒出来。
// 此时上一句"谢谢你"其实还在播（它的 done 要到 at+960 才触发），所以这两句会先排队。
assert.equal(speakStarts.length, 2);
action('speak', { text: '第一句' });
action('speak', { text: '第二句' });
assert.equal(speakStarts.length, 2, '"谢谢你"还没播完，这两句应该排队而不是立刻播放');
action('stop', {});
advance(3000);
assert.equal(speakStarts.length, 2, 'stop 应清空排队，"第一句"/"第二句"不该在之后冒出来播放');

// 掐麦回报：只有整个队列都播完了才告诉 main「说完了」。
// 中间每句缝里都解除的话，音箱余响会被麦克风收回去——2026-07-29 实测过的自问自答死循环。
// 这里给的 petAPI 故意只有 speakDone 没有 speak，走同步兜底路径，配合假时钟好断言。
let doneCount = 0;
window.petAPI = { speakDone: () => { doneCount += 1; } };
action('speak', { text: '一' });          // 兜底时长 600 + 1*120 = 720ms
action('speak', { text: '二' });
assert.equal(doneCount, 0, '还在说的时候不该回报"说完了"');
advance(720);
assert.equal(doneCount, 0, '第一句播完但队列还没空，仍然不该回报（否则句缝里会收进余响）');
advance(720);
assert.equal(doneCount, 1, '队列排空后回报一次"说完了"');

// 预取：排队的下一条要在当前这条还没播完时就开始合成。
// 实测合成一句 2.6~4.8 秒且与文本长短无关，串行做的话每条都要干等一次。
const synthCalls = [];
window.petAPI = {
  speak: (text) => { synthCalls.push(text); return new Promise(() => {}); },  // 永不 resolve：模拟合成还在进行中
  speakDone: () => {},
};
action('speak', { text: '甲' });
assert.deepEqual(synthCalls, ['甲'], '第一句应立刻开始合成');
action('speak', { text: '乙' });
assert.deepEqual(synthCalls, ['甲', '乙'], '排队的第二句应在第一句还没播完时就开始合成（预取）');
action('speak', { text: '丙' });
assert.deepEqual(synthCalls, ['甲', '乙'], '只预取队首一条，不同时开一堆合成进程');

// ── 气泡队列 ────────────────────────────────────────────────────────────────
// 2026-07-30 起观众弹幕大部分回成纯文字气泡、不出声（一直有语音会盖过直播本身），
// show_bubble 从"偶尔用一下"变成主力显示通道，必须扛得住两条前后脚到达。
window.petAPI = null;
action('stop', {});   // 清掉上一段留下的排队

const bubbleStarts = [];
window.__onBubbleStart = (text) => bubbleStarts.push({ text, at: now });

action('show_bubble', { text: '第一条弹幕回复' });
assert.equal(bubbleText.textContent, '第一条弹幕回复');
action('show_bubble', { text: '第二条弹幕回复' });
assert.equal(bubbleStarts.length, 1, '前后脚到达的第二条应排队，不能顶掉第一条');
assert.equal(bubbleText.textContent, '第一条弹幕回复');
advance(7999);
assert.equal(bubbleText.textContent, '第一条弹幕回复', '缺省停留 8 秒，不到点不换');
assert.ok(bubble.classList.contains('show'),
  '这 8 秒里待机状态会来回切（idle↔idle_sleep），切状态不能把观众正在读的回复收掉');
advance(1);
assert.deepEqual(bubbleStarts.map((item) => item.text), ['第一条弹幕回复', '第二条弹幕回复']);
assert.equal(bubbleStarts[1].at, bubbleStarts[0].at + 8000, 'show_bubble 缺省时长是 8 秒');

// 拥堵降时长：排队还剩超过 2 条时压到 5 秒，让画面跟得上正在刷的弹幕。
action('stop', {});
bubbleStarts.length = 0;
for (const text of ['甲甲', '乙乙', '丙丙', '丁丁', '戊戊']) action('show_bubble', { text });
advance(8000);   // 甲甲 开始显示时队列还是空的，走满 8 秒
assert.equal(bubbleText.textContent, '乙乙');
advance(4999);
assert.equal(bubbleText.textContent, '乙乙', '缩短了也不能提前换，读不完一样白搭');
advance(1);
assert.equal(bubbleText.textContent, '丙丙', '乙乙 接手时后面还排着 3 条（>2），停留时间应缩到 5 秒');

// 显式 duration_ms 优先于缺省值。
action('stop', {});
action('show_bubble', { text: '短的', duration_ms: 1200 });
action('show_bubble', { text: '后一条' });
advance(1199);
assert.equal(bubbleText.textContent, '短的');
advance(1);
assert.equal(bubbleText.textContent, '后一条', '显式 duration_ms 应被采用，而不是套缺省的 8 秒');

// 跟语音队列的协调：两者共用同一个气泡框，所以排的是同一条队。
action('stop', {});
action('speak', { text: '正在念' });                  // 无 petAPI，兜底 600 + 3*120 = 960ms
action('show_bubble', { text: '语音后面才轮到我' });
assert.equal(bubbleText.textContent, '正在念', '语音在播时来的文字回复要等，不能顶掉正在念的那句');
advance(959);
assert.equal(bubbleText.textContent, '正在念');
advance(1);
assert.equal(bubbleText.textContent, '语音后面才轮到我', '语音播完后应接上排队的文字气泡');

// 反方向同理：文字气泡挂着的时候来的语音也要等。
action('stop', {});
const speakCountBefore = speakStarts.length;
action('show_bubble', { text: '先来的文字回复', duration_ms: 3000 });
action('speak', { text: '后到的语音' });
assert.equal(speakStarts.length, speakCountBefore, '文字气泡还在显示时，语音应排队');
advance(3000);
assert.equal(speakStarts.length, speakCountBefore + 1, '文字气泡到点后语音才开口');

// 待机提示：告诉观众队列上限和怎么才会被回复，逐条轮换而不是一句重复到底。
action('stop', {});
const idleBubble = registry.getState('idle').bubble;
assert.equal(idleBubble.rotateTexts, true, '待机文案要顺序轮换，随机抽会让关键提示长时间不露面');
assert.ok(idleBubble.texts.some((text) => text.includes('15') && text.includes('本公主')),
  '待机文案里要有一条讲清楚弹幕队列上限');
const seenIdleTexts = new Set();
for (let round = 0; round < idleBubble.texts.length; round += 1) {
  action('play_motion', { motion: 'idle' });
  advance(2400);   // idle 进入段走完，第 8 帧的待机气泡冒出来
  seenIdleTexts.add(bubbleText.textContent);
}
assert.equal(seenIdleTexts.size, idleBubble.texts.length, '连续几次待机应把提示逐条轮完，不重复同一条');

console.log(JSON.stringify({
  ok: true,
  checks: [
    'idle enters once then loops eating frames',
    'idle_sleep selects one independent pose',
    'scared freezes its close-up and shows copy',
    'beg heart appears after wink',
    'expression copy returns to idle',
    'explicit sleep persists until idle/wake',
    'all 13 states are registered',
    'queued speak plays sequentially, not interrupting',
    'stop clears the pending speak queue',
    'speak-done is reported only after the whole queue drains (mic gate)',
    'next queued line is synthesized while the current one plays (prefetch)',
    'a second show_bubble queues instead of clobbering the first',
    'show_bubble holds for 8s by default and survives state changes',
    'bubble hold shrinks to 5s once more than two are queued',
    'explicit duration_ms wins over the default hold',
    'speech and text bubbles share one queue in both directions',
    'idle hints rotate through the viewer-facing copy',
  ],
}));
