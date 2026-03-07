/** Shared layout components: Shell, Nav, ToastContainer, Modal, ConfirmDialog. */

import { type ComponentChildren } from "preact";
import { signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import { pingOk, toasts } from "@/lib/utils";
import { adminToken, clearAdminToken, setAdminToken, authPromptOpen, resolveAuth, cancelAuth, api, ApiError } from "@/lib/api";

// -- Modal state (global singleton) ------------------------------------------

export const activeModal = signal<string | null>(null);

export function openModal(name: string): void {
  activeModal.value = name;
}
export function closeModal(): void {
  activeModal.value = null;
}

// -- Confirm dialog ----------------------------------------------------------

interface ConfirmState {
  title: string;
  message: string;
  actionLabel: string;
  onConfirm: (() => void) | null;
}

const confirmState = signal<ConfirmState>({
  title: "",
  message: "",
  actionLabel: "Delete",
  onConfirm: null,
});

let _prevModal: string | null = null;

export function showConfirm(
  title: string,
  message: string,
  actionLabel: string,
  onConfirm: () => void,
): void {
  _prevModal = activeModal.value;
  confirmState.value = { title, message, actionLabel, onConfirm };
  activeModal.value = "confirm";
}

function cancelConfirm(): void {
  activeModal.value = _prevModal;
  _prevModal = null;
  confirmState.value = { ...confirmState.value, onConfirm: null };
}

function executeConfirm(): void {
  const cb = confirmState.value.onConfirm;
  confirmState.value = { ...confirmState.value, onConfirm: null };
  activeModal.value = _prevModal;
  _prevModal = null;
  cb?.();
}

// -- Components --------------------------------------------------------------

interface ShellProps {
  activeTab: string;
  children: ComponentChildren;
}

export function Shell({ activeTab, children }: ShellProps) {
  // Global escape key handler
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && activeModal.value) {
        if (activeModal.value === "confirm") cancelConfirm();
        else closeModal();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <div class="orb orb-1" />
      <div class="orb orb-2" />
      <div class="orb orb-3" />
      <div class="app">
        <Nav activeTab={activeTab} />
        {children}
        <ConfirmDialog />
        <AuthDialog />
        <ToastContainer />
      </div>
    </>
  );
}

function Nav({ activeTab }: { activeTab: string }) {
  const tabs = [
    { id: "failover", label: "Failover", href: "/failover" },
    { id: "dhcp", label: "DHCP", href: "/dhcp" },
    { id: "protection", label: "Protection", href: "/protection" },
    { id: "servers", label: "Servers", href: "/servers" },
    { id: "voters", label: "Voters", href: "/voters" },
  ];

  return (
    <nav class="nav fade-up">
      <a class="nav-brand" href="/failover">
        <svg viewBox="0 0 24 24" fill="none" stroke="url(#g)" stroke-width="2" stroke-linecap="round">
          <defs>
            <linearGradient id="g" x1="0" y1="0" x2="24" y2="24">
              <stop stop-color="#6366f1" />
              <stop offset="1" stop-color="#ec4899" />
            </linearGradient>
          </defs>
          <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
        </svg>
        Tessera
      </a>
      <div class="nav-status">
        <span class={`dot ${pingOk.value ? "online" : "offline"}`} />
        <span>{pingOk.value ? "Connected" : "Offline"}</span>
      </div>
      <div class="nav-tabs">
        {tabs.map((t) => (
          <a key={t.id} class={`nav-tab${activeTab === t.id ? " active" : ""}`} href={t.href}>
            {t.label}
          </a>
        ))}
      </div>
      {adminToken.value && (
        <button
          class="btn btn-sm btn-ghost"
          style="margin-left:8px;font-size:11px;color:var(--text-dim)"
          onClick={() => { clearAdminToken(); }}
          title="Clear admin token"
        >
          🔓 Logout
        </button>
      )}
    </nav>
  );
}

function ToastContainer() {
  return (
    <div class="toast-container" id="toast-container">
      {toasts.value.map((t) => (
        <div key={t.id} class={`toast ${t.type}`}>
          {t.message}
        </div>
      ))}
    </div>
  );
}

