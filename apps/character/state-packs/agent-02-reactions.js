/* Agent 02 reaction state pack: scared, gift thanks, and laugh.
 * UMD: Node uses require(); browsers read window.PetStatePacks.agent02.
 * Runtime frame numbers are one-based: 1..8 map to source files 00.png..07.png.
 */
(function (root, factory) {
  const pack = factory();
  if (typeof module === 'object' && module.exports) module.exports = pack;
  else {
    root.PetStatePacks = root.PetStatePacks || {};
    root.PetStatePacks.agent02 = pack;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const states = {
    scared: {
      bubble: {
        mode: 'motion',
        texts: ['吓死宝宝了'],
        tone: 'fear',
        showAtFrame: 3,
        durationMs: 3700,
        anchor: {
          preset: 'above-head',
          target: 'mouth',
          align: 'center',
          offsetX: 0,
          offsetY: -4,
          avoidOverlap: true,
        },
        closeOnExit: true,
      },
      playback: {
        mode: 'once',
        frameIndexBase: 1,
        frameCount: 8,
        frameSequence: [1, 2, 3, 4, 5, 6, 7, 8],
        frameDurationsMs: [220, 260, 340, 220, 300, 320, 300, 340],
        freezeFrame: 3,
        freezeMs: 3000,
        effect: {
          type: 'camera-punch-in',
          showAtFrame: 3,
          durationMs: 3000,
          strength: 'strong',
        },
      },
    },

    thank_small: {
      bubble: {
        mode: 'motion',
        texts: ['谢谢犒劳我家主人！'],
        tone: 'gratitude-soft',
        showAtFrame: 2,
        durationMs: 2500,
        anchor: {
          preset: 'above-head',
          target: 'mouth',
          align: 'center',
          offsetX: -6,
          offsetY: -4,
          avoidOverlap: true,
        },
        closeOnExit: true,
      },
      playback: {
        mode: 'once',
        frameIndexBase: 1,
        frameCount: 8,
        frameSequence: [1, 2, 3, 4, 5, 6, 7, 8],
        frameDurationsMs: [280, 320, 360, 480, 440, 360, 340, 420],
        effect: null,
      },
    },

    thank_big: {
      bubble: {
        mode: 'motion',
        texts: ['Mua 你最好啦爱你！'],
        tone: 'gratitude-kiss',
        showAtFrame: 4,
        durationMs: 2200,
        anchor: {
          preset: 'mouth-side',
          target: 'mouth',
          align: 'right',
          offsetX: 40,
          offsetY: -18,
          avoidOverlap: true,
          fallback: 'above-head-right',
        },
        closeOnExit: true,
      },
      playback: {
        mode: 'once',
        frameIndexBase: 1,
        frameCount: 8,
        frameSequence: [1, 2, 3, 4, 5, 6, 7, 8],
        frameDurationsMs: [240, 260, 360, 420, 520, 420, 380, 400],
        effect: {
          type: 'kiss-heart',
          showAtFrame: 4,
          durationMs: 900,
          anchor: 'mouth-right-attached',
          detached: false,
        },
      },
    },

    laugh: {
      bubble: {
        mode: 'motion',
        texts: ['哈哈哈哈哈主人好蠢！'],
        tone: 'mischief-laugh',
        showAtFrame: 3,
        durationMs: 2500,
        anchor: {
          preset: 'above-head',
          target: 'mouth',
          align: 'center',
          offsetX: 0,
          offsetY: -4,
          avoidOverlap: true,
        },
        closeOnExit: true,
      },
      playback: {
        mode: 'once',
        frameIndexBase: 1,
        frameCount: 8,
        frameSequence: [1, 2, 3, 4, 5, 6, 7, 8],
        frameDurationsMs: [300, 260, 340, 400, 430, 430, 390, 450],
        effect: {
          type: 'pose-emphasis',
          phases: ['belly-laugh', 'sit-fall', 'ground-roll', 'recover'],
        },
      },
    },
  };

  return { owner: 'agent-02', states };
}));
