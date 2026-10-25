import { Fragment } from "react";
import type { UnitCell, UnitMap } from "../lib/api";
import { money } from "../lib/format";

type State = UnitCell["state"] | "pending";

const GLYPH: Record<State, string> = {
  verified: "■", issue: "◆", critical: "✕", review: "?", reviewed: "□", vacant: "·", pending: "▪",
};
const STATE_LABEL: Record<State, string> = {
  verified: "verified", issue: "discrepancy", critical: "critical", review: "needs review",
  reviewed: "reviewed", vacant: "vacant at cutover", pending: "not yet audited",
};

const ROOFS = [
  (n: number) => ({ acc: " ".repeat(Math.floor(n / 2)) + "╻", roof: "┌" + "─".repeat(n - 2) + "┐", wall: ["│", "│"] }),
  (n: number) => ({ acc: " ".repeat(Math.max(0, n - 5)) + "▗▄▖", roof: "▛" + "▀".repeat(n - 2) + "▜", wall: ["▌", "▐"] }),
  (n: number) => ({ acc: "", roof: "╔" + "═".repeat(n - 2) + "╗", wall: ["║", "║"] }),
  (n: number) => ({
    acc: " ".repeat(Math.floor(n / 2) - 1) + "╱╲",
    roof: "┏" + "━".repeat(n - 2) + "┓",
    wall: ["┃", "┃"],
  }),
];

interface Props {
  properties: UnitMap["properties"];
  variant?: "ink" | "paper";
  lit?: boolean;
  onPick?: (findingId: string) => void;
  pending?: boolean;
}

export function Skyline({ properties, variant = "ink", lit = false, onPick, pending = false }: Props) {
  let order = 0;
  return (
    <div className="skyline-wrap">
      <div className={`skyline ${variant}${lit ? " lit" : ""}`} role="group" aria-label="Unit-level verification map">
        {properties.map((p, pi) => {
          const floors = new Map<number, UnitCell[]>();
          for (const u of p.units) {
            const f = u.floor ?? 1;
            floors.set(f, [...(floors.get(f) ?? []), u]);
          }
          const levels = [...floors.keys()].sort((a, b) => b - a);
          const width = Math.max(...[...floors.values()].map((v) => v.length), 1);
          const n = 2 * width + 3;
          const shape = ROOFS[pi % ROOFS.length](n);
          const problems = p.units.filter((u) => ["critical", "issue", "review"].includes(u.state)).length;
          return (
            <div className="bldg" key={p.key}>
              <pre aria-hidden="true">{shape.acc || " "}</pre>
              <pre aria-hidden="true">{shape.roof}</pre>
              <div className="floors">
                {levels.map((lv) => {
                  const cells = (floors.get(lv) ?? []).slice().sort((a, b) => (a.norm ?? a.label).localeCompare(b.norm ?? b.label));
                  return (
                    <div className="floor" key={lv}>
                      <span className="wall" aria-hidden="true">{shape.wall[0]} </span>
                      {Array.from({ length: width }).map((_, i) => {
                        const u = cells[i];
                        const sep = i < width - 1 ? " " : "";
                        if (!u) return <Fragment key={i}><span className="win s-vacant" aria-hidden="true"> </span>{sep}</Fragment>;
                        const st: State = pending ? "pending" : u.state;
                        const label = `Unit ${u.label} · ${p.name} — ${STATE_LABEL[st]}${u.tenant ? ` · ${u.tenant}` : ""}${
                          u.rent ? ` · ${money(u.rent)}/mo` : ""}${u.top ? ` · ${u.top.title}` : ""}`;
                        const style = { ["--d" as string]: `${(order++ % 400) * 4}ms` };
                        const target = u.top?.id ?? u.findings[0];
                        return (
                          <Fragment key={u.key}>
                            {target && onPick && !pending ? (
                              <button type="button" className={`win s-${st}`} style={style} title={label} aria-label={label}
                                      onClick={() => onPick(target)}>
                                {GLYPH[st]}
                              </button>
                            ) : (
                              <span className={`win s-${st}`} style={style} title={label}>{GLYPH[st]}</span>
                            )}
                            {sep && <span aria-hidden="true">{sep}</span>}
                          </Fragment>
                        );
                      })}
                      <span className="wall" aria-hidden="true"> {shape.wall[1]}</span>
                    </div>
                  );
                })}
              </div>
              <pre className="ground" aria-hidden="true">{"▀".repeat(n)}</pre>
              <div className="name">
                {p.name}
                {!pending && problems > 0 && <span className="sr-only">, {problems} units need attention</span>}
              </div>
            </div>
          );
        })}
      </div>
      <div className={`legend ${variant}`} aria-hidden={pending}>
        {(pending ? (["pending", "vacant"] as State[]) : (["verified", "issue", "critical", "review", "reviewed", "vacant"] as State[])).map((s) => (
          <span key={s}>
            <b className={`win s-${s}`}>{GLYPH[s]}</b>
            {STATE_LABEL[s]}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Decorative portfolio for the empty state — clearly labelled as illustrative. */
export function IllustrativeSkyline() {
  const seed = [5, 4, 6, 3, 5, 4];
  const states: UnitCell["state"][] = ["verified", "verified", "verified", "vacant", "verified", "issue", "verified",
    "critical", "verified", "verified", "review", "verified"];
  let k = 0;
  const properties = seed.map((floors, i) => ({
    key: `demo-${i}`,
    name: ["Maple", "Harbor", "Quince", "Linden", "Fir", "Alder"][i],
    address: null,
    units: Array.from({ length: floors * (3 + (i % 3)) }).map((_, j) => {
      const per = 3 + (i % 3);
      const st = states[(k++ * 7 + i) % states.length];
      return {
        key: `demo-${i}-${j}`, label: `${Math.floor(j / per) + 1}${String.fromCharCode(65 + (j % per))}`,
        norm: null, floor: Math.floor(j / per) + 1, lease: null, tenant: null, rent: null, state: st,
        findings: [], top: null,
      };
    }),
  }));
  return <Skyline properties={properties} variant="ink" lit />;
}