function AuthDialog() {
  const open = authPromptOpen.value;
  const [tokenInput, setTokenInput] = useState("");
  const [error, setError] = useState("");
  const [verifying, setVerifying] = useState(false);

  // Reset state when dialog opens
  useEffect(() => {
    if (open) { setTokenInput(""); setError(""); setVerifying(false); }
  }, [open]);

  async function handleSubmit() {
    const trimmed = tokenInput.trim();
    if (!trimmed) { setError("Token is required"); return; }
    setVerifying(true);
    setError("");
    // Temporarily set the token so the verify request includes it
    setAdminToken(trimmed);
    try {
      await api.verifyToken();
      resolveAuth();
    } catch (e) {
      clearAdminToken();
      if (e instanceof ApiError) {
        setError(e.status === 401 ? "Invalid token" : e.message);
      } else {
        setError("Verification failed");
      }
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div class={`modal-overlay${open ? " open" : ""}`} onClick={(e) => { if (e.target === e.currentTarget) cancelAuth(); }}>
      <div class="modal" style="width:420px">
        <button class="modal-close" onClick={cancelAuth}>✕</button>
        <h2>🔐 Admin Authentication</h2>
        <p style="font-size:13px;color:var(--text-dim);margin-bottom:16px">
          Enter the admin API key to perform this action.
        </p>
        <input
          class="input"
          type="password"
          placeholder="Admin API key"
          value={tokenInput}
          onInput={(e) => { setTokenInput((e.target as HTMLInputElement).value); setError(""); }}
          onKeyDown={(e) => { if (e.key === "Enter" && !verifying) handleSubmit(); }}
          style="width:100%;margin-bottom:8px"
          autofocus
        />
        {error && <div style="font-size:12px;color:var(--red);margin-bottom:8px">{error}</div>}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn btn-ghost" onClick={cancelAuth}>Cancel</button>
          <button class="btn btn-accent" disabled={verifying || !tokenInput.trim()} onClick={handleSubmit}>
            {verifying ? "Verifying…" : "Authenticate"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ConfirmDialog() {
  const open = activeModal.value === "confirm";
  const { title, message, actionLabel } = confirmState.value;

  return (
    <div class={`modal-overlay${open ? " open" : ""}`} onClick={(e) => { if (e.target === e.currentTarget) cancelConfirm(); }}>
      <div class="modal" style="width:440px">
        <button class="modal-close" onClick={cancelConfirm}>✕</button>
        <h2>{title}</h2>
        <div class="confirm-message">{message}</div>
        <div class="confirm-actions">
          <button class="btn btn-ghost" onClick={cancelConfirm}>Cancel</button>
          <button class="btn btn-danger" onClick={executeConfirm}>{actionLabel}</button>
        </div>
      </div>
    </div>
  );
}

// -- Modal wrapper component -------------------------------------------------

interface ModalProps {
  name: string;
  width?: string;
  children: ComponentChildren;
  onClose?: () => void;
}

export function Modal({ name, width, children, onClose }: ModalProps) {
  const open = activeModal.value === name;
  const handleClose = () => { closeModal(); onClose?.(); };
  return (
    <div
      class={`modal-overlay${open ? " open" : ""}`}
      onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}
    >
      <div class="modal" style={width ? `width:${width}` : undefined}>
        <button class="modal-close" onClick={handleClose}>✕</button>
        {children}
      </div>
    </div>
  );
}

// -- Pagination ---------------------------------------------------------------

interface PaginatorProps {
  total: number;
  offset: number;
  limit: number;
  onPage: (offset: number) => void;
}

export function Paginator({ total, offset, limit, onPage }: PaginatorProps) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <div class="paginator">
      <button class="btn btn-sm btn-ghost" disabled={offset === 0} onClick={() => onPage(offset - limit)}>
        ← Prev
      </button>
      <span class="paginator-info">{page} / {pages} ({total} total)</span>
      <button class="btn btn-sm btn-ghost" disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}>
        Next →
      </button>
    </div>
  );
}
