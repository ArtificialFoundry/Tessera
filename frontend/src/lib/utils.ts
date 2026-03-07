/** Shared utilities: toast, time formatting, polling. */

import { signal, type ReadonlySignal } from "@preact/signals";

// -- Toast system ------------------------------------------------------------

interface Toast {
  id: number;
  message: string;
  type: "success" | "error" | "info";
}

let _nextId = 0;
export const toasts = signal<Toast[]>([]);

export function toast(message: string, type: Toast["type"] = "info"): void {
  const id = ++_nextId;
  toasts.value = [...toasts.value, { id, message, type }];
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id);
  }, 4000);
}

// -- Ping status -------------------------------------------------------------

export const pingOk = signal(false);

async function checkPing(): Promise<void> {
  try {
    const r = await fetch("/api/v1/ping", { signal: AbortSignal.timeout(3000) });
    pingOk.value = r.ok;
  } catch {
    pingOk.value = false;
  }
}
checkPing();
setInterval(checkPing, 10_000);

// -- Time helpers ------------------------------------------------------------

export function timeAgo(ts: number | null | undefined): string {
  if (!ts) return "—";
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function formatTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

// -- Polling hook (non-React, signal-based) ----------------------------------

export function poll<T>(
  fetcher: () => Promise<T>,
  data: ReturnType<typeof signal<T>>,
  intervalMs: number,
): { start: () => void; stop: () => void; lastUpdated: ReturnType<typeof signal<number>>; consecutiveErrors: ReturnType<typeof signal<number>> } {
  let timer: ReturnType<typeof setInterval> | null = null;
  const lastUpdated = signal(0);
  const consecutiveErrors = signal(0);

  async function tick(): Promise<void> {
    try {
      data.value = await fetcher();
      lastUpdated.value = Date.now();
      consecutiveErrors.value = 0;
    } catch {
      consecutiveErrors.value++;
    }
  }

  return {
    start() {
      tick();
      timer = setInterval(tick, intervalMs);
    },
    stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    },
    lastUpdated,
    consecutiveErrors,
  };
}

// -- Escape HTML (safety) ----------------------------------------------------

const ESC_MAP: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ESC_MAP[c] ?? c);
}

// -- Active tab from server --------------------------------------------------

export function getActiveTab(): ReadonlySignal<string> {
  const tab = signal(
    document.querySelector<HTMLAnchorElement>(".nav-tab.active")?.getAttribute("data-tab") ?? "failover",
  );
  return tab;
}
