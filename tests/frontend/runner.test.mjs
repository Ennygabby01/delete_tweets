import assert from "node:assert/strict";
import { test } from "node:test";
import { createRunner } from "../../static/js/runner.js";
import { runStatus } from "../../static/js/run-status.js";

function setup() {
  const nodes = new Map();
  const get = (id) => {
    if (!nodes.has(id))
      nodes.set(id, {
        value: "",
        hidden: false,
        dataset: {},
        handlers: {},
        addEventListener(name, fn) {
          this.handlers[name] = fn;
        },
        setAttribute(name, value) {
          this[name] = value;
        },
        reportValidity() {
          return true;
        },
        reset() {},
      });
    return nodes.get(id);
  };
  globalThis.document = { getElementById: get };
  let generation = 0,
    refreshed = 0;
  const reviewed = [],
    notices = [];
  const controller = createRunner({
    generation: () => generation,
    refresh: async () => {
      refreshed++;
    },
    review: (detail) => reviewed.push(detail),
    notify: (message) => notices.push(message),
  });
  const workspace = {
    runner_limits: {
      max_posts: 5000,
      batch_size: 50,
      fallback_window_seconds: 900,
    },
    filters: { sites: [], keywords: [] },
    stats: { matched: 600 },
    quota: { remaining: 50, source: "estimate" },
    sync: { status: "idle" },
    runs: [],
  };
  controller.render(workspace);
  return {
    get,
    workspace,
    controller,
    reviewed,
    notices,
    refreshed: () => refreshed,
    logout() {
      generation++;
      controller.reset();
    },
    submit: () => get("runner-form").handlers.submit({ preventDefault() {} }),
  };
}

test("runner review sends a capped snapshot request and never starts deletion", async () => {
  const h = setup(),
    calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push([path, JSON.parse(options.body)]);
    return {
      ok: true,
      json: async () => ({
        id: "a".repeat(32),
        schedule: { cap: 500, batch_size: 50 },
      }),
    };
  };
  await h.submit();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/v1/runs/scheduled/preview");
  assert.equal(calls[0][1].cap, 500);
  assert.equal(calls[0][1].batch_size, 50);
  assert.equal(calls[0][1].all_matching, true);
  assert.equal(h.reviewed.length, 1);
  assert.equal(h.refreshed(), 1);
});

test("pending review blocks duplicates and logout discards late results", async () => {
  const h = setup();
  let finish,
    calls = 0;
  globalThis.fetch = () => {
    calls++;
    return new Promise((resolve) => {
      finish = resolve;
    });
  };
  const pending = h.submit();
  await h.submit();
  assert.equal(calls, 1);
  h.logout();
  finish({ ok: true, json: async () => ({ id: "a".repeat(32) }) });
  await pending;
  assert.deepEqual(h.reviewed, []);
  assert.equal(h.refreshed(), 0);
});

test("cooldown is explicit, pausing uses only local API, and no credit polling occurs", async () => {
  const h = setup(),
    id = "a".repeat(32),
    calls = [];
  const run = {
    id,
    status: "running",
    schedule: { waiting_until: Date.now() / 1000 + 600, batch_size: 50 },
    counts: { deleted: 50, pending: 450 },
    planned: 500,
  };
  assert.equal(runStatus(run), "Cooling down");
  globalThis.fetch = async (path) => {
    calls.push(path);
    return { ok: true, json: async () => run };
  };
  await h.controller.render({ ...h.workspace, runs: [run] });
  assert.equal(h.get("runner-status").textContent, "Cooling down");
  assert.match(h.get("runner-next").textContent, /Next batch after/);
  await h.get("runner-pause").handlers.click();
  assert.deepEqual(calls, [`/v1/runs/${id}`, `/v1/runs/${id}/pause`]);
  assert.equal(h.get("runner-review").disabled, true);
  assert.ok(!calls.some((path) => /credits|usage|sync/.test(path)));
});

test("review errors are safe and preserve the same request identity for retry", async () => {
  const h = setup(),
    keys = [];
  globalThis.fetch = async (_, options) => {
    keys.push(JSON.parse(options.body).request_key);
    throw new Error("PRIVATE");
  };
  await h.submit();
  await h.submit();
  assert.equal(keys[0], keys[1]);
  assert.ok(!h.notices.join(" ").includes("PRIVATE"));
});
