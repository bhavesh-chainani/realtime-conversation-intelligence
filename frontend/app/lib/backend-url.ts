const DEFAULT_BACKEND_URL = "http://localhost:8000";

/** Backend base URL: NEXT_PUBLIC_BACKEND_URL, else a localStorage override, else localhost:8000. */
export function getBackendUrl(): string {
  if (process.env.NEXT_PUBLIC_BACKEND_URL) return process.env.NEXT_PUBLIC_BACKEND_URL;
  if (typeof window === "undefined") return DEFAULT_BACKEND_URL;
  try {
    return localStorage.getItem("BACKEND_URL") || DEFAULT_BACKEND_URL;
  } catch {
    return DEFAULT_BACKEND_URL;
  }
}
