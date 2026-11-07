import { describe, it, expect, vi, afterEach } from "vitest";
import { api, consumeSSE, setCSRF, APIError } from "./api";

function reader(parts: string[]) {
  let index = 0;
  return {
    read: async () =>
      index < parts.length
        ? { done: false, value: new TextEncoder().encode(parts[index++]) }
        : { done: true, value: undefined },
  } as ReadableStreamDefaultReader<Uint8Array>;
}
afterEach(() => vi.unstubAllGlobals());
describe("stream protocol", () => {
  it("handles fragmented SSE and ignores final usage chunks", async () => {
    const output: string[] = [];
    await consumeSSE(
      reader([
        'data: {"choices":[{"delta":{"con',
        'tent":"hello"}}]}\n',
        '\ndata: {"choices":[],"usage":{"total_tokens":2}}\n\ndata: [DONE]\n\n',
      ]),
      (part) => output.push(part),
    );
    expect(output.join("")).toBe("hello");
  });
  it("surfaces stream errors", async () => {
    await expect(
      consumeSSE(
        reader(['data: {"error":{"message":"runtime lost"}}\n\n']),
        () => {},
      ),
    ).rejects.toThrow("runtime lost");
  });
});
describe("authenticated HTTP", () => {
  it("adds CSRF on writes and keeps credentials same-origin", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    setCSRF("session-csrf");
    await api("/api/v1/keys", { method: "POST", body: "{}" });
    const init = fetcher.mock.calls[0][1];
    expect(init.headers.get("X-CSRF-Token")).toBe("session-csrf");
    expect(init.credentials).toBe("same-origin");
  });
  it("preserves structured error codes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { message: "not allowed", code: "forbidden" },
          }),
          { status: 403 },
        ),
      ),
    );
    await expect(api("/v1/models")).rejects.toMatchObject({
      status: 403,
      code: "forbidden",
    } satisfies Partial<APIError>);
  });
});
