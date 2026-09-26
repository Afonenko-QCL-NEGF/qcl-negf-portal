import assert from "node:assert/strict";
import { test } from "node:test";
import {
  api,
  describeDetail,
  errorMessage,
  isTerminal,
  readableBytes,
  type Run,
} from "../src/api.ts";

test("validation errors produce readable messages", () => {
  assert.equal(
    describeDetail([
      { msg: "Code is invalid" },
      { msg: "Resource limit exceeded" },
    ]),
    "Code is invalid; Resource limit exceeded",
  );
  assert.equal(errorMessage(new Error("Offline")), "Offline");
});

test("scientific failure is not inferred from terminal scheduler state", () => {
  assert.equal(
    isTerminal({ process_state: "finished", is_finished_ok: false } as Run),
    true,
  );
  assert.equal(isTerminal({ process_state: "waiting" } as Run), false);
  assert.equal(readableBytes(2048), "2.0 KiB");
});

test("API carries credentials only in header and serializes one submission", async () => {
  const original = globalThis.fetch;
  const calls: [string, RequestInit | undefined][] = [];
  globalThis.fetch = async (url, init) => {
    calls.push([String(url), init]);
    return new Response(JSON.stringify({ uuid: "workflow" }), { status: 202 });
  };
  try {
    const originalPlan = '{\n"energy": 1.234567890123456789\n}\n';
    assert.deepEqual(await api("secret", "/runs", { plan: originalPlan }), {
      uuid: "workflow",
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/api/v1/runs");
    assert.deepEqual(calls[0][1]?.headers, {
      Authorization: "Bearer secret",
      "Content-Type": "application/json",
    });
    assert.equal(JSON.parse(String(calls[0][1]?.body)).plan, originalPlan);
  } finally {
    globalThis.fetch = original;
  }
});

test("API surfaces service refusal without retrying submission", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(
      JSON.stringify({ detail: "AiiDA service is unavailable" }),
      { status: 503 },
    );
  };
  try {
    await assert.rejects(
      api("secret", "/runs", {}),
      /AiiDA service is unavailable/,
    );
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = original;
  }
});
