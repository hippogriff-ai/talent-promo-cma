// Thin fetch wrapper over the gateway REST API (CONTRACT §2).

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8100";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function api<T>(path: string, opts?: { method?: string; body?: unknown }): Promise<T> {
  const hasBody = opts?.body !== undefined;
  const res = await fetch(`${API_URL}${path}`, {
    method: opts?.method ?? (hasBody ? "POST" : "GET"),
    headers: hasBody ? { "Content-Type": "application/json" } : undefined,
    body: hasBody ? JSON.stringify(opts?.body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const envelope = (await res.json()) as { error?: { message?: string } };
      if (envelope?.error?.message) message = envelope.error.message;
    } catch {
      // non-JSON error body — keep the status message
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}
