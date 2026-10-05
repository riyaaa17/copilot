const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/** Plain amount: $1,234 or -$1,234. */
export const money = (n: number): string => usd.format(n);

/** Ledger convention: negatives in parentheses. */
export const ledger = (n: number): string => (n < 0 ? `(${usd.format(Math.abs(n))})` : usd.format(n));

export const pct = (n: number, digits = 1): string => `${n.toFixed(digits)}%`;
/** A difference between two figures: +$1,234, ($1,234) or "No change". */
export const change = (n: number): string => (Math.abs(n) < 1 ? "No change" : n > 0 ? `+${usd.format(n)}` : ledger(n));
/** Axis labels only: $450k. */
export const axisMoney = (n: number): string => {
  const sign = n < 0 ? "-" : "";
  const a = Math.abs(n);
  return a >= 1_000_000 ? `${sign}$${(a / 1_000_000).toFixed(1)}M` : `${sign}$${Math.round(a / 1000)}k`;
};
export const shortDate = (iso: string): string =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });

export const longDate = (iso: string): string =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

/** 'Recurring: bank_fees' -> 'Bank fees'. */
export const driverName = (name: string): string => {
  const clean = name.replace("Recurring: ", "").replace(/_/g, " ");
  return clean.charAt(0).toUpperCase() + clean.slice(1);
};

export const BEHAVIOR: Record<string, string> = {
  prompt: "Usually prompt",
  average: "Slightly late",
  slow: "Often late",
  chronic: "Chronically late",
  insufficient_data: "Not enough history",
};

export const TONE: Record<string, string> = {
  friendly: "Friendly reminder",
  firm: "Firm follow-up",
  urgent: "Urgent",
};