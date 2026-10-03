/** Display helpers. Strings live here so the UI can be translated later (i18n-ready). */

export function inr(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(paise / 100);
}

export const OPTION_LABELS: Record<string, string> = {
  exchange: "Exchange",
  replacement: "Replacement",
  store_credit: "Store credit",
  refund: "Refund (we collect the item)",
  keep_item_refund: "Refund, keep the item",
};

export const METHOD_LABELS: Record<string, string> = {
  source: "Original payment method",
  store_credit: "Store credit",
  bank_transfer: "Bank transfer",
  upi: "UPI",
};

export const REASONS: [string, string][] = [
  ["", "Let the assistant work it out"],
  ["size_fit", "Size or fit"],
  ["damaged", "Arrived damaged"],
  ["defective", "Not working / defective"],
  ["wrong_item", "Wrong item"],
  ["not_as_described", "Not as described"],
  ["changed_mind", "Changed my mind"],
];

export const WISHES: [string, string][] = [
  ["", "No preference"],
  ["refund", "Refund"],
  ["exchange", "Exchange"],
  ["replacement", "Replacement"],
  ["store_credit", "Store credit"],
];

export const STATUS_LABELS: Record<string, string> = {
  waiting: "In progress",
  escalated: "With a specialist",
  closed: "Closed",
  open: "Open",
};

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function label(value: string | null | undefined): string {
  return (value || "—").replace(/_/g, " ");
}
