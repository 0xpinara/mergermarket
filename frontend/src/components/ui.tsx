import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import type { FindingStatus, Severity } from "../lib/api";
import { DEAL_STATE, SEVERITY_LABEL, STATUS_GLYPH, STATUS_LABEL } from "../lib/format";

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="4.5" y="7" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <rect x="12.5" y="11" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <rect x="12.5" y="11" width="6" height="10" fill="#C6F36B" />
      <path d="M14 16.2l1.6 1.6 2.6-3" stroke="#0B1220" strokeWidth="1.5" fill="none" />
    </svg>
  );
}

export function Kicker({ sec, children, plain }: { sec?: string; children: ReactNode; plain?: boolean }) {
  return (
    <div className={`kicker${plain ? " plain" : ""}`}>
      {sec && <span className="sec">§{sec}</span>}
      <span>{children}</span>
    </div>
  );
}

export function SevChip({ severity }: { severity: Severity }) {
  return <span className={`chip sev sev-${severity}`}>{SEVERITY_LABEL[severity]}</span>;
}

export function StatusPill({ status, rereview }: { status: FindingStatus; rereview?: boolean }) {
  return (
    <span className={`status st-${status}`}>
      <span className="g" aria-hidden="true">{STATUS_GLYPH[status]}</span>
      {STATUS_LABEL[status]}
      {rereview && <span className="flag" title="Source data changed since this was reviewed">re-review</span>}
    </span>
  );
}

export function DealStateChip({ state }: { state: string }) {
  const s = DEAL_STATE[state] ?? { label: state, tone: "muted" };
  return <span className={`chip tone-${s.tone}`}>{s.label}</span>;
}

/** Segmented, terminal-style meter. Drawn with CSS so it renders identically in every font. */
export function AsciiBar({ fraction, width = 24 }: { fraction: number; width?: number }) {
  const f = Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0));
  const filled = f > 0 && f < 1 ? Math.min(width - 1, Math.max(1, Math.round(f * width))) : Math.round(f * width);
  return (
    <span className="bar segbar" aria-hidden="true" style={{ ["--w" as string]: width }}>
      <i style={{ width: `${(filled / width) * 100}%` }} />
    </span>
  );
}

export function Info({ children, label = "How this is calculated" }: { children: ReactNode; label?: string }) {
  const id = useId();
  return (
    <span className="tip">
      <button type="button" className="tip-btn" aria-label={label} aria-describedby={id}>
        i
      </button>
      <span role="tooltip" id={id} className="tip-body">
        {children}
      </span>
    </span>
  );
}

export function Empty({ art, title, children, actions }: { art?: string; title: string; children?: ReactNode;
                                                            actions?: ReactNode }) {
  return (
    <div className="empty">
      {art && <pre aria-hidden="true">{art}</pre>}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {actions && <div className="btn-row" style={{ justifyContent: "center" }}>{actions}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spin" aria-hidden="true" /> {label}…
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div className="banner danger" role="alert">
      <span className="ico" aria-hidden="true">✕</span>
      <span className="grow">{msg}</span>
      {onRetry && (
        <button className="btn sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- modal

function useFocusTrap(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const focusables = () =>
      Array.from(
        el?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((n) => n.offsetParent !== null);
    const first = el?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0];
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "Tab") {
        const f = focusables();
        if (!f.length) return;
        const a = f[0];
        const z = f[f.length - 1];
        if (e.shiftKey && document.activeElement === a) {
          e.preventDefault();
          z.focus();
        } else if (!e.shiftKey && document.activeElement === z) {
          e.preventDefault();
          a.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      previous?.focus?.();
    };
  }, [open, onClose]);
  return ref;
}

export function Modal({ title, kicker, onClose, children, footer }: {
  title: string; kicker?: string; onClose: () => void; children: ReactNode; footer?: ReactNode;
}) {
  const ref = useFocusTrap(true, onClose);
  const id = useId();
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={id} ref={ref}>
        <div className="modal-head">
          <div>
            {kicker && <Kicker plain>{kicker}</Kicker>}
            <h2 id={id}>{title}</h2>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </>
  );
}

export function Drawer({ label, onClose, children }: { label: string; onClose: () => void; children: ReactNode }) {
  const ref = useFocusTrap(true, onClose);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="drawer" role="dialog" aria-modal="true" aria-label={label} ref={ref}>
        {children}
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- toasts

interface Toast {
  id: number;
  text: string;
  kind: "ok" | "err";
}

const ToastCtx = createContext<(text: string, kind?: "ok" | "err") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "err" ? 6500 : 3800);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <span className="g" aria-hidden="true">{t.kind === "ok" ? "✓" : "✕"}</span>
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

export function useCountUp(target: number, duration = 700, enabled = true): number {
  const [v, setV] = useState(enabled ? 0 : target);
  const from = useRef(0);
  useEffect(() => {
    if (!enabled) {
      setV(target);
      return;
    }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setV(Math.round(a + (target - a) * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration, enabled]);
  return v;
}
