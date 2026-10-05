import type { ReactNode } from "react";
import { ledger } from "../format";

export function Notice({ tone = "info", children }: { tone?: "info" | "error" | "warn"; children: ReactNode }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Pill({ tone = "plain", children }: { tone?: "plain" | "green" | "red" | "amber"; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function Loading({ children }: { children?: ReactNode }) {
  return (
    <p className="loading" role="status">
      {children ?? "Loading…"}
    </p>
  );
}

/** An amount in ledger style: negatives in red parentheses. */
export function Amount({ value }: { value: number }) {
  return <span className={value < 0 ? "num neg" : "num"}>{ledger(value)}</span>;
}