// Applause synthesised with Web Audio, so there is no audio file to ship: a
// crowd of virtual clappers, each a train of short filtered noise bursts,
// fading out over the clip.
export function createApplause() {
  let context;
  return {
    /** Call from a click or key press: browsers only allow sound after a gesture. */
    prime() {
      try {
        context ??= new AudioContext();
        if (context.state === 'suspended') context.resume();
      } catch { /* no audio available: the game still works silently */ }
    },
    play(seconds = 2) {
      if (!context) return;
      const now = context.currentTime + 0.05;
      const noise = context.createBuffer(1, Math.floor(context.sampleRate * 0.05), context.sampleRate);
      const samples = noise.getChannelData(0);
      for (let i = 0; i < samples.length; i++) samples[i] = Math.random() * 2 - 1;

      const master = context.createGain();
      // Full applause for the first 40%, then a long fade to silence.
      master.gain.setValueAtTime(0.6, now);
      master.gain.setValueAtTime(0.6, now + seconds * 0.4);
      master.gain.exponentialRampToValueAtTime(0.0001, now + seconds);
      master.connect(context.destination);

      for (let clapper = 0; clapper < 18; clapper++) {
        const rate = 3.5 + Math.random() * 1.8;            // claps per second
        const tone = 900 + Math.random() * 1500;
        const loudness = 0.25 + Math.random() * 0.35;
        for (let at = now + Math.random() / rate; at < now + seconds; at += (0.85 + Math.random() * 0.3) / rate) {
          const source = context.createBufferSource();
          source.buffer = noise;
          const filter = context.createBiquadFilter();
          filter.type = 'bandpass';
          filter.frequency.value = tone;
          filter.Q.value = 1.1;
          const envelope = context.createGain();
          envelope.gain.setValueAtTime(0, at);
          envelope.gain.linearRampToValueAtTime(loudness, at + 0.002);
          envelope.gain.exponentialRampToValueAtTime(0.001, at + 0.06);
          source.connect(filter).connect(envelope).connect(master);
          source.start(at);
          source.stop(at + 0.07);
        }
      }
    },
  };
}
