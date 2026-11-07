let csrf = "";
export const setCSRF = (token: string) => {
  csrf = token;
};
export class APIError extends Error {
  constructor(
    public status: number,
    message: string,
    public code: string,
  ) {
    super(message);
  }
}
export async function api<T = any>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD"].includes(init.method || "GET") && csrf)
    headers.set("X-CSRF-Token", csrf);
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch {
      data = { error: { message: response.statusText, code: "network_error" } };
    }
    if (response.status === 401 && !path.includes("/auth/login"))
      window.dispatchEvent(new Event("modeldock:unauthorized"));
    throw new APIError(
      response.status,
      data.error?.message || "Request failed",
      data.error?.code || "unknown",
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export async function raw(path: string, body: unknown, signal?: AbortSignal) {
  const response = await fetch(path, {
    method: "POST",
    body: JSON.stringify(body),
    credentials: "same-origin",
    signal,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
  });
  if (!response.ok) {
    const data = await response.json();
    throw new APIError(
      response.status,
      data.error?.message || "Request failed",
      data.error?.code || "unknown",
    );
  }
  return response;
}
export function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}
export function formatDate(value: number | null | undefined) {
  return value ? new Date(value * 1000).toLocaleString() : "—";
}
export async function consumeSSE(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onDelta: (text: string) => void,
) {
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const event = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      for (const line of event.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") return;
        const parsed = JSON.parse(payload);
        if (parsed.error)
          throw new APIError(502, parsed.error.message, parsed.error.code);
        const text = parsed.choices?.[0]?.delta?.content;
        if (text) onDelta(text);
      }
    }
    if (done) break;
  }
}
