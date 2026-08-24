/* agent-03-social.js -- 3 号 Agent 负责的社交与面部联动状态包。
 * 本文件只描述运行时配置：文字与爱心均由 DOM 渲染，不写入逐帧 PNG。
 */
(function (root, factory) {
  const pack = factory();
  if (typeof module === 'object' && module.exports) module.exports = pack;
  else {
    root.PetStatePacks = root.PetStatePacks || {};
    root.PetStatePacks.agent03 = pack;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  const mouthBubble = (overrides) => Object.assign({
    mode: 'speech',
    texts: [],
    tone: 'default',
    showAtFrame: 0,
    durationMs: 2200,
    anchor: {
      target: 'mouth',
      placement: 'above',
      offsetX: 10,
      offsetY: -4,
      gapPx: 4,
      avoid: ['eyes', 'gesture'],
    },
    closeOnExit: true,
  }, overrides);

  const states = {
    praise: {
      bubble: mouthBubble({
        texts: ['感谢点赞哟！我也来点点赞！'],
        tone: 'cheerful',
        showAtFrame: 3,
        durationMs: 2600,
        anchor: {
          target: 'mouth',
          placement: 'above-left',
          offsetX: -8,
          offsetY: -4,
          gapPx: 4,
          avoid: ['eyes', 'thumbs-up'],
        },
      }),
      playback: {
        mode: 'once',
        frameSequence: [1, 2, 3, 4, 5, 6, 7, 8],
        frameDurationsMs: [400, 300, 350, 400, 500, 350, 300, 400],
      },
    },

    beg: {
      bubble: mouthBubble({
        mode: 'heart-grow',
        texts: ['爱你哟！'],
        tone: 'affection',
        // 源文件 04.png 是明确 wink（运行时第 5 帧）；爱心从眼角长出后才显示文案。
        showAtFrame: 5,
        durationMs: 1600,
        revealTextDelayMs: 650,
        anchor: {
          target: 'wink-eye',
          placement: 'eye-right',
          offsetX: 52,
          offsetY: 0,
          gapPx: 4,
          avoid: ['eyes', 'wink-eye', 'clasped-hands'],
        },
        effect: 'wink-eye-heart',
      }),
      playback: {
        mode: 'once',
        // 第 5 帧保持到动作结束，不再回到睁眼蹲姿或站姿。
        frameSequence: [1, 2, 3, 4, 5],
        frameDurationsMs: [400, 300, 350, 350, 1600],
      },
      winkFrame: 5,
      effect: {
        type: 'wink-eye-heart',
        showAtFrame: 5,
        delayMs: 120,
        growDurationMs: 480,
        textRevealDelayMs: 650,
        attachTo: 'wink-eye',
        detachedParticles: false,
      },
    },

    idle_smug: {
      bubble: mouthBubble({
        texts: ['嘿嘿，主人又被我拿捏啦！'],
        tone: 'mischievous',
        showAtFrame: 3,
        durationMs: 2200,
        anchor: {
          target: 'mouth',
          placement: 'above-right',
          offsetX: 14,
          offsetY: -3,
          gapPx: 4,
          avoid: ['eyes', 'paw-over-mouth'],
        },
      }),
      playback: {
        mode: 'once',
        frameSequence: [1, 2, 3, 4, 5, 6, 7, 8],
        frameDurationsMs: [450, 300, 350, 400, 500, 350, 300, 350],
      },
    },

    idle_surprised: {
      bubble: mouthBubble({
        texts: ['诶？居然还有这一招！'],
        tone: 'surprised',
        showAtFrame: 3,
        durationMs: 2100,
        anchor: {
          target: 'mouth',
          placement: 'above-left',
          offsetX: -12,
          offsetY: -4,
          gapPx: 4,
          avoid: ['eyes', 'open-hands'],
        },
      }),
      playback: {
        mode: 'once',
        frameSequence: [1, 2, 3, 4, 5, 6, 7, 8],
        frameDurationsMs: [450, 300, 350, 350, 450, 400, 300, 400],
      },
    },
  };

  return { owner: 'agent-03', states };
}));
