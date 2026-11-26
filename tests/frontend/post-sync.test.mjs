import assert from "node:assert/strict";
import { test } from "node:test";
import { createPostSync } from "../../static/js/post-sync.js";
import { accountAvatar } from "../../static/js/views.js";

function setup() {
  const elements = new Map();
  for (const id of ["refresh", "sync-older", "post-sync-controls"]) {
    elements.set(id, {
      hidden: false,
      disabled: false,
      handlers: {},
      classes: new Set(),
      attrs: {},
      addEventListener(type, handler) {
        this.handlers[type] = handler;
      },
      setAttribute(key, value) {
        this.attrs[key] = value;
      },
      classList: {
        toggle(name, active) {
          if (active) elements.get(id).classes.add(name);
          else elements.get(id).classes.delete(name);
        },
      },
    });
  }
  globalThis.document = { getElementById: (id) => elements.get(id) };
  let generation = 0;
  const notices = [];
  const context = {
    session: { authenticated: true },
    workspace: { runs: [], preferences: { allow_post_reads: true } },
  };
  const controller = createPostSync({
    context: () => context,
    generation: () => generation,
    refresh: async () => {},
    onBusy: () => {},
    notify: (message) => notices.push(message),
  });
  return {
    controller,
    context,
    notices,
    elements,
    logout() {
      generation++;
      controller.reset();
    },
  };
}
const idle = { status: "idle", has_older: true, retry_after: 0 };
const failed = {
  ...idle,
  status: "error",
  message: "X could not read your posts.",
};
const response = (value) => ({ ok: true, json: async () => value });

test("polling a saved failure is silent; only explicit attempts notify", async () => {
  const h = setup();
  let reads = 0;
  globalThis.fetch = async () => {
    reads++;
    return response(failed);
  };
  for (let i = 0; i < 4; i++) h.controller.render(failed);
  assert.equal(reads, 0);
  assert.deepEqual(h.notices, []);
  assert.equal(h.elements.get("post-sync-controls").hidden, true);
  await h.elements.get("refresh").handlers.click();
  assert.deepEqual(h.notices, [failed.message]);
  for (let i = 0; i < 4; i++) h.controller.render(failed);
  assert.equal(h.notices.length, 1);
  await h.elements.get("refresh").handlers.click();
  assert.equal(reads, 2);
  assert.equal(h.notices.length, 2);
});

test("request spinner stops on failure and a retry restores older pages", async () => {
  const h = setup();
  h.controller.render(idle);
  let finish;
  globalThis.fetch = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const pending = h.elements.get("refresh").handlers.click();
  assert.equal(h.controller.busy, true);
  assert.equal(h.elements.get("refresh").attrs["aria-busy"], "true");
  assert.equal(h.elements.get("refresh").classes.has("is-refreshing"), true);
  finish(response(failed));
  await pending;
  assert.equal(h.elements.get("refresh").attrs["aria-busy"], "false");
  assert.equal(h.elements.get("refresh").classes.has("is-refreshing"), false);
  assert.equal(h.elements.get("post-sync-controls").hidden, true);
  globalThis.fetch = async () => response(idle);
  await h.elements.get("refresh").handlers.click();
  assert.equal(h.elements.get("post-sync-controls").hidden, false);
  assert.equal(h.notices.length, 1);
});

test("late failure after logout cannot restore feedback", async () => {
  const h = setup();
  h.controller.render(idle);
  let finish;
  globalThis.fetch = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const pending = h.elements.get("refresh").handlers.click();
  h.logout();
  finish(response(failed));
  await pending;
  assert.deepEqual(h.notices, []);
  assert.equal(h.controller.busy, false);
});

test("avatar retains initials until loaded and ignores late images after logout", () => {
  const avatar = {
    dataset: {},
    textContent: "",
    children: [],
    hasChildNodes() {
      return this.children.length > 0 || this.textContent.length > 0;
    },
    replaceChildren(image) {
      this.children = [image];
      this.textContent = "";
    },
  };
  const images = [];
  globalThis.document = {
    getElementById: () => avatar,
    createElement: () => {
      const image = {
        addEventListener(type, handler) {
          this[type] = handler;
        },
      };
      images.push(image);
      return image;
    },
  };
  const profile = {
    name: "Test Owner",
    profile_image_url: "https://pbs.twimg.com/profile_images/123/a.jpg",
  };
  accountAvatar(profile);
  assert.equal(avatar.textContent, "T");
  accountAvatar(profile);
  assert.equal(images.length, 1);
  images[0].load();
  assert.equal(avatar.children[0], images[0]);
  accountAvatar({
    ...profile,
    profile_image_url: profile.profile_image_url.replace("a.jpg", "b.jpg"),
  });
  accountAvatar(null);
  images[1].load();
  assert.equal(avatar.textContent, "");
});

test("disabled read preference blocks sync even through an invoked click handler", async () => {
  const h = setup();
  h.context.workspace.preferences.allow_post_reads = false;
  let reads = 0;
  globalThis.fetch = async () => {
    reads++;
    return response(idle);
  };
  h.controller.render(idle);
  assert.equal(h.elements.get("refresh").disabled, true);
  assert.equal(h.elements.get("post-sync-controls").hidden, true);
  await h.elements.get("refresh").handlers.click();
  assert.equal(reads, 0);
});
