import assert from "node:assert/strict";
import { test } from "node:test";
import * as client from "../src/api.ts";

test("composite request pins explicit child and preserves returned status identity", async () => {
  const original = globalThis.fetch;
  const calls: [string, RequestInit | undefined][] = [];
  const selected = { attempt: 2, calcjob_uuid: "20dd2a4c-031b-425c-90b8-2f7ea564d313" };
  const status = { export_id: "export2", execution_id: "E", ...selected, status: "failed", state: "ready" };
  globalThis.fetch = async (url, init) => {
    calls.push([String(url), init]);
    return new Response(JSON.stringify(status), { status: 202 });
  };
  try {
    assert.deepEqual(await client.prepareExport("secret", "run", "E", "science", selected), status);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/api/v1/runs/run/exports");
    assert.deepEqual(JSON.parse(String(calls[0][1]?.body)), {
      execution_id: "E", profile: "science", attempt: 2,
      calcjob_uuid: "20dd2a4c-031b-425c-90b8-2f7ea564d313",
    });
    assert.equal((calls[0][1]?.headers as Record<string, string>).Authorization, "Bearer secret");
  } finally { globalThis.fetch = original; }
});

test("export download follows exact export ID using native browser transfer", async () => {
  const originalFetch = globalThis.fetch;
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const clicks: string[] = [];
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "/api/v1/exports/export2/authorize");
    assert.equal(init?.method, "POST");
    const response = new Response(JSON.stringify({ url: "/api/v1/exports/export2/download" }));
    response.blob = async () => { throw new Error("Buffered full archive"); };
    return response;
  };
  Object.defineProperty(globalThis, "document", { configurable: true, value: {
    createElement: () => {
      const link = { href: "", download: "", click: () => clicks.push(link.href) };
      return link;
    },
  } });
  try {
    await client.downloadExport("secret", "export2");
    assert.deepEqual(clicks, ["/api/v1/exports/export2/download"]);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else Reflect.deleteProperty(globalThis, "document");
  }
});
