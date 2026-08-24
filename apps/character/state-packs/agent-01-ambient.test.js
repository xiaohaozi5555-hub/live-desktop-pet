const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const pack = require('./agent-01-ambient.js');

const expectedStates = ['idle', 'idle_sleep', 'sleep', 'idle_happy'];

assert.equal(pack.owner, 'agent-01');
assert.deepEqual(Object.keys(pack.states).sort(), expectedStates.slice().sort());

for (const name of expectedStates) {
  const state = pack.states[name];
  assert.ok(state, `${name} must exist`);
  assert.equal(state.source.numbering, 'one-based');

  for (const field of ['mode', 'texts', 'tone', 'showAtFrame', 'durationMs', 'anchor', 'closeOnExit']) {
    assert.ok(Object.hasOwn(state.bubble, field), `${name}.bubble.${field} is required`);
  }
  assert.ok(state.bubble.showAtFrame >= 1 && state.bubble.showAtFrame <= state.source.frameCount);
  assert.equal(state.bubble.anchor.allowCharacterOverlap, false);

  for (let frame = 1; frame <= state.source.frameCount; frame += 1) {
    const suffix = frame === 1 ? '' : `_${frame}`;
    const assetPath = path.resolve(__dirname, '..', '..', '..', 'assets', 'character', `${state.source.key}${suffix}.png`);
    assert.ok(fs.existsSync(assetPath), `${name} runtime frame is missing: ${assetPath}`);
  }

  const sequences = [];
  if (state.playback.entryFrames) sequences.push(state.playback.entryFrames);
  if (state.playback.loopFrames) sequences.push(state.playback.loopFrames);
  if (state.playback.exitFrames) sequences.push(state.playback.exitFrames);
  for (const variant of state.playback.loopVariants || []) {
    sequences.push(variant.entryFrames, variant.loopFrames);
  }
  for (const sequence of sequences) {
    for (const frame of sequence) {
      assert.ok(Number.isInteger(frame), `${name} frame must be an integer`);
      assert.ok(frame >= 1 && frame <= state.source.frameCount, `${name} frame ${frame} is out of range`);
    }
  }
}

const idle = pack.states.idle.playback;
assert.equal(idle.mode, 'entry-then-loop');
assert.equal(idle.entryFrames.some((frame) => idle.loopFrames.includes(frame)), false,
  'idle entry and eating loop must not share frames');
assert.ok(Math.min(...idle.loopFrames) >= 8, 'idle loop must start at the first eating frame');
assert.ok(Math.max(...idle.loopFrames) <= 10, 'idle loop must not stand up again');

const idleSleep = pack.states.idle_sleep.playback;
assert.equal(idleSleep.mode, 'random-variant-loop');
assert.equal(idleSleep.selection, 'random-on-entry');
assert.equal(idleSleep.loopVariants.length, 4, 'idle_sleep must expose exactly four independent poses');
assert.equal(new Set(idleSleep.loopVariants.map((variant) => variant.id)).size, 4);
assert.deepEqual(idleSleep.loopVariants.map((variant) => variant.loopFrames), [[2], [3], [4], [6]]);
assert.deepEqual(pack.states.idle_sleep.bubble.snoreTexts, ['Z', 'Zz', 'Zzz']);
assert.equal(pack.states.idle_sleep.bubble.dreamTexts.length, 4);
for (const variant of idleSleep.loopVariants) {
  assert.ok(variant.loopFrames.length > 0, `${variant.id} must be independently loopable`);
  assert.equal(new Set(variant.loopFrames).size, 1, `${variant.id} must remain one fixed sleeping pose`);
}

const sleep = pack.states.sleep.playback;
assert.equal(sleep.mode, 'persistent-loop');
assert.equal(sleep.autoExit, false);
assert.equal(sleep.exitPolicy, 'wake-or-idle-only');
assert.ok(sleep.loopFrames.length > 0);

const browserCode = fs.readFileSync(path.resolve(__dirname, 'agent-01-ambient.js'), 'utf8');
const browserContext = {};
vm.runInNewContext(browserCode, browserContext);
assert.equal(browserContext.PetStatePacks.agent01.owner, 'agent-01', 'browser global must be exposed');

console.log('agent-01 ambient state pack: all tests passed');
