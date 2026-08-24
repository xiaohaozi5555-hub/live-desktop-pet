/* Map action.* contract messages to renderer intents. */
(function (root, factory) {
  const registry = typeof module === 'object' && module.exports
    ? require('./state-registry.js')
    : root.PetStateRegistry;
  const api = factory(registry);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ActionMap = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (registry) {
  const MOTIONS = ['idle', 'wave', 'scared', 'thank_small', 'thank_big', 'laugh', 'praise', 'beg', 'sleep'];
  const EXPRESSIONS = ['neutral', 'happy', 'scared', 'surprised', 'smug', 'blush', 'sleepy'];
  const MOTION_EXPR = {
    idle: 'neutral', wave: 'happy', scared: 'scared', thank_small: 'happy',
    thank_big: 'happy', laugh: 'happy', praise: 'smug', beg: 'blush', sleep: 'sleepy',
  };
  const EXPRESSION_STATE = { happy: 'idle_happy', smug: 'idle_smug', surprised: 'idle_surprised' };

  // 纯文字气泡自 2026-07-30 起是弹幕回复的主力通道——大部分回复不再出声，一直有语音
  // 会盖过直播本身的声音、影响观感。8 秒是主播试播后定的停留时长：再短观众读不完，
  // 再长下一条就堵着。后端 dialogue.py 的 BUBBLE_MS 是同一个值，两边要对齐。
  const DEFAULT_BUBBLE_MS = 8000;

  function mapAction(msg) {
    if (!msg || msg.channel !== 'action') return { ignored: true, reason: 'not-action' };
    const data = msg.data || {};
    switch (msg.type) {
      case 'play_motion': {
        const motion = MOTIONS.includes(data.motion) ? data.motion : 'idle';
        return {
          motion,
          expression: MOTION_EXPR[motion] || 'neutral',
          stateKey: motion,
          stateConfig: registry ? registry.getState(motion) : null,
        };
      }
      case 'set_expression': {
        const expression = EXPRESSIONS.includes(data.expression) ? data.expression : 'neutral';
        const stateKey = EXPRESSION_STATE[expression] || 'idle';
        return {
          expression,
          stateKey,
          stateConfig: registry ? registry.getState(stateKey) : null,
        };
      }
      case 'show_bubble':
        return {
          bubble: {
            text: String(data.text || ''),
            duration: Number(data.duration_ms) > 0 ? Number(data.duration_ms) : DEFAULT_BUBBLE_MS,
          },
        };
      case 'speak':
        return {
          speak: {
            text: String(data.text || ''),
            voice: data.voice || 'zh-CN-XiaoyiNeural',
            emotion: data.emotion || 'general',
          },
          bubble: { text: String(data.text || ''), duration: 4000 },
        };
      case 'stop':
        return { stop: true, motion: 'idle', expression: 'neutral', stateKey: 'idle', clearBubble: true };
      default:
        return { ignored: true, reason: `unknown-type:${msg.type}` };
    }
  }

  return { mapAction, MOTIONS, EXPRESSIONS, EXPRESSION_STATE, DEFAULT_BUBBLE_MS };
}));
