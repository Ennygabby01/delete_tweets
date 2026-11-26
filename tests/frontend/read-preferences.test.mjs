import assert from "node:assert/strict";
import { test } from "node:test";
import { createReadPreferences } from "../../static/js/read-preferences.js";

function setup() {
  const button = {
    attrs: {},
    disabled: false,
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    addEventListener(name, action) {
      this[name] = action;
    },
  };
  const label = { textContent: "" };
  globalThis.document = {
    getElementById: (id) => (id === "allow-post-reads" ? button : label),
  };
  let generation = 0,
    refreshed = 0;
  const notices = [];
  const controller = createReadPreferences({
    generation: () => generation,
    refresh: async () => {
      refreshed++;
    },
    notify: (message) => notices.push(message),
  });
  controller.render({ allow_post_reads: false });
  return {
    button,
    label,
    notices,
    controller,
    refreshed: () => refreshed,
    logout() {
      generation++;
      controller.reset();
    },
  };
}

test("read switch saves off/on/off and never calls post sync", async () => {
  const h = setup(),
    calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push(path);
    return { ok: true, json: async () => JSON.parse(options.body) };
  };
  await h.button.click();
  assert.equal(h.button.attrs["aria-checked"], "true");
  assert.equal(h.label.textContent, "On");
  await h.button.click();
  assert.equal(h.button.attrs["aria-checked"], "false");
  assert.equal(h.refreshed(), 2);
  assert.deepEqual(calls, [
    "/v1/preferences/post-reads",
    "/v1/preferences/post-reads",
  ]);
});

test("pending save blocks duplicate toggles and logout discards late response", async () => {
  const h = setup();
  let finish,
    calls = 0;
  globalThis.fetch = () => {
    calls++;
    return new Promise((resolve) => {
      finish = resolve;
    });
  };
  const pending = h.button.click();
  await h.button.click();
  assert.equal(calls, 1);
  assert.equal(h.button.disabled, true);
  h.logout();
  finish({ ok: true, json: async () => ({ allow_post_reads: true }) });
  await pending;
  assert.equal(h.button.attrs["aria-checked"], "false");
  assert.deepEqual(h.notices, []);
  assert.equal(h.refreshed(), 0);
});

test("failed save restores the saved switch and safe error feedback", async () => {
  const h = setup();
  globalThis.fetch = async () => {
    throw new Error("SYNTHETIC_PRIVATE_ERROR");
  };
  await h.button.click();
  assert.equal(h.button.disabled, false);
  assert.equal(h.button.attrs["aria-checked"], "false");
  assert.equal(h.button.attrs["aria-busy"], "false");
  assert.equal(h.notices.length, 1);
  assert.equal(h.notices[0].includes("SYNTHETIC_PRIVATE_ERROR"), false);
});
