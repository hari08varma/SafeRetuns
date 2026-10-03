/** Small fetch wrapper: per-audience tokens (customer / staff), refresh once on 401. */

export type Audience = "customer" | "staff";

const KEYS: Record<Audience, string> = { customer: "sr.customer", staff: "sr.staff" };
const LOGIN: Record<Audience, string> = { customer: "/login", staff: "/console/login" };

type Tokens = { access_token: string; refresh_token: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function read(a: Audience): Tokens | null {
  if (typeof window === "undefined") return null;
  try {
    return JSON.parse(localStorage.getItem(KEYS[a]) || "null");
  } catch {
    return null;
  }
}

export function saveTokens(a: Audience, t: Tokens): void {
  localStorage.setItem(KEYS[a], JSON.stringify(t));
}

export function signOut(a: Audience): void {
  localStorage.removeItem(KEYS[a]);
  window.location.href = LOGIN[a];
}

export function signedIn(a: Audience): boolean {
  return read(a) !== null;
}

async function refresh(a: Audience): Promise<boolean> {
  const t = read(a);
  if (!t) return false;
  const r = await fetch("/api/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: t.refresh_token }),
  });
  if (!r.ok) return false;
  saveTokens(a, await r.json());
  return true;
}

export async function api<T>(
  audience: Audience | null,
  path: string,
  init: RequestInit = {},
  retried = false,
): Promise<T> {
  const headers = new Headers(init.headers);
  const t = audience ? read(audience) : null;
  if (t) headers.set("Authorization", `Bearer ${t.access_token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const r = await fetch(`/api/v1${path}`, { ...init, headers });
  if (r.status === 401 && audience) {
    if (!retried && (await refresh(audience))) return api<T>(audience, path, init, true);
    signOut(audience);
  }
  if (!r.ok) {
    let message = r.statusText;
    try {
      const body = await r.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(r.status, message);
  }
  if (r.status === 204) return undefined as T;
  const type = r.headers.get("content-type") || "";
  return (type.includes("json") ? r.json() : r.blob()) as Promise<T>;
}

export const post = <T>(a: Audience | null, path: string, body?: unknown) =>
  api<T>(a, path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
