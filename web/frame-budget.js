// Measured frame pacing with hysteresis. Hidden/idle gaps never affect quality.
export function createFrameBudget({ targetMs = 1000 / 30, windowSize = 90 } = {}) {
  let samples = [], level = 0, lastChange = -Infinity;
  return {
    reset() { samples = []; level = 0; lastChange = -Infinity; },
    record(ms, now, active = true) {
      if (!active || !Number.isFinite(ms) || ms <= 0 || ms > 200) { samples = []; return null; }
      samples.push(ms);
      if (samples.length < windowSize || now - lastChange < 5000) return null;
      const sorted = samples.slice().sort((a,b) => a-b);
      const p95 = sorted[Math.floor((sorted.length - 1) * .95)];
      samples = [];
      const next = p95 > targetMs * 1.2 ? Math.min(2, level + 1)
        : p95 < targetMs * .72 ? Math.max(0, level - 1) : level;
      if (next === level) return null;
      level = next; lastChange = now;
      return { level, p95, targetMs };
    },
    get level() { return level; }
  };
}
