import { useQuery } from "@tanstack/react-query";
import { api, type ProfileFile, type Relationship } from "../../lib/api";
import { num } from "../../lib/format";
import { ErrorBox, Info, Loading } from "../ui";
import { Code } from "./shared";

function pctOf(n: number, d: number) {
  return d ? `${Math.round((n / d) * 100)}%` : "—";
}

function RefMap({ files, rels }: { files: ProfileFile[]; rels: Relationship[] }) {
  const inbound = (f: ProfileFile, col: string) => rels.filter((r) => r.to.file === f.file && r.to.column === col);
  const withKeys = files.filter((f) => f.keys.length);
  return (
    <div className="refmap" role="list" aria-label="Which columns other files point at">
      {withKeys.map((f) => (
        <div key={f.snapshot_id} className="rm-file" role="listitem">
          <div className="rm-head">
            <b>{f.file}</b> <span className="muted">· {f.entity_label.toLowerCase()} · {num(f.rows)} rows</span>
          </div>
          {f.keys
            .filter((k) => k === f.primary_key || !f.columns.find((c) => c.column === k)?.mapped_to)
            .map((k) => {
              const ins = inbound(f, k);
              return (
                <div key={k} className={`rm-key${ins.length ? "" : " lonely"}`}>
                  <span className="k">
                    <span className="g" aria-hidden="true">⚿</span> {k}
                    {k === f.primary_key && <span className="chip tone-muted">audit key</span>}
                  </span>
                  <span className="ins">
                    {ins.length ? ins.map((r) => (
                      <span key={r.from.file + r.from.column} className="in">
                        <span className="wire" aria-hidden="true">◀━━</span>
                        <b>{Math.round(r.inclusion * 100)}%</b>
                        <span className="wire" aria-hidden="true">━━</span> {r.from.file} · <Code>{r.from.column}</Code>
                        <span className="muted"> ({num(r.values - r.missing)}/{num(r.values)} values)</span>
                      </span>
                    )) : (
                      <span className="in muted">
                        <span className="wire" aria-hidden="true">◀┄┄</span> unique, but no other file references these values
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
        </div>
      ))}
    </div>
  );
}

function FileProfile({ f }: { f: ProfileFile }) {
  return (
    <details className="card prof">
      <summary>
        <span className="t">{f.file}</span>
        <span className="muted">{f.entity_label} · {num(f.rows)} rows · {f.columns.length} columns</span>
        <span className="mono muted small">#{f.content_hash.slice(0, 10)}</span>
      </summary>
      <div className="table-wrap">
        <table className="map-table prof-table">
          <thead>
            <tr><th>Column</th><th>Type</th><th>Blank</th><th>Distinct</th><th>Shape</th><th>Examples</th><th>Audit field</th></tr>
          </thead>
          <tbody>
            {f.columns.map((c) => (
              <tr key={c.column}>
                <td className="mono">
                  {c.key_candidate && <span title="Unique and complete — could be a key" aria-label="key candidate">⚿ </span>}
                  {c.column}
                </td>
                <td><span className="chip tone-muted">{c.type}</span></td>
                <td className="mono">{c.blank ? pctOf(c.blank, c.rows) : "0"}</td>
                <td className="mono">{c.unique ? "all unique" : num(c.distinct)}</td>
                <td className="mono muted">{c.patterns[0]?.shape ?? "—"}</td>
                <td className="sample">{c.pii ? <span className="pii">personal data · hidden</span> : (c.examples ?? []).join(" · ")}</td>
                <td className="mono muted">{c.mapped_to ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export function ProfileView({ dealId }: { dealId: string }) {
  const q = useQuery({ queryKey: ["integ", dealId, "profile"], queryFn: () => api.integ.profile(dealId) });
  if (q.isLoading) return <Loading label="Profiling the legacy exports" />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />;
  const p = q.data!;
  const rows = p.files.reduce((a, f) => a + f.rows, 0);
  return (
    <div className="stack">
      <div className="contract">
        <div>
          <div className="k">Source contract</div>
          <div className="v mono">{p.fingerprint.slice(0, 12)}<span className="muted">…</span></div>
        </div>
        <div><div className="k">Files</div><div className="v mono">{p.files.length}</div></div>
        <div><div className="k">Rows</div><div className="v mono">{num(rows)}</div></div>
        <div><div className="k">References found</div><div className="v mono">{p.relationships.length}</div></div>
        <p className="muted small">
          Profiled from the legacy exports themselves: types, blanks, keys, and which columns other files point at.
          If an export changes, this fingerprint changes and the plan asks you to rebase.
          <Info>Value inclusion: a column “references” a key when its values occur in that key column. 100% means every
            value resolves. It’s the evidence used to pick keys — not the column’s name.</Info>
        </p>
      </div>
      <section className="card">
        <div className="card-head"><h3>Who points at what</h3><span className="muted small">keys and the files that reference them</span></div>
        <div className="card-body"><RefMap files={p.files} rels={p.relationships} /></div>
      </section>
      <div className="stack tight">
        {p.files.map((f) => <FileProfile key={f.snapshot_id} f={f} />)}
      </div>
    </div>
  );
}
