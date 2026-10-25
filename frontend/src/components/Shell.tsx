import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Menu, Plus, Sparkles } from "lucide-react";
import { api } from "../lib/api";
import { useAppState } from "../lib/store";
import { DEAL_STATE } from "../lib/format";
import { DealForm } from "./DealForm";
import { Logo, useToast } from "./ui";

export function useSampleLoader() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const { setDealId } = useAppState();
  return useMutation({
    mutationFn: (reset: boolean = false) => api.sample(reset),
    onSuccess: ({ deal, created }) => {
      setDealId(deal.id);
      qc.invalidateQueries({ queryKey: ["deals"] });
      qc.removeQueries({ queryKey: ["summary", deal.id] });
      toast(created ? "Sample acquisition loaded — both exports are ready" : "Opened your sample acquisition");
      nav(`/deals/${deal.id}/workspace`);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
}

function DealSwitcher() {
  const { deals, dealId, setDealId } = useAppState();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const nav = useNavigate();
  const loc = useLocation();
  const sample = useSampleLoader();
  const current = deals.find((d) => d.id === dealId);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const pick = (id: string) => {
    setDealId(id);
    setOpen(false);
    const m = loc.pathname.match(/^\/deals\/[^/]+\/(\w+)/);
    nav(m ? `/deals/${id}/${m[1]}` : "/");
  };

  return (
    <div className="deal-switch" ref={ref}>
      <button className="deal-switch-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <span className="glyph" aria-hidden="true">{current ? "A×B" : "+"}</span>
        <span style={{ minWidth: 0 }}>
          <span className="k">Current audit</span>
          <span className="t">{current ? current.title : "No audit selected"}</span>
        </span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {open && (
        <div className="menu" role="menu">
          {deals.map((d) => (
            <button key={d.id} role="menuitem" className={`item${d.id === dealId ? " current" : ""}`} onClick={() => pick(d.id)}>
              <span style={{ minWidth: 0 }}>
                {d.title}
                <small>
                  {DEAL_STATE[d.status.state]?.label ?? d.status.state}
                  {d.is_sample ? " · synthetic sample" : ""}
                </small>
              </span>
            </button>
          ))}
          {deals.length > 0 && <div className="sep" />}
          <button role="menuitem" className="item" onClick={() => { setOpen(false); setCreating(true); }}>
            <Plus size={16} aria-hidden="true" /> Start audit with my files…
          </button>
          <button role="menuitem" className="item" disabled={sample.isPending}
                  onClick={() => { setOpen(false); sample.mutate(false); }}>
            <Sparkles size={16} aria-hidden="true" /> Try sample acquisition
          </button>
        </div>
      )}
      {creating && <DealForm onClose={() => setCreating(false)} />}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { dealId, reviewer, setReviewer } = useAppState();
  const [navOpen, setNavOpen] = useState(false);
  const loc = useLocation();
  const summary = useQuery({
    queryKey: ["summary", dealId],
    queryFn: () => api.summary(dealId!),
    enabled: !!dealId,
  });
  useEffect(() => setNavOpen(false), [loc.pathname]);

  const open = summary.data?.kpis?.findings_open;
  const d = dealId ? `/deals/${dealId}` : null;
  const items: { to: string | null; label: string; idx: string; badge?: ReactNode; end?: boolean }[] = [
    { to: "/", label: "Command Center", idx: "01", end: true },
    { to: d && `${d}/workspace`, label: "Audit Workspace", idx: "02" },
    {
      to: d && `${d}/findings`, label: "Findings", idx: "03",
      badge: open !== undefined ? <span className={`count${open ? "" : " calm"}`}>{open}</span> : null,
    },
    { to: d && `${d}/proof`, label: "Continuity Proof", idx: "04" },
  ];

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className={`sidebar${navOpen ? " open" : ""}`} aria-label="Primary">
        <NavLink to="/" className="brand" aria-label="MergeProof home">
          <Logo />
          <span>
            <span className="brand-word">
              Merge<em>Proof</em>
            </span>
            <span className="brand-tag">Every record accounted for</span>
          </span>
        </NavLink>
        <nav className="nav">
          <div className="nav-label">Audit</div>
          {items.map((it) =>
            it.to ? (
              <NavLink key={it.idx} to={it.to} end={it.end} className={({ isActive }) => (isActive ? "active" : "")}>
                <span className="idx">{it.idx}</span>
                <span>{it.label}</span>
                {it.badge}
              </NavLink>
            ) : (
              <a key={it.idx} className="disabled" aria-disabled="true">
                <span className="idx">{it.idx}</span>
                <span>{it.label}</span>
              </a>
            ),
          )}
          <div className="nav-label">Company</div>
          <NavLink to="/intel" className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="idx">05</span>
            <span>Deal Intelligence</span>
          </NavLink>
          <NavLink to="/about" className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="idx">06</span>
            <span>About &amp; pitch</span>
          </NavLink>
        </nav>
        <div className="sidebar-foot">
          <div className="synthetic-tag">Synthetic demonstration data</div>
          <div>Audits customer-provided exports.</div>
          <div>No vendor connections.</div>
        </div>
      </aside>
      {navOpen && <div className="scrim" style={{ zIndex: 65 }} onClick={() => setNavOpen(false)} />}
      <div className="main">
        <header className="topbar">
          <button className="icon-btn menu-btn" onClick={() => setNavOpen(true)} aria-label="Open navigation">
            <Menu />
          </button>
          <DealSwitcher />
          <div className="grow" />
          <div className="reviewer">
            <label htmlFor="reviewer-name">Reviewer</label>
            <input id="reviewer-name" value={reviewer} maxLength={60}
                   onChange={(e) => setReviewer(e.target.value)} placeholder="Your name"
                   aria-describedby="reviewer-hint" />
            <span id="reviewer-hint" className="sr-only">Saved with every review decision</span>
          </div>
        </header>
        <main id="main" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}
