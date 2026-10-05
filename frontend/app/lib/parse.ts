// Defensive readers for backend JSON, and the JSON POST every endpoint uses.

export function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function obj(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

/** POST `body` as JSON; resolves to the parsed reply, or null on a network error or non-2xx status. */
export async function postJson(url: string, body: unknown, signal?: AbortSignal): Promise<unknown> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    return res.ok ? await res.json() : null;
  } catch (err) {
    if (signal?.aborted) throw err;
    console.error(`[POST ${url}] failed:`, err);
    return null;
  }
}
