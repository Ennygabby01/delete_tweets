import assert from "node:assert/strict";
import { test } from "node:test";
import { api, setSession, onSessionLost } from "../../static/js/api.js";
import { createSessionActivity } from "../../static/js/session-activity.js";

const response = (status, data = {}) => ({
  status,
  ok: status < 400,
  json: async () => data,
});

test("late unauthorized response cannot sign out a newer session", async () => {
  let lost = 0,
    finish;
  onSessionLost(() => lost++);
  setSession({ csrf: "old-session" });
  globalThis.fetch = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const old = api("/workspace");
  setSession({ csrf: "new-session" });
  finish(response(401, { detail: "Sign in" }));
  await assert.rejects(old, /session changed/);
  assert.equal(lost, 0);
});

test("concurrent current-session unauthorized responses sign out once", async () => {
  let lost = 0;
  onSessionLost(() => {
    lost++;
    setSession(null);
  });
  setSession({ csrf: "current-session" });
  globalThis.fetch = async () => response(401, { detail: "Sign in" });
  await Promise.allSettled([api("/workspace"), api("/posts")]);
  assert.equal(lost, 1);
});

test("temporary local/network failures never signal logout", async () => {
  let lost = 0;
  onSessionLost(() => lost++);
  setSession({ csrf: "retained-session" });
  globalThis.fetch = async () => response(503);
  await assert.rejects(api("/workspace"));
  globalThis.fetch = async () => {
    throw new Error("offline");
  };
  await assert.rejects(api("/workspace"));
  assert.equal(lost, 0);
});

test("trusted wheel/touch/key activity renews locally, polling and synthetic events do not", async () => {
  const handlers = {},
    calls = [];
  let now = 0,
    authenticated = true,
    visible = true;
  const activity = createSessionActivity({
    target: {
      addEventListener(name, action) {
        handlers[name] = action;
      },
    },
    authenticated: () => authenticated,
    visible: () => visible,
    send: async () => calls.push("activity"),
    onError: () => {},
    clock: () => now,
  });
  assert.deepEqual(calls, []);
  await handlers.wheel({ type: "wheel", isTrusted: false });
  await handlers.pointermove({
    type: "pointermove",
    isTrusted: true,
    buttons: 0,
  });
  assert.deepEqual(calls, []);
  await handlers.wheel({ type: "wheel", isTrusted: true });
  now = 1000;
  await handlers.touchstart({ type: "touchstart", isTrusted: true });
  assert.equal(calls.length, 1);
  now = 61000;
  await handlers.touchstart({ type: "touchstart", isTrusted: true });
  assert.equal(calls.length, 2);
  authenticated = false;
  now = 122000;
  await handlers.keydown({ type: "keydown", isTrusted: true });
  authenticated = true;
  visible = false;
  await handlers.keydown({ type: "keydown", isTrusted: true });
  assert.equal(calls.length, 2);
  visible = true;
  activity.reset();
  await handlers.keydown({ type: "keydown", isTrusted: true });
  assert.equal(calls.length, 3);
});
