import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { api, type Readiness, type Summary } from "../../lib/api";
import { pct } from "../../lib/format";

const STATE: Record<Readiness["state"], { label: string; next: string; tone: string }> = {
  no_plan: { label: "No integration plan yet", next: "Plan the import", tone: "muted" },
  planning: { label: "Planning · columns to decide", next: "Finish the mapping review", tone: "amber" },
  simulating: { label: "Mapping decided · dry run pending or failing", next: "Run the dry run", tone: "amber" },
  ready_to_sign: { label: "Dry run clean · ready to sign off", next: "Sign off and export", tone: "lime" },
  verifying: { label: "Signed off · verify after the load", next: "Verify the import", tone: "amber" },
  ready_to_release: { label: "Every gate passes · receipt ready", next: "Issue the release receipt", tone: "lime" },
  released: { label: "Released · receipt issued", next: "View the receipt", tone: "lime" },
};

/** Two independent statuses side by side: will the import be safe (readiness), and did it work (continuity). */
export function ReleaseTrack({ dealId, summary }: { dealId: string; summary?: Summary }) {
  const q = useQuery({ queryKey: ["integ", dealId], queryFn: () => api.integ.overview(dealId) });
  const r = q.data?.readiness;
  if (!r) return null;
  const st = STATE[r.state];
  const c = summary?.continuity;
  return (
    <section className="track card">
      <div className="tr-col">
        <div className="k">Integration readiness</div>
        <div className="pips" aria-label={`${r.gates.filter((g) => g.status === "pass").length} of ${r.gates.length || 11} gates pass`}>
          {(r.gates.length ? r.gates : Array.from({ length: 11 }, (_, i) => ({ id: `G${i + 1}`, status: "pending", label: "" }))).map((g) => (
            <span key={g.id} className={`pip p-${g.status}`} title={`${g.id} ${g.label} — ${g.status}`}>{g.id.slice(1)}</span>
          ))}
        </div>
        <div className="row"><span className={`chip tone-${st.tone}`}>{st.label}</span></div>
      </div>
      <div className="tr-col">
        <div className="k">Data continuity</div>
        <div className="big mono">{c?.score != null ? pct(c.score) : "—"}</div>
        <div className="muted small">{c ? `${c.accounted}/${c.active_leases} active leases accounted for · ${pct(c.clean_pct)} clean` : "Run the audit to measure it"}</div>
      </div>
      <Link className="btn ink tr-go" to={`/deals/${dealId}/integrate`}>{st.next} <ArrowRight /></Link>
    </section>
  );
}
