import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Deal } from "./api";

function read(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode) — keep in memory only */
  }
}

interface AppState {
  dealId: string | null;
  setDealId: (id: string | null) => void;
  reviewer: string;
  setReviewer: (name: string) => void;
  deals: Deal[];
  dealsLoading: boolean;
}

const Ctx = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [dealId, setDealIdState] = useState<string | null>(() => read("mp.deal", "") || null);
  const [reviewer, setReviewerState] = useState<string>(() => read("mp.reviewer", "Demo Reviewer"));
  const dealsQ = useQuery({ queryKey: ["deals"], queryFn: api.deals });

  const setDealId = useCallback((id: string | null) => {
    setDealIdState(id);
    write("mp.deal", id);
  }, []);
  const setReviewer = useCallback((name: string) => {
    setReviewerState(name);
    write("mp.reviewer", name);
  }, []);

  // Fall back to the most recent audit when the remembered one no longer exists.
  useEffect(() => {
    if (!dealsQ.data) return;
    if (dealId && dealsQ.data.some((d) => d.id === dealId)) return;
    setDealId(dealsQ.data[0]?.id ?? null);
  }, [dealsQ.data, dealId, setDealId]);

  const value = useMemo(
    () => ({ dealId, setDealId, reviewer, setReviewer, deals: dealsQ.data ?? [], dealsLoading: dealsQ.isLoading }),
    [dealId, setDealId, reviewer, setReviewer, dealsQ.data, dealsQ.isLoading],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("AppStateProvider missing");
  return v;
}

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    const fn = () => setReduced(mq.matches);
    mq.addEventListener?.("change", fn);
    return () => mq.removeEventListener?.("change", fn);
  }, []);
  return reduced;
}
