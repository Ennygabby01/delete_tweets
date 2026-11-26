import assert from "node:assert/strict";
import { test } from "node:test";
import { createPostOrder } from "../../static/js/post-order.js";
import { initializeSelect, initializeInfo } from "../../static/js/controls.js";

function setup() {
  const events = [],
    nodes = new Map(),
    documentHandlers = {};
  const host = { contains: (target) => [...nodes.values()].includes(target) };
  const get = (id) => {
    if (!nodes.has(id))
      nodes.set(id, {
        value: "newest",
        attrs: {},
        handlers: {},
        hidden: true,
        addEventListener(name, action) {
          this.handlers[name] = action;
        },
        setAttribute(name, value) {
          this.attrs[name] = value;
        },
        getAttribute(name) {
          return this.attrs[name];
        },
        focus() {
          document.activeElement = this;
        },
        closest() {
          return host;
        },
        dispatchEvent(event) {
          events.push(event.type);
        },
      });
    return nodes.get(id);
  };
  const options = ["newest", "oldest"].map((value) => {
    const node = get(value);
    node.dataset = { value };
    node.firstChild = {
      textContent: value === "oldest" ? "Oldest first" : "Newest first",
    };
    return node;
  });
  get("post-order-options").querySelectorAll = () => options;
  globalThis.document = {
    getElementById: get,
    addEventListener: (name, fn) => {
      documentHandlers[name] = fn;
    },
  };
  globalThis.window = { addEventListener() {} };
  let generation = 0,
    refreshed = 0;
  const notices = [];
  const controller = createPostOrder({
    generation: () => generation,
    refresh: async () => {
      refreshed++;
    },
    notify: (value) => notices.push(value),
  });
  controller.render({ post_order: "newest" });
  return {
    get,
    options,
    events,
    controller,
    notices,
    refreshed: () => refreshed,
    logout() {
      generation++;
      controller.reset();
    },
    async change(value) {
      get("post-order").value = value;
      await get("post-order").handlers.change();
    },
  };
}

test("order preference saves without X sync and restores server selection", async () => {
  const h = setup(),
    calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(url);
    return { ok: true, json: async () => JSON.parse(options.body) };
  };
  await h.change("oldest");
  assert.equal(h.get("post-order-value").textContent, "Oldest first");
  assert.equal(h.options[1].attrs["aria-selected"], "true");
  assert.deepEqual(calls, ["/v1/preferences/post-order"]);
  assert.equal(h.refreshed(), 1);
});

test("failed save reverts value and logout ignores a late preference response", async () => {
  const h = setup();
  globalThis.fetch = async () => {
    throw new Error("PRIVATE");
  };
  await h.change("oldest");
  assert.equal(h.get("post-order").value, "newest");
  assert.ok(!h.notices.join().includes("PRIVATE"));
  let finish;
  globalThis.fetch = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const pending = h.change("oldest");
  h.logout();
  finish({ ok: true, json: async () => ({ post_order: "oldest" }) });
  await pending;
  assert.equal(h.get("post-order-value").textContent, "Newest first");
  assert.equal(h.refreshed(), 0);
});

test("custom order select supports arrows, Escape, selection and busy guard", () => {
  const h = setup();
  initializeSelect("post-order");
  const toggle = h.get("post-order-toggle"),
    list = h.get("post-order-options");
  const key = (value) => ({ key: value, preventDefault() {} });
  toggle.handlers.keydown(key("ArrowDown"));
  assert.equal(list.hidden, false);
  assert.equal(document.activeElement, h.options[0]);
  h.options[0].handlers.keydown(key("ArrowDown"));
  assert.equal(document.activeElement, h.options[1]);
  h.options[1].handlers.click();
  assert.equal(h.get("post-order").value, "oldest");
  assert.equal(list.hidden, true);
  assert.deepEqual(h.events, ["change"]);
  toggle.handlers.click();
  h.options[1].handlers.keydown(key("Escape"));
  assert.equal(list.hidden, true);
  assert.equal(document.activeElement, toggle);
  toggle.setAttribute("aria-busy", "true");
  toggle.handlers.click();
  assert.equal(list.hidden, true);
});

test("filter info shows on focus or hover and Escape stays closed after focus restoration", () => {
  const events = {};
  const element = () => ({
    handlers: {},
    attrs: {},
    hidden: true,
    addEventListener(name, fn) {
      this.handlers[name] = fn;
    },
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    focus() {
      document.activeElement = this;
      this.handlers.focus?.();
    },
  });
  const host = element(),
    trigger = element(),
    panel = element();
  host.contains = (node) => [host, trigger, panel].includes(node);
  const nodes = { host, trigger, panel };
  globalThis.document = {
    getElementById: (id) => nodes[id],
    addEventListener: (name, fn) => {
      events[name] = fn;
    },
  };
  globalThis.window = { addEventListener() {} };
  initializeInfo("host", "trigger", "panel");
  trigger.focus();
  assert.equal(panel.hidden, false);
  document.activeElement = panel;
  events.keydown({ key: "Escape", preventDefault() {} });
  assert.equal(panel.hidden, true);
  assert.equal(document.activeElement, trigger);
  host.handlers.pointerenter();
  assert.equal(panel.hidden, false);
  events.pointerdown({ target: {} });
  assert.equal(panel.hidden, true);
});
