import { useEffect, useState } from "react";

export const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export interface Loadable<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/** Load data now and again whenever `deps` change. Keeps the old data on screen while reloading. */
export function useData<T>(load: () => Promise<T>, deps: readonly unknown[]): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ data: null, error: null, loading: true });
  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    load().then(
      (data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      },
      (e: unknown) => {
        if (!cancelled) setState({ data: null, error: errorMessage(e), loading: false });
      },
    );
    return () => {
      cancelled = true;
    };
  }, deps);
  return state;
}

/** Run a button action with a busy flag and an error message. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(errorMessage(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run, clearError: () => setError(null) };
}