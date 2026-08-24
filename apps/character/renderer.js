/* Runtime renderer for transparent frame animation plus DOM-only speech/effect bubbles. */
(function () {
  const stage = document.getElementById('stage');
  const root = document.getElementById('pet-root');
  const character = document.getElementById('character');
  const layers = [character.querySelector('[data-layer="a"]'), character.querySelector('[data-layer="b"]')];
  const bubble = document.getElementById('bubble');
  const bubbleText = document.getElementById('bubble-text');
  const voice = document.getElementById('voice');
  const AM = window.ActionMap;
  const registry = window.PetStateRegistry;
  const IMAGE_DIR = '../../assets/character/';
  const EXPRESSION_STATE = { happy: 'idle_happy', smug: 'idle_smug', surprised: 'idle_surprised' };

  let currentMotion = 'idle';
  let currentExpression = 'neutral';
  let currentState = null;
  let runToken = 0;
  let frameTimer = null;
  let ambientTimer = null;
  let bubbleTimer = null;
  let bubbleCycleTimer = null;
  let bubbleRevealTimer = null;
  let stateBubbleShown = false;
  let stateBubbleTrigger = 1;
  let freezeConsumed = false;

  function frameFile(key, frame) { return frame <= 1 ? `${key}.png` : `${key}_${frame}.png`; }

  function clearTimer(timer) {
    if (timer) clearTimeout(timer);
    return null;
  }

  function stopStateTimers() {
    frameTimer = clearTimer(frameTimer);
    ambientTimer = clearTimer(ambientTimer);
  }

  function clearBubble() {
    bubbleTimer = clearTimer(bubbleTimer);
    bubbleCycleTimer = clearTimer(bubbleCycleTimer);
    bubbleRevealTimer = clearTimer(bubbleRevealTimer);
    bubble.classList.remove('show');
    for (const name of ['data-tone', 'data-source', 'data-mode', 'data-placement', 'data-anchor-target', 'data-phase', 'data-long']) {
      bubble.removeAttribute(name);
    }
  }

  function setBubbleText(text) {
    bubbleText.textContent = text;
    if (String(text).length > 12) bubble.setAttribute('data-long', 'true');
    else bubble.removeAttribute('data-long');
  }

  function applyBubbleAnchor(anchor) {
    const value = anchor || {};
    const placement = value.placement || value.side || value.preset || 'above';
    bubble.setAttribute('data-placement', placement);
    bubble.setAttribute('data-anchor-target', value.target || 'mouth');
    if (bubble.style && bubble.style.setProperty) {
      bubble.style.setProperty('--bubble-x', `${Number(value.offsetX || 0)}px`);
      bubble.style.setProperty('--bubble-y', `${Number(value.offsetY || 0)}px`);
    }
  }

  function showBubble(text, duration, tone = 'default', source = 'event', mode = 'speech', anchor = null, allowEmpty = false) {
    if (!text && !allowEmpty) { clearBubble(); return; }
    bubbleTimer = clearTimer(bubbleTimer);
    setBubbleText(text);
    bubble.setAttribute('data-tone', tone || 'default');
    bubble.setAttribute('data-source', source || 'event');
    bubble.setAttribute('data-mode', mode || 'speech');
    applyBubbleAnchor(anchor);
    bubble.classList.add('show');
    if (duration && duration > 0) bubbleTimer = setTimeout(clearBubble, duration);
  }

  function randomItem(items) {
    if (!Array.isArray(items) || items.length === 0) return '';
    return items[Math.floor(Math.random() * items.length)];
  }

  function resolvedBubbleMode(config, stateConfig) {
    const mode = config.mode || 'speech';
    const effect = stateConfig && stateConfig.playback && stateConfig.playback.effect;
    if (mode === 'motion' && effect && effect.type === 'kiss-heart') return 'kiss';
    return mode;
  }

  function startBubbleCycle(items, intervalMs, token) {
    let index = 0;
    const tick = () => {
      if (token !== runToken || !bubble.classList.contains('show')) return;
      setBubbleText(items[index % items.length]);
      index += 1;
      bubbleCycleTimer = setTimeout(tick, intervalMs);
    };
    bubbleCycleTimer = setTimeout(tick, intervalMs);
  }

  // 待机文案按顺序轮换而不是随机抽：随机抽会连着抽中同一句，而这份文案里有一条
  // "本公主一次只能看 N 条弹幕"的说明必须让观众定期看到——纯随机可能让它长时间不露面。
  const textCursor = {};
  function pickStateText(key, config) {
    const items = config.texts || [];
    if (!config.rotateTexts) return randomItem(items);
    if (!items.length) return '';
    const index = textCursor[key] || 0;
    textCursor[key] = (index + 1) % items.length;
    return items[index];
  }

  function showStateBubble(key, stateConfig, token) {
    const config = stateConfig && stateConfig.bubble;
    if (!config || token !== runToken) return;
    // 待机/动作气泡跟弹幕回复共用同一个气泡框。回复是直播的主通道，它正在显示或
    // 排队时不能被"好吃好吃"这类环境气泡插一脚顶掉。这里不置 stateBubbleShown，
    // 等队列排空后触发帧再转回来时环境气泡自然会补上。
    if (isBusy) return;
    stateBubbleShown = true;
    const mode = resolvedBubbleMode(config, stateConfig);
    const snore = config.snoreTexts || ['Z', 'Zz', 'Zzz'];
    let text = pickStateText(key, config);
    let cycle = null;
    let interval = 850;

    if (mode === 'snore-cycle') {
      text = snore[0];
      cycle = snore;
      interval = 720;
    } else if (mode === 'snore-or-dream-cycle') {
      const dream = randomItem(config.dreamTexts || config.texts);
      cycle = [snore[0], snore[1], snore[2], dream].filter(Boolean);
      text = cycle[0];
      interval = 900;
    }

    if (mode === 'heart-grow') {
      const effect = stateConfig.effect || {};
      const revealDelay = Number(config.revealTextDelayMs || effect.textRevealDelayMs || 650);
      const growDuration = Number(effect.growDurationMs || 480);
      const effectDelay = Number(effect.delayMs || 0);
      showBubble('', config.durationMs, config.tone, 'motion', mode, config.anchor, true);
      bubble.setAttribute('data-phase', 'growing');
      if (bubble.style && bubble.style.setProperty) {
        bubble.style.setProperty('--heart-delay', `${effectDelay}ms`);
        bubble.style.setProperty('--heart-grow-duration', `${growDuration}ms`);
      }
      bubbleRevealTimer = setTimeout(() => {
        if (token !== runToken || !bubble.classList.contains('show')) return;
        setBubbleText(text);
        bubble.setAttribute('data-phase', 'revealed');
      }, Math.max(revealDelay, effectDelay + growDuration));
      return;
    }

    showBubble(text, config.durationMs, config.tone, 'motion', mode, config.anchor);
    if (cycle && cycle.length) startBubbleCycle(cycle, interval, token);
  }

  function renderFrame(key, frame) {
    layers[0].src = IMAGE_DIR + frameFile(key, frame);
    layers[0].classList.add('active');
    layers[1].classList.remove('active');
  }

  function preloadFrames(key, frames) {
    for (const frame of new Set(frames)) {
      const image = new Image();
      image.src = IMAGE_DIR + frameFile(key, frame);
    }
  }

  function durationsFor(playback, phase, frameCount) {
    const value = playback.frameDurationsMs;
    if (Array.isArray(value)) return value;
    if (value && Array.isArray(value[phase])) return value[phase];
    return Array.from({ length: frameCount }, () => 375);
  }

  function selectPlan(key, config) {
    const playback = config.playback || {};
    const mode = playback.mode || 'once';
    if (mode === 'random-variant-loop') {
      const variants = playback.loopVariants || [];
      const params = typeof location !== 'undefined' ? new URLSearchParams(location.search) : null;
      const requested = params ? Number(params.get('variant')) : NaN;
      const index = Number.isInteger(requested) && requested >= 0 && requested < variants.length
        ? requested
        : Math.floor(Math.random() * Math.max(variants.length, 1));
      const variant = variants[index] || { entryFrames: [1], loopFrames: [1], frameDurationsMs: {} };
      return {
        mode,
        entry: variant.entryFrames || [],
        entryDurations: durationsFor(variant, 'entry', (variant.entryFrames || []).length),
        loop: variant.loopFrames || [],
        loopDurations: durationsFor(variant, 'loop', (variant.loopFrames || []).length),
        exit: playback.exitFrames || [],
        durationMs: playback.variantDurationMs || 6000,
      };
    }
    if (mode === 'entry-then-loop' || mode === 'persistent-loop') {
      return {
        mode,
        entry: playback.entryFrames || [],
        entryDurations: durationsFor(playback, 'entry', (playback.entryFrames || []).length),
        loop: playback.loopFrames || [],
        loopDurations: durationsFor(playback, 'loop', (playback.loopFrames || []).length),
      };
    }
    const frames = playback.frameSequence || playback.entryFrames || [1];
    const durations = Array.isArray(playback.frameDurationsMs)
      ? playback.frameDurationsMs
      : durationsFor(playback, 'entry', frames.length);
    return { mode: 'once', frames, durations };
  }

  function allPlanFrames(plan) {
    return [...(plan.frames || []), ...(plan.entry || []), ...(plan.loop || []), ...(plan.exit || [])];
  }

  function playSegment(key, stateConfig, frames, durations, index, token, onComplete) {
    if (token !== runToken || !frames.length) { if (onComplete) onComplete(); return; }
    const frame = frames[index];
    renderFrame(key, frame);
    if (!stateBubbleShown && frame === stateBubbleTrigger) showStateBubble(key, stateConfig, token);
    let delay = durations[index] || 375;
    const playback = stateConfig.playback || {};
    if (!freezeConsumed && playback.freezeFrame === frame && playback.freezeMs) {
      delay += playback.freezeMs;
      freezeConsumed = true;
    }
    frameTimer = setTimeout(() => {
      if (token !== runToken) return;
      if (index + 1 < frames.length) playSegment(key, stateConfig, frames, durations, index + 1, token, onComplete);
      else if (onComplete) onComplete();
    }, delay);
  }

  function loopSegment(key, stateConfig, frames, durations, token) {
    if (!frames.length || token !== runToken) return;
    playSegment(key, stateConfig, frames, durations, 0, token, () => loopSegment(key, stateConfig, frames, durations, token));
  }

  function returnToIdle() {
    currentMotion = 'idle';
    currentExpression = 'neutral';
    root.className = 'e-neutral';
    character.setAttribute('class', 'm-idle');
    beginState('idle');
  }

  function runPlan(key, stateConfig, plan, token) {
    if (plan.mode === 'once') {
      playSegment(key, stateConfig, plan.frames, plan.durations, 0, token, returnToIdle);
      return;
    }
    const beginLoop = () => loopSegment(key, stateConfig, plan.loop, plan.loopDurations, token);
    if (plan.entry.length) playSegment(key, stateConfig, plan.entry, plan.entryDurations, 0, token, beginLoop);
    else beginLoop();

    if (plan.mode === 'entry-then-loop' && key === 'idle') {
      ambientTimer = setTimeout(() => {
        if (token !== runToken) return;
        currentState = 'idle_sleep';
        beginState('idle_sleep');
      }, 5000);
    } else if (plan.mode === 'random-variant-loop') {
      ambientTimer = setTimeout(() => {
        if (token !== runToken) return;
        returnToIdle();
      }, plan.durationMs);
    }
  }

  function beginState(key) {
    const stateConfig = registry && registry.getState(key);
    if (!stateConfig) return;
    runToken += 1;
    const token = runToken;
    currentState = key;
    stopStateTimers();
    // 显示队列（弹幕回复 / 语音）正在占着气泡框时不清它：切个动作就把观众正在读的
    // 回复抹掉，等于这条回复白发了——纯文字回复现在是主通道，抹不起。
    if (!isBusy) clearBubble();
    stateBubbleShown = false;
    freezeConsumed = false;
    stage.setAttribute('data-state', key);
    const plan = selectPlan(key, stateConfig);
    const playable = allPlanFrames(plan);
    stateBubbleTrigger = playable.includes(stateConfig.bubble && stateConfig.bubble.showAtFrame)
      ? stateConfig.bubble.showAtFrame
      : playable[0];
    preloadFrames(key, playable);
    runPlan(key, stateConfig, plan, token);
  }

  function applyMotion(name, expression) {
    currentMotion = name || 'idle';
    currentExpression = expression || (currentMotion === 'idle' ? 'neutral' : currentExpression);
    character.setAttribute('class', `m-${currentMotion}`);
    root.className = `e-${currentExpression}`;
    beginState(currentMotion);
  }

  function applyExpression(name) {
    currentExpression = name || 'neutral';
    root.className = `e-${currentExpression}`;
    if (currentMotion === 'idle') beginState(EXPRESSION_STATE[currentExpression] || 'idle');
  }

  const startTalk = () => character.classList.add('talking');
  const stopTalk = () => character.classList.remove('talking');

  // ── 显示队列 ───────────────────────────────────────────────────────────────
  // 多个动作几乎同时到达时（brain 的即时反应 + dialogue 追加的 LLM 回复），不排队的话
  // 后一条会立刻顶掉前一条，观众根本没看到第一条就没了。
  // 语音气泡和纯文字气泡**共用同一个气泡框**，所以两者不能各排各的队——各排各的必然
  // 出现"正在念的那句被后到的文字回复当场顶掉"。这里合并成一条队列串行出队：
  //   speak 项  —— 音频真的播完（或兜底时长走完）才算结束；
  //   bubble 项 —— 停留时长走完才算结束。
  // 另一条路是给文字回复单开一个气泡框、两个框上下并排，但直播画面里桌宠只有巴掌大，
  // 两个框会互相挡脸，所以选单框排队。
  // motion/expression 不进队列——那些是瞬时反应，不该等前面的话说完。
  const displayQueue = [];
  let isBusy = false;
  let displayTimer = null;
  // 本轮队列里有没有真的出过声。只有出过声才需要解除掐麦，纯文字回复压根没开过麦。
  let spokeThisRun = false;
  // stop 之后旧条目的收尾回调（音频 onended、兜底计时器）还会迟到。认这个代号把它们挡掉，
  // 否则一条已经被清掉的语音结束时会去推动新队列，把刚显示的回复挤掉。
  let displayEpoch = 0;

  // 队列彻底空了才算"说完"——中间那几句之间不解除掐麦，否则每句缝隙里都会把音箱余响收进去。
  function notifySpeakDone() {
    if (window.petAPI && window.petAPI.speakDone) window.petAPI.speakDone();
  }

  function clearDisplayQueue() {
    displayQueue.length = 0;
    displayTimer = clearTimer(displayTimer);
    isBusy = false;
    spokeThisRun = false;
    displayEpoch += 1;
    notifySpeakDone();
  }

  // 合成一条（幂等：同一条只会真的合成一次，结果挂在 item 上复用）。
  // 实测（2026-07-29）合成一句要 2.6~4.8 秒，且**跟文本长短几乎无关**——4 字和 36 字一样慢，
  // 花的是固定的进程启动 + 云端往返。所以串行"播完一句再合成下一句"每条都要干等一次，
  // 而让下一条在当前这条播放期间就去合成，这段等待就被藏掉了。
  function synth(item) {
    if (!item.pending) {
      item.pending = Promise.resolve(window.petAPI.speak(item.text, item.voiceName))
        .catch(() => null);
    }
    return item.pending;
  }

  const canSynth = () => !!(window.petAPI && window.petAPI.speak);

  // 让最近的一条待播语音先去合成。队列里可能夹着纯文字气泡（不需要合成），所以找的是
  // "最靠前的一条 speak"而不是队首；依然只预取一条，再多就是同时开好几个合成进程了。
  function prefetchNext() {
    if (!canSynth()) return;
    const next = displayQueue.find((item) => item.kind === 'speak');
    if (next) synth(next);
  }

  function enqueueDisplay(item) {
    if (isBusy) {
      displayQueue.push(item);
      prefetchNext();
      return;
    }
    runDisplayItem(item);
  }

  function runDisplayItem(item) {
    isBusy = true;
    if (item.kind === 'bubble') runBubble(item);
    else runSpeak(item);
  }

  // 一条显示结束后在这里接力下一条。
  function finishDisplayItem(epoch) {
    if (epoch !== displayEpoch) return;
    isBusy = false;
    if (displayQueue.length) { runDisplayItem(displayQueue.shift()); return; }
    if (spokeThisRun) { spokeThisRun = false; notifySpeakDone(); }
  }

  // 排队还剩超过 2 条时，把停留时间压到 5 秒。弹幕是实时的，攒一堆过时的话慢慢念既没
  // 意义又"不讨喜"（主播原话：不然一直说个没完只会让桌宠爆掉而且还不讨喜），宁可每条
  // 短一点也要让画面跟上正在刷的弹幕。已经比 5 秒还短的显式时长不会被拉长。
  const BUBBLE_CROWD_THRESHOLD = 2;
  const CROWDED_BUBBLE_MS = 5000;

  function bubbleHoldMs(requested) {
    const base = Number(requested) > 0 ? Number(requested) : AM.DEFAULT_BUBBLE_MS;
    return displayQueue.length > BUBBLE_CROWD_THRESHOLD ? Math.min(base, CROWDED_BUBBLE_MS) : base;
  }

  // 纯文字气泡：没有音频可等，到停留时长自己结束。
  function runBubble(item) {
    const epoch = displayEpoch;
    if (window.__onBubbleStart) window.__onBubbleStart(item.text);
    showBubble(item.text, 0, 'default', 'reply', 'speech');
    // 用独立计时器而不是 showBubble 自带的 bubbleTimer：切状态时 beginState 会
    // clearBubble()，那会顺手清掉 bubbleTimer——接力要是挂在它上面，一次状态切换就能
    // 让整条队列永远卡死，之后所有弹幕回复都不再显示。
    displayTimer = setTimeout(() => {
      displayTimer = null;
      // 期间要是被别的来源（动作气泡等）接管了气泡框，就别越权去清人家的。
      if (bubble.getAttribute('data-source') === 'reply') clearBubble();
      finishDisplayItem(epoch);
    }, bubbleHoldMs(item.duration));
  }

  function speak(text, voiceName) {
    enqueueDisplay({ kind: 'speak', text, voiceName });
  }

  async function runSpeak(item) {
    const { text, voiceName } = item;
    const epoch = displayEpoch;
    spokeThisRun = true;
    if (window.__onSpeakStart) window.__onSpeakStart(text);
    const stateBubbleActive = bubble.classList.contains('show') && bubble.getAttribute('data-source') === 'motion';
    if (!stateBubbleActive) showBubble(text, 0, 'speech', 'speech', 'speech');
    startTalk();
    // onended / onerror / play() 的 catch 有可能都触发，收尾只能算一次，
    // 否则一条语音结束会把队列一次推进两格，中间那条观众根本看不到。
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      stopTalk();
      // 已经被 stop 清掉的那条不该再对气泡框动手：这时候框里多半已经换成新的一条回复了，
      // 让它把 800ms 后收气泡的计时器挂上去，等于把新回复提前抹掉。
      if (epoch !== displayEpoch) return;
      if (!stateBubbleActive) bubbleTimer = setTimeout(clearBubble, 800);
      finishDisplayItem(epoch);
    };
    try {
      // ⚠️ 没有 petAPI 时（离线测试用的假环境）这里必须**一个 await 都不能有**：
      // test_renderer.js 用假时钟同步断言"第二句紧接第一句播完就开始"，多一个微任务就对不上。
      if (canSynth()) {
        const result = await synth(item);
        if (result && result.ok && result.audio) {
          voice.src = result.audio;
          voice.onended = done;
          voice.onerror = done;
          prefetchNext();      // 开始播这条的同时，让下一条去合成
          await voice.play().catch(done);
          return;
        }
      }
    } catch (error) { /* fall through to the timing-only preview */ }
    prefetchNext();
    setTimeout(done, Math.min(4000, 600 + text.length * 120));
  }

  // 空文本历来当"收气泡"用，别让它占一格队列干等 8 秒。
  function showQueuedBubble(text, duration) {
    if (!text) { clearBubble(); return; }
    enqueueDisplay({ kind: 'bubble', text: String(text), duration });
  }

  function applyIntent(intent) {
    if (!intent || intent.ignored) return;
    if (intent.clearBubble) clearBubble();
    // stop 要先于动作生效：先把队列清空，后面 beginState 才会认为气泡框没人占、该清就清。
    // 反过来的话会留下一个已经被叫停、却还挂在屏幕上的气泡。
    if (intent.stop) { stopTalk(); try { voice.pause(); } catch (error) {} clearDisplayQueue(); }
    if (intent.motion) applyMotion(intent.motion, intent.expression);
    else if (intent.expression) applyExpression(intent.expression);
    if (intent.bubble && !intent.speak) showQueuedBubble(intent.bubble.text, intent.bubble.duration);
    if (intent.speak) speak(intent.speak.text, intent.speak.voice);
  }

  const injectAction = (message) => applyIntent(AM.mapAction(message));
  window.__injectAction = injectAction;
  window.__previewState = (key) => {
    if (!registry.getState(key)) return false;
    if (AM.MOTIONS.includes(key)) applyMotion(key);
    else {
      currentMotion = 'idle';
      currentExpression = Object.keys(EXPRESSION_STATE).find((name) => EXPRESSION_STATE[name] === key) || 'neutral';
      root.className = `e-${currentExpression}`;
      character.setAttribute('class', 'm-idle');
      beginState(key);
    }
    return true;
  };

  if (window.petAPI && window.petAPI.onAction) window.petAPI.onAction(injectAction);
  if (window.petAPI && window.petAPI.onCommand) {
    window.petAPI.onCommand((message) => {
      if (message.kind === 'render') {
        if (message.motion) applyMotion(message.motion, message.expression || 'neutral');
        if (message.bubble) showBubble(message.bubble, 0);
      }
    });
  }

  applyMotion('idle', 'neutral');
  if (typeof location !== 'undefined') {
    const preview = new URLSearchParams(location.search).get('preview');
    if (preview) setTimeout(() => window.__previewState(preview), 50);
  }
}());
