'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const pack = require('./agent-03-social.js');

const browserContext = { self: {} };
vm.runInNewContext(fs.readFileSync(require.resolve('./agent-03-social.js'), 'utf8'), browserContext);
assert.equal(browserContext.self.PetStatePacks.agent03.owner, 'agent-03');
assert.deepEqual(
  Array.from(Object.keys(browserContext.self.PetStatePacks.agent03.states)).sort(),
  ['beg', 'idle_smug', 'idle_surprised', 'praise'],
);

const expectedStates = ['praise', 'beg', 'idle_smug', 'idle_surprised'];
assert.equal(pack.owner, 'agent-03');
assert.deepEqual(Object.keys(pack.states).sort(), expectedStates.slice().sort());

for (const name of expectedStates) {
  const state = pack.states[name];
  assert.ok(state.bubble, `${name}: missing bubble`);
  assert.ok(state.playback, `${name}: missing playback`);
  assert.equal(state.bubble.closeOnExit, true, `${name}: bubble must close on exit`);
  assert.ok(Array.isArray(state.bubble.texts) && state.bubble.texts.length > 0, `${name}: missing texts`);
  const expectedAnchor = name === 'beg' ? 'wink-eye' : 'mouth';
  assert.equal(state.bubble.anchor.target, expectedAnchor, `${name}: unexpected bubble anchor`);
  assert.ok(state.bubble.anchor.gapPx > 0, `${name}: bubble cannot overlap character`);

  const frames = state.playback.frameSequence;
  const durations = state.playback.frameDurationsMs;
  assert.equal(state.playback.mode, 'once', `${name}: facial/event state must play once`);
  assert.equal(frames.length, durations.length, `${name}: frame duration count mismatch`);
  assert.ok(frames.length > 0, `${name}: empty frame sequence`);
  assert.ok(frames.every((frame) => Number.isInteger(frame) && frame >= 1 && frame <= 8), `${name}: invalid frame number`);
  assert.ok(durations.every((duration) => Number.isInteger(duration) && duration > 0), `${name}: invalid duration`);
  assert.ok(frames.includes(state.bubble.showAtFrame), `${name}: bubble show frame is not playable`);
}

assert.deepEqual(pack.states.praise.bubble.texts, ['感谢点赞哟！我也来点点赞！']);
assert.equal(pack.states.beg.bubble.mode, 'heart-grow');
assert.deepEqual(pack.states.beg.bubble.texts, ['爱你哟！']);
assert.equal(pack.states.beg.winkFrame, 5);
assert.equal(pack.states.beg.bubble.showAtFrame, 5);
assert.equal(pack.states.beg.bubble.revealTextDelayMs, 650);
assert.deepEqual(pack.states.beg.playback.frameSequence, [1, 2, 3, 4, 5]);
assert.equal(pack.states.beg.playback.frameDurationsMs.at(-1), 1600);
assert.equal(pack.states.beg.effect.type, 'wink-eye-heart');
assert.equal(pack.states.beg.effect.detachedParticles, false);
assert.equal(pack.states.beg.effect.showAtFrame, pack.states.beg.bubble.showAtFrame);
assert.equal(pack.states.beg.effect.delayMs + pack.states.beg.effect.growDurationMs, 600);
assert.ok(pack.states.beg.effect.textRevealDelayMs > 600, 'beg: copy must appear after heart growth');

console.log('agent-03-social: 4 states validated');
