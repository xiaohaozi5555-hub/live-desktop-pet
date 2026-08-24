'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const pack = require('./agent-02-reactions.js');

const sourcePath = path.join(__dirname, 'agent-02-reactions.js');
const browserContext = { globalThis: {} };
vm.runInNewContext(fs.readFileSync(sourcePath, 'utf8'), browserContext);
assert.equal(browserContext.globalThis.PetStatePacks.agent02.owner, 'agent-02');

const expectedStates = ['laugh', 'scared', 'thank_big', 'thank_small'];
assert.equal(pack.owner, 'agent-02');
assert.deepEqual(Object.keys(pack.states).sort(), expectedStates);

for (const [name, state] of Object.entries(pack.states)) {
  assert.ok(state.bubble, `${name}: missing bubble config`);
  assert.ok(state.playback, `${name}: missing playback config`);
  assert.equal(state.bubble.mode, 'motion', `${name}: bubble must be motion-owned`);
  assert.equal(state.bubble.closeOnExit, true, `${name}: bubble must close on exit`);
  assert.ok(Array.isArray(state.bubble.texts) && state.bubble.texts.length > 0, `${name}: missing copy`);
  assert.ok(state.bubble.anchor && state.bubble.anchor.avoidOverlap, `${name}: anchor must avoid overlap`);

  const playback = state.playback;
  assert.equal(playback.mode, 'once', `${name}: event reaction must be one-shot`);
  assert.equal(playback.frameIndexBase, 1, `${name}: frame numbering must be explicit`);
  assert.equal(playback.frameSequence.length, playback.frameDurationsMs.length, `${name}: duration count mismatch`);
  assert.ok(playback.frameDurationsMs.every((ms) => Number.isInteger(ms) && ms > 0), `${name}: invalid duration`);
  assert.ok(playback.frameSequence.every((frame) => Number.isInteger(frame) && frame >= 1 && frame <= playback.frameCount), `${name}: invalid frame number`);
  assert.ok(playback.frameSequence.includes(state.bubble.showAtFrame), `${name}: bubble trigger frame is not played`);
  for (const frame of playback.frameSequence) {
    const sourceFrame = path.resolve(__dirname, '..', '..', '..', 'assets', 'character', 'frames', name, `${String(frame - 1).padStart(2, '0')}.png`);
    assert.ok(fs.existsSync(sourceFrame), `${name}: missing source frame ${frame}`);
  }
}

assert.equal(pack.states.scared.playback.freezeFrame, 3);
assert.equal(pack.states.scared.playback.freezeMs, 3000);
assert.ok(pack.states.scared.playback.frameSequence.includes(pack.states.scared.playback.freezeFrame));
assert.deepEqual(pack.states.scared.bubble.texts, ['吓死宝宝了']);

assert.deepEqual(pack.states.thank_small.bubble.texts, ['谢谢犒劳我家主人！']);
assert.deepEqual(pack.states.thank_big.bubble.texts, ['Mua 你最好啦爱你！']);
assert.notEqual(pack.states.thank_small.bubble.tone, pack.states.thank_big.bubble.tone);
assert.equal(pack.states.thank_small.playback.effect, null);
assert.equal(pack.states.thank_big.playback.effect.type, 'kiss-heart');
assert.equal(pack.states.thank_big.playback.effect.detached, false);

assert.deepEqual(pack.states.laugh.bubble.texts, ['哈哈哈哈哈主人好蠢！']);
assert.ok(pack.states.laugh.playback.effect.phases.includes('ground-roll'));

const normalDuration = (state) => state.playback.frameDurationsMs.reduce((sum, ms) => sum + ms, 0);
assert.equal(normalDuration(pack.states.thank_small), 3000);
assert.equal(normalDuration(pack.states.thank_big), 3000);
assert.equal(normalDuration(pack.states.laugh), 3000);
assert.ok(normalDuration(pack.states.scared) + pack.states.scared.playback.freezeMs > 5000);

console.log('agent-02-reactions: 4 states validated');
