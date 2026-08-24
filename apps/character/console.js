/* 控制台窗口的渲染逻辑。所有对外动作都经 preload 暴露的 consoleAPI 走主进程，
   渲染进程本身不碰 net / child_process。 */
(function () {
  'use strict';
  // preload 没挂上时退化成空实现：宁可按钮点了没反应，也不要整个窗口白屏没法排查。
  const NOOP = {
    publish: async () => ({ ok: false, error: 'preload 未加载' }),
    voiceStart: async () => ({ ok: false }), voiceStop: async () => ({ ok: false }),
    streamModeGet: async () => null, streamModeSet: async () => ({ ok: false, error: 'preload 未加载' }),
    offscreenGet: async () => null, offscreenSet: async () => ({ ok: false }),
    onBus() {}, onBusState() {}, onVoiceState() {}, onVoiceLog() {},
  };
  const API = window.consoleAPI || NOOP;

  const $ = (id) => document.getElementById(id);
  const chatlog = $('chatlog');
  const chatform = $('chatform');
  const chatinput = $('chatinput');
  const chatsend = $('chatsend');
  const voicebtn = $('voicebtn');
  const voicesub = $('voicesub');
  const heard = $('heard');
  const walkbtn = $('walkbtn');

  // 动作区：与控制面板 GUI_GROUPS 的"动作"组保持一致（brain 的 command.do 直接吃 motion 名）
  const MOTIONS = [
    ['挥手', 'wave'], ['被吓', 'scared'], ['大笑', 'laugh'],
    ['谢礼', 'thank_small'], ['答谢', 'thank_big'], ['夸夸', 'praise'],
    ['撒娇', 'beg'],
  ];
  // 状态区：走各自的 command.*，不是 do
  const STATES = [
    ['闭嘴', 'mute'], ['恢复', 'unmute'], ['休息', 'sleep'], ['唤醒', 'wake'],
  ];

  // ---- 聊天记录 ----
  function addMsg(kind, text, who) {
    const el = document.createElement('div');
    el.className = `msg ${kind}`;
    if (who) {
      const w = document.createElement('span');
      w.className = 'who';
      w.textContent = who;
      el.appendChild(w);
    }
    el.appendChild(document.createTextNode(text));
    chatlog.appendChild(el);
    chatlog.scrollTop = chatlog.scrollHeight;
    return el;
  }

  // ---- 弹幕断流告警条 ----
  function setDanmakuAlert(text) {
    const el = $('danmaku-alert');
    if (!el) return;
    if (text) {
      el.textContent = text;
      el.hidden = false;
    } else {
      el.hidden = true;
    }
  }

  // ---- 状态灯 ----
  function setChip(id, state) {
    const el = $(id);
    el.classList.toggle('on', state === 'on');
    el.classList.toggle('busy', state === 'busy');
  }

  // ---- 打字聊天 ----
  // dialogue 服务订阅 perception.audio.command，intent=chat 的走自由聊天。
  // 打字和语音最终进的是同一条链路，所以这里伪造一条 speaker_verified 的 audio.command，
  // 而不是另开一种消息类型——避免 dialogue 那边为"打字"单开一个分支。
  chatform.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = chatinput.value.trim();
    if (!text) return;
    addMsg('me', text, '我');
    chatinput.value = '';
    API.publish({
      channel: 'perception', type: 'audio.command', ts: Date.now(), source: 'console',
      data: { intent: 'chat', raw_text: text, speaker_verified: true },
    });
  });

  // ---- 语音开关 ----
  voicebtn.addEventListener('click', async () => {
    const on = voicebtn.dataset.on === 'true';
    voicebtn.disabled = true;
    if (on) {
      await API.voiceStop();
    } else {
      heard.textContent = '正在启动（首次要加载语音模型，可能十几秒）…';
      setChip('chip-voice', 'busy');
      await API.voiceStart();
    }
    voicebtn.disabled = false;
  });

  API.onVoiceState((s) => {
    const on = !!s.running;
    voicebtn.dataset.on = String(on);
    voicebtn.querySelector('.big-label').textContent = on ? '关闭语音识别' : '开启语音识别';
    voicesub.textContent = on ? `喊一声「${s.wakeWord || '魔丸'}」开始说话` : '用麦克风跟她说话';
    setChip('chip-voice', on ? 'on' : 'off');
    if (!on) heard.textContent = '—';
  });

  // 语音子进程的 stdout 直接透传上来，挑有用的显示
  API.onVoiceLog((line) => {
    const t = String(line).trim();
    if (!t) return;
    if (t.includes('听到：')) {
      heard.textContent = t.slice(t.indexOf('听到：') + 3);
    } else if (t.includes('非主播声音')) {
      heard.textContent = '（听到别人说话，已忽略）';
    } else if (t.includes('未注册主播声纹')) {
      heard.textContent = '没有声纹文件，需要先注册主播声音';
    } else if (t.includes('开始监听')) {
      heard.textContent = '在听了，说话试试';
    }
  });

  // ---- 麦克风测试 / 声纹重录 ----
  // mic_tool.py 每行一个 JSON 事件，这里按事件类型驱动界面：显示要读的句子、实时电平、结果。
  const micnote = $('micnote');
  const mictest = $('mictest');
  const micenroll = $('micenroll');
  const modal = $('micmodal');
  const mmstep = $('mmstep');
  const mmsay = $('mmsay');
  const mmcount = $('mmcount');
  const mmbar = $('mmbar');
  const mmnote = $('mmnote');
  const mmstop = $('mmstop');

  function micBusy(busy) { mictest.disabled = busy; micenroll.disabled = busy; }
  function note(text, cls) {
    micnote.textContent = text;
    micnote.className = 'mic-note' + (cls ? ` ${cls}` : '');
  }
  function mm(text, cls) {
    mmnote.textContent = text;
    mmnote.className = 'mm-note' + (cls ? ` ${cls}` : '');
  }
  function openModal(step, say, tip) {
    modal.hidden = false;
    mmstep.textContent = step;
    mmsay.textContent = say;
    mmcount.textContent = '';
    mmbar.style.width = '0%';
    mm(tip || '');
    mmstop.textContent = '停止';
  }
  function closeModal() { modal.hidden = true; }

  async function start(mode) {
    micBusy(true);
    note('');
    openModal(mode === 'enroll' ? '声纹重录' : '麦克风测试', '准备中…', '正在启动…');
    const r = await API.micRun(mode);
    if (!r.ok) { mm(r.error || '启动失败', 'bad'); mmstop.textContent = '关闭'; micBusy(false); }
  }
  mictest.addEventListener('click', () => start('test'));
  micenroll.addEventListener('click', () => start('enroll'));

  // 任何时候都能中断——上一版没有停止入口，用户以为"一直开着停不下来"。
  mmstop.addEventListener('click', async () => {
    await API.micStop();
    closeModal();
    micBusy(false);
  });

  API.onMicEvent((e) => {
    switch (e.ev) {
      case 'device':
        mm(`麦克风：${e.name}`);
        break;
      case 'prompt':
        mmstep.textContent = e.index ? `第 ${e.index} / ${e.total} 句` : '请说一句话';
        mmsay.textContent = e.text;
        mmcount.textContent = '准备…';
        mm('看清上面这句，马上开始录');
        break;
      case 'recording':
        mm('现在读出来 👄');
        break;
      case 'level': {
        // 峰值 0~32768。正常说话大概几千，按 8000 满格更直观；接近满格说明快削波了。
        const pct = Math.min(100, Math.round((e.peak / 8000) * 100));
        mmbar.style.width = `${pct}%`;
        if (typeof e.remain === 'number') mmcount.textContent = `${e.remain.toFixed(1)}s`;
        break;
      }
      case 'recorded':
        mmbar.style.width = '0%';
        mmcount.textContent = '';
        if (e.weak) mm(`这句几乎没声音（峰值 ${e.peak}），稍后会让你重录`, 'bad');
        else if (e.clipped) mm(`这句爆音了（峰值 ${e.peak}），说话离麦远一点`, 'bad');
        else mm('这句收到了 ✓', 'good');
        break;
      case 'result': {
        mmstep.textContent = '测试完成';
        mmsay.textContent = e.ok ? '结果如下' : '没录到声音';
        mmcount.textContent = '';
        mmstop.textContent = '关闭';
        const lines = e.ok ? [
          `峰值音量 ${e.peak} / 32768`,
          e.sim === null ? '还没有声纹文件'
            : `声纹相似度 ${e.sim}（阈值 ${e.threshold}）→ ${e.sim >= e.threshold ? '通过' : '不通过'}`,
          `听成：${e.text || '（没听清）'}`,
        ] : [e.reason];
        mm(lines.join('\n'), e.ok && e.sim !== null && e.sim >= e.threshold ? 'good' : 'bad');
        note(lines.join('\n'));
        break;
      }
      case 'backup':
        note(`旧声纹已备份：${e.path}`);
        break;
      case 'done': {
        mmstep.textContent = e.ok ? '完成' : '未更新';
        mmsay.textContent = e.ok ? '声纹已更新' : '声纹没有更新';
        mmcount.textContent = '';
        mmstop.textContent = '关闭';
        const msg = e.ok
          ? `用 ${e.count} 段有效录音重建\n自检相似度 最低 ${e.selfsim_min} / 平均 ${e.selfsim_avg}`
          : e.reason;
        mm(msg, e.ok ? 'good' : 'bad');
        note(msg, e.ok ? 'good' : 'bad');
        break;
      }
      case 'error':
        mmstep.textContent = '出错了';
        mmsay.textContent = '没能完成';
        mmstop.textContent = '关闭';
        mm(e.message, 'bad');
        break;
      case 'exit':
        micBusy(false);
        mmbar.style.width = '0%';
        mmcount.textContent = '';
        mmstop.textContent = '关闭';
        break;
      default:
        break;
    }
  });

  // ---- 卡关攻略 ----
  // 发 command.do{action:walkthrough}：perception-game 的 _is_walkthrough 认这条，
  // 收到后截当前画面交给多模态模型分析，结论以 game.scene 回到总线、由 brain 让桌宠说出来。
  // 卡关求助：把主播打的那句一起送过去。**这句话是搜索质量的主要来源**——游戏名能自动认，
  // 但"第几关、在哪个房间、上一个任务是什么、什么东西找不到"只有他自己说得出来。
  const walkinput = $('walkinput');
  const walknote = $('walknote');

  function askWalkthrough() {
    const note = (walkinput.value || '').trim();
    addMsg('sys', note ? `已让她去找攻略：${note}` : '已让她看一眼画面去找攻略…');
    walknote.textContent = '快切回游戏画面！5 秒后才截图（不然截到的是这个控制台）。'
      + '之后找攻略要十几秒，她会直接说出来，气泡也会多留半分钟给你回头看。';
    walknote.className = 'mic-note';
    API.publish({
      channel: 'command', type: 'do', ts: Date.now(), source: 'console',
      data: note ? { action: 'walkthrough', note } : { action: 'walkthrough' },
    });
    walkinput.value = '';
  }

  walkbtn.addEventListener('click', askWalkthrough);
  // 打完直接回车就发，不用再去够按钮——卡关时手还在键盘上
  walkinput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); askWalkthrough(); }
  });

  // ---- 本场观众 · 勾会员/星守护 ----
  // 为什么必须手动勾：2026-07-30 真实抓包核实过，观众数据里**没有任何字段**能标出会员或
  // 星守护（PayLevel 是 0~40 的财富等级，不是会员标志）。灯牌等级倒是自带的，所以灯牌
  // 只展示不勾——后端按 ≥8 级自动算 VIP。
  // 名单来源就是总线上已经在收的弹幕/进场/送礼事件，不用另开接口。
  const viewerlist = $('viewerlist');
  const viewers = new Map();          // 昵称 -> { tier, fansclub, seen }
  let viewerRefresh = null;           // 重画节流句柄
  const TIERS = [['普通', 'normal'], ['会员', 'member'], ['星守护', 'star_guardian']];

  function renderViewers() {
    if (!viewerlist) return;
    viewerlist.textContent = '';
    // 勾过的排前面，其余按最近出现排序——主播要找的多半是刚说话的人
    const rows = [...viewers.entries()].sort((a, b) => {
      const w = (x) => (x[1].tier !== 'normal' ? 0 : 1);
      return w(a) - w(b) || b[1].seen - a[1].seen;
    });
    for (const [name, info] of rows) {
      const row = document.createElement('div');
      row.className = 'vrow';
      const n = document.createElement('span');
      n.className = 'vname'; n.textContent = name;
      row.appendChild(n);
      const fc = document.createElement('span');
      fc.className = 'vfc';
      fc.textContent = info.fansclub ? `灯牌${info.fansclub}` : '';
      if (info.fansclub >= 8) fc.setAttribute('data-vip', 'true');
      row.appendChild(fc);
      for (const [label, tier] of TIERS) {
        const b = document.createElement('button');
        b.type = 'button'; b.textContent = label;
        b.dataset.on = String(info.tier === tier);
        b.addEventListener('click', () => {
          info.tier = tier;
          // source 缺省即 manual。手动打标是最终裁决，后端不会被自动判定覆盖。
          API.publish({
            channel: 'command', type: 'set_viewer_tier', ts: Date.now(), source: 'console',
            data: { nickname: name, tier },
          });
          renderViewers();
        });
        row.appendChild(b);
      }
      viewerlist.appendChild(row);
    }
  }

  function noteViewer(name, fansclub) {
    if (!name) return;
    const cur = viewers.get(name) || { tier: 'normal', fansclub: 0, seen: 0 };
    cur.seen = Date.now();
    if (fansclub) cur.fansclub = Math.max(cur.fansclub, fansclub);
    viewers.set(name, cur);
  }

  // ---- 直播采集：绿幕模式 ----
  // 为什么是"重启"而不是"切换"：`transparent` 是窗口构造参数，硬件加速开关必须在 app ready
  // 之前调用，运行时都改不了。所以按钮上明写会重启，不装作是无感切换。
  const chromabtn = $('chromabtn');
  const chromalabel = $('chromalabel');
  const chromasub = $('chromasub');
  const chromanote = $('chromanote');

  function chromaNote(text, cls) {
    chromanote.textContent = text;
    chromanote.className = 'mic-note' + (cls ? ` ${cls}` : '');
  }

  async function renderChromaMode() {
    const s = await API.streamModeGet();
    if (!s) return;                      // preload 没挂上：按钮留在默认文案，不报错
    chromabtn.dataset.on = String(!!s.on);
    chromalabel.textContent = s.on ? '回到普通模式' : '切到绿幕模式';
    chromasub.textContent = s.on
      ? `绿底 ${s.chroma} · 直播伴侣用「窗口捕获」+ 绿幕抠图`
      : '给直播采集用，切换会重启桌宠';
    chromabtn.disabled = !!s.grabOn;
    if (s.grabOn) chromaNote('开播中不能切换：重启会连带重装抓包证书、重设系统代理。下播后再切。', 'bad');
    else chromaNote('');
  }

  if (chromabtn) {
    chromabtn.addEventListener('click', async () => {
      const s = await API.streamModeGet();
      if (!s) return;
      chromabtn.disabled = true;
      chromaNote('正在重启桌宠…');
      const r = await API.streamModeSet(!s.on);
      if (!r || !r.ok) {                 // 成功的话进程已经在退出了，走不到这里
        chromabtn.disabled = false;
        chromaNote((r && r.error) || '切换失败', 'bad');
      }
    });
    renderChromaMode();
  }

  // ---- 移出视野 ----
  // 只是挪窗口位置，即时生效不用重启（位置不像 transparent，运行时能改）。
  // 「窗口捕获」读的是窗口自己的画面，所以挪出视野不影响观众看到。
  const hidebtn = $('hidebtn');
  const hidelabel = $('hidelabel');

  async function renderHidden() {
    const s = await API.offscreenGet();
    if (!s) return;
    hidebtn.dataset.on = String(!!s.on);
    hidelabel.textContent = s.on ? '把桌宠移回屏幕' : '把桌宠移出视野';
  }

  if (hidebtn) {
    hidebtn.addEventListener('click', async () => {
      const s = await API.offscreenGet();
      if (!s) return;
      const r = await API.offscreenSet(!s.on);
      if (r && r.ok) {
        hidebtn.dataset.on = String(!!r.on);
        hidelabel.textContent = r.on ? '把桌宠移回屏幕' : '把桌宠移出视野';
      }
    });
    renderHidden();
  }

  // ---- 动作 / 状态按钮 ----
  function build(container, items, make) {
    items.forEach(([label, key]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.addEventListener('click', () => API.publish(make(key)));
      container.appendChild(b);
    });
  }
  build($('motions'), MOTIONS, (motion) => ({
    channel: 'command', type: 'do', ts: Date.now(), source: 'console', data: { action: motion },
  }));
  build($('states'), STATES, (intent) => ({
    channel: 'command', type: intent, ts: Date.now(), source: 'console', data: {},
  }));

  // ---- 手动下播：command.stream_end 目前唯一的触发入口，08-01 真开播发现真实抓包数据
  // 里从来没有 102(下播) 事件（主播全程靠关控制台结束，没走"直播伴侣里点结束直播"那条路），
  // 档案总结/观众场次记录因此一次都没真的跑过。不用语音"下播"这个词触发（主播明确说
  // 直播时开玩笑可能随口提到这两个字，怕误触发），改成这个按钮，点一次才算一次，
  // 需要二次确认防手滑。
  $('streamendbtn').addEventListener('click', () => {
    if (!confirm('确定要下播吗？会真实总结本场主播档案、给观众记一场。\n中途重启修bug、不是真的播完了，不要点这个。')) return;
    API.publish({
      channel: 'command', type: 'stream_end', ts: Date.now(), source: 'console', data: {},
    });
    addMsg('sys', '已手动触发下播：主播档案总结 + 观众场次记录进行中…');
  });

  // ---- 总线回流：把桌宠说的话显示在对话区 ----
  API.onBus((msg) => {
    if (msg.channel === 'action' && msg.type === 'speak') {
      addMsg('pet', msg.data.text, '魔丸');
    } else if (msg.channel === 'action' && msg.type === 'show_bubble') {
      addMsg('pet', msg.data.text, '魔丸');
    } else if (msg.channel === 'perception' && msg.type === 'danmaku.health') {
      // 弹幕断流告警。2026-08-02 真开播时弹幕断了 58 分钟主播全程不知情，这条就是为了
      // 不再发生那种事——**必须显眼**，所以除了写进对话区还要把状态灯打红。
      const d = msg.data || {};
      const mins = Math.round((d.silent_ms || 0) / 60000);
      const rec = String(d.recovery || '');
      if (d.ok) {
        addMsg('sys', `✔ 弹幕恢复了（断了约 ${mins} 分钟）`);
        setDanmakuAlert(null);
      } else if (rec === 'trying') {
        addMsg('sys', `⚠ 约 ${mins} 分钟没弹幕了，正在自动重启抓包程序抢救…`);
        setDanmakuAlert(`⚠ 弹幕断流 ${mins} 分钟 · 正在自动抢救…`);
      } else if (rec === 'ok') {
        addMsg('sys', '抓包程序已重启，等直播伴侣重连——真收到弹幕才会报恢复。');
        setDanmakuAlert('⚠ 已重启抓包程序 · 等直播伴侣重连');
      } else if (rec.startsWith('failed:')) {
        addMsg('sys', `⚠ 自动抢救没成功：${rec.slice(7)}`);
        setDanmakuAlert(`⚠ 弹幕断流 ${mins} 分钟 · 自动抢救失败`);
      } else if (rec === 'gaveup') {
        addMsg('sys', '⚠ 自动重启试过几次都没救回来，已停止重试（继续重启只会反复打断直播伴侣）。请手动看一眼 BarrageGrab 那个黑窗口。');
        setDanmakuAlert(`⚠ 弹幕断流 ${mins} 分钟 · 自动抢救已放弃，需要手动处理`);
      } else {
        const why = d.connected
          ? '抓包程序的连接还在，但它不往外送数据了'
          : '到抓包程序的连接已经断开，看看那个黑窗口还在不在';
        addMsg('sys', `⚠ 已经约 ${mins} 分钟没收到任何弹幕了。${why}`);
        setDanmakuAlert(`⚠ 弹幕断流约 ${mins} 分钟`);
      }
    } else if (msg.channel === 'perception' && String(msg.type).startsWith('danmaku.')) {
      // 顺手把出现过的人记进名单。刷新有节流：爆火时弹幕每秒几十条，每条都重画会卡住控制台。
      noteViewer(msg.data && msg.data.user, (msg.data && msg.data.fansclub_level) || 0);
      if (!viewerRefresh) viewerRefresh = setTimeout(() => { viewerRefresh = null; renderViewers(); }, 1500);
    } else if (msg.channel === 'perception' && msg.type === 'game.scene') {
      // 攻略的出处显示在这里。**能核对是这条链路存在的意义**——她念出来的每句话都该
      // 追得到来源，主播想细看就自己点开。搜不到时不会有 sources，也就不会显示。
      const src = (msg.data && msg.data.sources) || [];
      if (src.length) {
        walknote.textContent = '攻略来源（可自己点开核对）：';
        walknote.className = 'mic-note good';
        addMsg('sys', `找到 ${src.length} 篇攻略：\n` +
          src.map((s, i) => `${i + 1}. ${s.title}\n   ${s.url}`).join('\n'));
      } else if (msg.data && msg.data.hint) {
        walknote.textContent = '';
      }
    }
  });

  API.onBusState((s) => {
    setChip('chip-bus', s.connected ? 'on' : 'off');
    setChip('chip-chat', s.dialogue ? 'on' : 'off');
    chatsend.disabled = !s.connected;
  });

  addMsg('sys', '控制台就绪。打字或开麦都能跟她说话。');
}());
