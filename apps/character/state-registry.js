/* state-registry.js — merges the fixed sub-agent state packs with the approved wave pilot. */
(function (root, factory) {
  const packs = typeof module === 'object' && module.exports
    ? [
        require('./state-packs/agent-01-ambient.js'),
        require('./state-packs/agent-02-reactions.js'),
        require('./state-packs/agent-03-social.js'),
      ]
    : Object.values(root.PetStatePacks || {});
  const registry = factory(packs);
  if (typeof module === 'object' && module.exports) module.exports = registry;
  else root.PetStateRegistry = registry;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (packs) {
  const wave = {
    bubble: {
      mode: 'speech',
      texts: ['哈喽 哈喽'],
      tone: 'greeting',
      showAtFrame: 1,
      durationMs: 3000,
      anchor: { target: 'mouth', placement: 'above', gapPx: 4 },
      closeOnExit: true,
    },
    playback: {
      mode: 'once',
      frameSequence: [1, 2, 3, 4, 5, 6, 7, 8],
      frameDurationsMs: [350, 300, 350, 400, 400, 450, 350, 400],
    },
  };

  const states = { wave };
  const owners = { wave: 'main' };
  for (const pack of packs) {
    if (!pack || !pack.states) continue;
    for (const [name, config] of Object.entries(pack.states)) {
      if (states[name]) throw new Error(`duplicate pet state: ${name}`);
      states[name] = config;
      owners[name] = pack.owner || 'unknown';
    }
  }

  function getState(name) { return states[name] || null; }
  function getOwner(name) { return owners[name] || null; }
  return { states, owners, getState, getOwner };
}));
