import assert from "node:assert/strict";
import { test } from "node:test";
import {
  api,
  download,
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

test("download delegates streaming to the browser without buffering a Blob", async () => {
  const originalFetch = globalThis.fetch;
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const clicks: string[] = [];
  const url = "/api/v1/runs/workflow/artifact?execution_id=point_1&path=result.bin&attempt=2&calcjob_uuid=exact-child";
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), `${url.replace("/artifact?", "/artifact/authorize?")}`);
    assert.equal(init?.method, "POST");
    assert.equal((init?.headers as Record<string, string>).Authorization, "Bearer secret");
    return new Response(JSON.stringify({ url }));
  };
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: {
      createElement: () => {
        const link = { href: "", download: "", click: () => clicks.push(link.href) };
        return link;
      },
    },
  });
  try {
    await download("secret", "workflow", { execution_id: "point_1", path: "result.bin", size: 1, attempt: 2, calcjob_uuid: "exact-child" });
    assert.deepEqual(clicks, [url]);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else Reflect.deleteProperty(globalThis, "document");
  }
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


test("run-view agent report endpoints preserve author JSON and stay authenticated without cache", async () => {
  const original = globalThis.fetch;
  const run = "e7f5313d-22fd-49fa-9be5-85fbe29e1974";
  const reportId = "758d02d5-37d6-487b-a95e-39c59d68ae3e";
  const report = {
    schema: "qcl-negf-agent-report-v1",
    anchor: { run_uuid: run, root_definition_id: "study_alpha", root_kind: "study", plan_fingerprint: "a".repeat(64) },
    question_snapshot: "Авторский вопрос, не canonical R01 question",
    used_runs: [], conclusion: "Accepted in author prose",
    reasoning: "<script>window.changed=true</script>",
    limitations: "Run-view only; no stable card proof",
  };
  const raw = JSON.stringify(report, null, 2) + "\n";
  const receipt = { uuid: reportId, filename: "agent-report.json", bytes: new TextEncoder().encode(raw).length, sha256: "b".repeat(64), ctime: "2026-01-02T00:00:00Z", anchor: report.anchor };
  const paths = [`/runs/${run}/agent-reports`, `/runs/${run}/agent-reports?limit=7&offset=3`, `/runs/${run}/agent-reports/${reportId}`];
  const calls: [string, RequestInit | undefined][] = [];
  globalThis.fetch = async (url, init) => {
    calls.push([String(url), init]);
    assert.equal(init?.cache, "no-store");
    assert.equal((init?.headers as Record<string, string>).Authorization, "Bearer secret");
    assert.equal(String(url).includes("secret"), false);
    if (calls.length === 1) return new Response(JSON.stringify(receipt), { status: 201 });
    if (calls.length === 2) return new Response(JSON.stringify({ reports: [receipt] }));
    return new Response(raw, { headers: { "Content-Type": "application/json" } });
  };
  try {
    assert.deepEqual(await api("secret", paths[0], { report: raw }), receipt);
    assert.deepEqual(await api("secret", paths[1]), { reports: [receipt] });
    assert.deepEqual(await api("secret", paths[2]), report);
    assert.deepEqual(calls.map(([url]) => url), paths.map((path) => `/api/v1${path}`));
    assert.deepEqual(calls.map(([, init]) => init?.method), ["POST", "GET", "GET"]);
    assert.equal(JSON.parse(String(calls[0][1]?.body)).report, raw);
    assert.equal(calls[1][1]?.body, undefined);
    assert.equal(calls[2][1]?.body, undefined);
    assert.equal(Object.hasOwn(report.anchor, "card_id"), false);
    assert.deepEqual(report.used_runs, []);
    assert.equal(report.reasoning, "<script>window.changed=true</script>");
  } finally {
    globalThis.fetch = original;
  }
});

test("agent report service refusal is surfaced once without retry", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({ detail: "Corrupt report binding" }), { status: 422 });
  };
  try {
    await assert.rejects(api("secret", "/runs/run/agent-reports/report"), /Corrupt report binding/);
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = original;
  }
});
