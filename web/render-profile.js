// Records browser frame pacing and CPU submission time; not GPU timer queries.
export function createRenderProfiler({ getContext, onStatus = () => {}, onComplete = () => {} }) {
  let capture = null, last = null;
  const percentile = (a, p) => a.length ? [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(p * a.length))] : null;
  function start(now = performance.now()) {
    capture = { start: now, warmupMs: 2000, durationMs: 30000, context: getContext(), frames: [], prior: null };
    onStatus('Warming up for 2 s, then recording 30 s…');
  }
  function cancel(reason = 'Capture cancelled.') { capture = null; onStatus(reason); }
  function record({ now, submissionMs, calls, triangles, lod }) {
    if (!capture) return;
    if (document.hidden) { cancel('Capture cancelled: keep the viewer tab visible.'); return; }
    const context = getContext();
    if (context.scene !== capture.context.scene) { cancel('Capture cancelled: the scene changed.'); return; }
    const elapsed = now - capture.start - capture.warmupMs;
    if (elapsed < 0) { capture.prior = null; return; }
    if (capture.prior !== null) capture.frames.push({ intervalMs: now - capture.prior, submissionMs, calls, triangles, quality: context.quality, lod });
    capture.prior = now;
    onStatus(`Recording · ${Math.max(0, Math.ceil((capture.durationMs - elapsed) / 1000))} s remaining`);
    if (elapsed < capture.durationMs) return;
    const frames = capture.frames, intervals = frames.map(f => f.intervalMs), cpu = frames.map(f => f.submissionMs);
    const total = intervals.reduce((sum, v) => sum + v, 0);
    last = { schema: 'depthwizard-render-profile-v1', capturedAt: new Date().toISOString(),
      initialContext: capture.context, finalContext: context, samples: frames.length, measuredSeconds: total / 1000,
      fps: total ? frames.length * 1000 / total : null, frameMedianMs: percentile(intervals, .5),
      frameP95Ms: percentile(intervals, .95), frameP99Ms: percentile(intervals, .99),
      cpuSubmissionMedianMs: percentile(cpu, .5), cpuSubmissionP95Ms: percentile(cpu, .95),
      framesOver33Ms: intervals.filter(v => v > 33.34).length, peakDrawCalls: Math.max(0, ...frames.map(f => f.calls)),
      peakTriangles: Math.max(0, ...frames.map(f => f.triangles)), qualityModes: [...new Set(frames.map(f => f.quality))],
      treeLodAtEnd: frames.at(-1)?.lod || null, notes: [
        'The viewer continuously rendered during capture; 2 s warmup excluded.',
        'Frame intervals include browser scheduling and rendering workload. CPU submission excludes GPU completion.',
        'Draw calls/triangles include shadow and postprocessing passes. Results apply only to the reported browser, viewport and renderer.',
        'This is a performance report, not a height-accuracy evaluation.' ] };
    capture = null; onComplete(last);
  }
  function download() {
    if (!last) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(last, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = `depthwizard-render-${Date.now()}.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return { start, cancel, record, download, get active() { return Boolean(capture); }, get result() { return last; } };
}
