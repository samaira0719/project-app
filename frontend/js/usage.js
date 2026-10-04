/** Best-effort visible-page heartbeat. Tracking failures never affect the app. */
const KEY = "dw_usage_session";
const DEFAULT_INTERVAL_MS = 20_000;

function sessionId() {
  try {
    let id = sessionStorage.getItem(KEY);
    if (!id || !/^[A-Za-z0-9-]{8,64}$/.test(id)) {
      id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      sessionStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

export function startUsageTracking() {
  try {
    const id = sessionId();
    let intervalMs = DEFAULT_INTERVAL_MS;
    let timer;
    const send = (beacon = false) => {
      try {
        const body = JSON.stringify({ session_id: id });
        if (beacon && navigator.sendBeacon) {
          navigator.sendBeacon("/api/usage/heartbeat", new Blob([body], { type: "application/json" }));
        } else {
          fetch("/api/usage/heartbeat", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body, keepalive: beacon,
          }).catch(() => {});
        }
      } catch { /* analytics are nonessential */ }
    };
    const stop = () => { clearInterval(timer); timer = undefined; };
    const start = () => {
      if (document.hidden) return;
      if (!timer) {
        send();
        timer = setInterval(() => send(), intervalMs);
      }
    };
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { stop(); send(true); }
      else start();
    });
    window.addEventListener("pagehide", () => { stop(); send(true); });
    fetch("/api/usage/config").then((response) => response.ok ? response.json() : null)
      .then((config) => {
        const seconds = Number(config?.heartbeat_interval);
        if (Number.isFinite(seconds) && seconds >= 5) intervalMs = seconds * 1000;
      }).catch(() => {}).finally(start);
  } catch { /* tracking must never interfere with application startup */ }
}
