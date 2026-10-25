import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Download, FileJson } from "lucide-react";
import { api, downloadUrl, type Gate, type Readiness } from "../../lib/api";
import { useAppState } from "../../lib/store";
import { when } from "../../lib/format";
import { useToast } from "../ui";
import { Code, GateMark, useInvalidateInteg } from "./shared";

export function Gates({ gates }: { gates: Gate[] }) {
  return (
    <ul className="gates">
      {gates.map((g) => (
        <li key={g.id} className={`g-${g.status}`}>
          <span className="gid mono">{g.id}</span>
          <GateMark status={g.status} />
          <span className="gl">{g.label}</span>
          <span className="gd muted">{g.detail}</span>
        </li>
      ))}
    </ul>
  );
}

export function ExportView({ dealId, readiness }: { dealId: string; readiness: Readiness }) {
  const { reviewer } = useAppState();
  const toast = useToast();
  const invalidate = useInvalidateInteg(dealId);
  const [ack, setAck] = useState(false);
  const sign = useMutation({
    mutationFn: () => api.integ.signoff(dealId, reviewer, ack),
    onSuccess: () => { toast("Signed off — the import package is unlocked"); setAck(false); invalidate(); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const revoke = useMutation({
    mutationFn: () => api.integ.revoke(dealId, reviewer, "Revoked from the Export step"),
    onSuccess: () => { toast("Sign-off revoked"); invalidate(); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const pre = readiness.gates.filter((g) => g.phase !== "verify");
  const so = readiness.signoff;
  const live = so && !so.revoked_at;
  const signed = readiness.gates.find((g) => g.id === "G8")?.status === "pass";
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h3>Release gates · before export</h3>
          <span className="muted small">recomputed on every view — nothing is cached as “passed”</span></div>
        <div className="card-body"><Gates gates={pre} /></div>
      </section>

      <div className="grid split">
        <section className="card ticks">
          <div className="card-head"><h3>Sign-off</h3><span className="chip tone-muted">single reviewer</span></div>
          <div className="card-body stack tight">
            {signed && so ? (
              <>
                <p className="stamped"><span className="stamp-ink" aria-hidden="true">SIGNED</span> {so.statement}</p>
                <p className="muted small">by <b>{so.reviewer}</b> · {when(so.created_at)} · plan <Code>{so.plan_hash.slice(0, 12)}</Code> · {so.dry_run_id}</p>
                <div className="btn-row"><button className="btn ghost sm" onClick={() => revoke.mutate()} disabled={revoke.isPending}>Revoke sign-off</button></div>
              </>
            ) : (
              <>
                {live && <p className="warnline"><span aria-hidden="true">!</span><span>The earlier sign-off by {so!.reviewer} covers a different plan or dry run. Sign again.</span></p>}
                <p className="small">
                  You’re confirming this exact plan (<Code>{(readiness.plan_hash ?? "").slice(0, 12)}</Code>, v{readiness.plan_version}) and its latest dry run,
                  and approving the import files for loading. Any later change to the mapping voids the sign-off.
                </p>
                <label className="ack small">
                  <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} disabled={!readiness.signoff_ready} />
                  <span>I reviewed the mapping and dry run. I understand this is a single-reviewer sign-off — not an audit opinion — and
                    that only post-import verification shows what actually loaded.</span>
                </label>
                <div className="btn-row">
                  <button className="btn primary" disabled={!readiness.signoff_ready || !ack || !reviewer.trim() || sign.isPending}
                          onClick={() => sign.mutate()}>Sign off as {reviewer || "…"}</button>
                  {!readiness.signoff_ready && <span className="muted small">Unlocks when G1–G7 pass.</span>}
                </div>
              </>
            )}
          </div>
        </section>

        <section className="card">
          <div className="card-head"><h3>Offline import package</h3><span className="muted small">MergeProof writes to no vendor system</span></div>
          <div className="card-body stack tight">
            <pre className="tree-art mono small" aria-label="Package contents">{`mergeproof_import/
├── import/        CSV per table
├── crosswalk/     old key → new key
├── lineage.csv    cell → file·row·col
├── plan.json      signed mapping
├── manifest.json  sha256 per file
└── README.txt`}</pre>
            <div className="btn-row">
              {readiness.export_ready ? (
                <a className="btn primary" href={downloadUrl(`/deals/${dealId}/integrate/export.zip`)} download onClick={() => setTimeout(invalidate, 800)}>
                  <Download /> Download import package
                </a>
              ) : (
                <button className="btn primary" disabled title="Unlocks after sign-off (G1–G8)"><Download /> Download import package</button>
              )}
              <a className="btn ghost" href={downloadUrl(`/deals/${dealId}/integrate/blueprint.json`)} download><FileJson /> Plan blueprint (JSON)</a>
            </div>
            <p className="muted small">
              The package is rebuilt from the plan and must reproduce the signed-off dry run byte for byte, or the download is refused.
              Free-text cells that a spreadsheet would run as a formula are prefixed with an apostrophe and counted in the manifest.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
