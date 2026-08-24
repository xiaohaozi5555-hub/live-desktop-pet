(function (root, factory) {
  const pack = factory();
  if (typeof module === 'object' && module.exports) module.exports = pack;
  if (root) {
    root.PetStatePacks = root.PetStatePacks || {};
    root.PetStatePacks.agent01 = pack;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  // 与后端 services/dialogue/dialogue.py 的 MAX_PENDING_REPLIES 是同一个含义：待回复队列
  // 一满，后来的弹幕直接丢掉，不出声也不提示。观众不知道自己被丢了，会以为桌宠故意不理
  // 人，所以待机气泡里常驻一句说明，把这个上限讲清楚。
  // ⚠️ 两边必须一起改，否则这句文案会骗观众。
  const MAX_PENDING_REPLIES = 15;

  const mouthBubble = (overrides) => Object.assign({
    mode: 'one-shot',
    texts: [],
    tone: 'default',
    showAtFrame: 1,
    durationMs: 2600,
    anchor: {
      target: 'mouth',
      side: 'top',
      offsetX: 18,
      offsetY: -6,
      avoidFace: true,
      allowCharacterOverlap: false,
    },
    closeOnExit: true,
  }, overrides || {});

  const sleepBubble = (overrides) => mouthBubble(Object.assign({
    mode: 'snore-cycle',
    texts: ['Z', 'Zz', 'Zzz'],
    tone: 'sleep',
    durationMs: 0,
    anchor: {
      target: 'head',
      side: 'top-right',
      offsetX: 16,
      offsetY: -5,
      avoidFace: true,
      allowCharacterOverlap: false,
    },
  }, overrides || {}));

  return {
    owner: 'agent-01',
    states: {
      idle: {
        source: { key: 'idle', frameCount: 12, numbering: 'one-based' },
        bubble: mouthBubble({
          mode: 'persistent-loop',
          // 待机是唯一能"白送"信息给观众的时段：桌宠没在回话，气泡框空着，说明性的话
          // 放在这里既不打断回复也不用出声。所以除了原来的吃货口癖，再挂两句规则提示。
          // 按顺序轮换（rotateTexts）而不是随机抽，保证每句都有露面机会。
          texts: [
            '好吃好吃',
            `本公主一次只能看 ${MAX_PENDING_REPLIES} 条弹幕哦，没听到的话就再说一遍，哼！`,
            '想让本公主搭理你，弹幕里得喊"魔丸"才行呀，笨蛋～',
          ],
          rotateTexts: true,
          tone: 'snack',
          showAtFrame: 8,
          durationMs: 0,
        }),
        playback: {
          mode: 'entry-then-loop',
          entryFrames: [1, 2, 3, 4, 5, 6, 7],
          loopFrames: [8, 9, 10, 9],
          frameDurationsMs: {
            entry: [420, 260, 260, 320, 360, 420, 360],
            loop: [300, 340, 620, 340],
          },
          exitPolicy: 'interruptible',
        },
      },

      idle_sleep: {
        source: { key: 'idle_sleep', frameCount: 8, numbering: 'one-based' },
        bubble: sleepBubble({
          mode: 'snore-or-dream-cycle',
          snoreTexts: ['Z', 'Zz', 'Zzz'],
          texts: [
            '主人怎么还不下班',
            '臭蝴蝶又要你女儿陪着加班',
            '想不明白我蝴蝶哥这么帅怎么没人气呢？',
            '不许骂我小蝴蝶',
          ],
          dreamTexts: [
            '主人怎么还不下班',
            '臭蝴蝶又要你女儿陪着加班',
            '想不明白我蝴蝶哥这么帅怎么没人气呢？',
            '不许骂我小蝴蝶',
          ],
          tone: 'dream',
          showAtFrame: 2,
        }),
        playback: {
          mode: 'random-variant-loop',
          selection: 'random-on-entry',
          exitFrames: [7, 8],
          loopVariants: [
            {
              id: 'seated-doze',
              entryFrames: [2],
              loopFrames: [2],
              frameDurationsMs: { entry: [480], loop: [920] },
            },
            {
              id: 'curled-up',
              entryFrames: [3],
              loopFrames: [3],
              frameDurationsMs: { entry: [520], loop: [1050] },
            },
            {
              id: 'starfish',
              entryFrames: [4],
              loopFrames: [4],
              frameDurationsMs: { entry: [480], loop: [980] },
            },
            {
              id: 'face-down',
              entryFrames: [6],
              loopFrames: [6],
              frameDurationsMs: { entry: [480], loop: [980] },
            },
          ],
          variantDurationMs: 6000,
          exitPolicy: 'ambient-timeout-or-interrupt',
        },
      },

      sleep: {
        source: { key: 'sleep', frameCount: 8, numbering: 'one-based' },
        bubble: sleepBubble({
          mode: 'snore-cycle',
          texts: ['Z', 'Zz', 'Zzz'],
          tone: 'sleep',
          showAtFrame: 5,
        }),
        playback: {
          mode: 'persistent-loop',
          entryFrames: [1, 2, 3, 4],
          loopFrames: [5, 6],
          frameDurationsMs: {
            entry: [340, 380, 420, 460],
            loop: [900, 900],
          },
          autoExit: false,
          exitPolicy: 'wake-or-idle-only',
        },
      },

      idle_happy: {
        source: { key: 'idle_happy', frameCount: 8, numbering: 'one-based' },
        bubble: mouthBubble({
          mode: 'one-shot',
          texts: ['抓到你啦，谁都不许抢～'],
          tone: 'dark-cute',
          showAtFrame: 2,
          durationMs: 2800,
        }),
        playback: {
          mode: 'one-shot-return-idle',
          entryFrames: [1, 2, 3, 4, 5, 6, 7, 8],
          loopFrames: [],
          frameDurationsMs: {
            entry: [350, 300, 320, 430, 430, 360, 360, 350],
            loop: [],
          },
          exitPolicy: 'return-idle',
        },
      },
    },
  };
}));
