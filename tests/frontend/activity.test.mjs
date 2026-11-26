import assert from "node:assert/strict";
import { test } from "node:test";
import { createActivity } from "../../static/js/activity.js";
import {
  groupRuns,
  runStatus,
  itemStatus,
  deletionProgress,
  outcomeSummary,
} from "../../static/js/run-status.js";

class Node {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.attrs = {};
    this.handlers = {};
    this.hidden = false;
    this.isConnected = true;
    this.replacements = 0;
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node.tag === "fragment") {
        this.append(...[...node.children]);
        continue;
      }
      if (node.parentElement)
        node.parentElement.children = node.parentElement.children.filter(
          (child) => child !== node,
        );
      this.children.push(node);
      node.parentElement = this;
    }
  }
  replaceChildren(...nodes) {
    this.replacements++;
    this.children = [];
    this.append(...nodes);
  }
  setAttribute(name, value) {
    this.attrs[name] = value;
  }
  addEventListener(name, callback) {
    this.handlers[name] = callback;
  }
  focus() {
    document.activeElement = this;
  }
}
function setup() {
  const ids = new Map();
  for (const id of [
    "run-detail-parking",
    "run-detail",
    "activity-list",
    "activity-empty",
    "activity-pagination",
    "activity-previous",
    "activity-next",
    "activity-page-count",
  ])
    ids.set(id, new Node());
  ids.get("run-detail-parking").append(ids.get("run-detail"));
  globalThis.document = {
    getElementById: (id) => ids.get(id),
    createElement: (tag) => new Node(tag),
    createElementNS: (_, tag) => new Node(tag),
    createDocumentFragment: () => new Node("fragment"),
    activeElement: new Node(),
  };
  let generation = 0;
  const toggles = [],
    pages = [],
    notices = [];
  const controller = createActivity({
    generation: () => generation,
    onToggle: (id) => toggles.push(id),
    onPage: (page) => pages.push(page),
    notify: (message) => notices.push(message),
  });
  return {
    ids,
    controller,
    toggles,
    pages,
    notices,
    logout() {
      generation++;
      controller.reset();
    },
  };
}
const run = {
  id: "a".repeat(32),
  status: "running",
  created: 1795608000,
  planned: 2,
  counts: { pending: 2 },
};
const response = (runs = [run]) => ({
  ok: true,
  json: async () => ({ runs, page: 1, pages: 2, total: 11 }),
});

test("date grouping uses calendar days and clear statuses preserve uncertainty", () => {
  const now = new Date(2026, 10, 25, 0, 1);
  const groups = groupRuns(
    [
      { created: now.getTime() / 1000 },
      { created: new Date(2026, 10, 24, 23, 59).getTime() / 1000 },
    ],
    now,
  );
  assert.deepEqual(
    groups.map((group) => group.label),
    ["Today", "Yesterday"],
  );
  assert.equal(
    runStatus({ status: "paused", counts: { unknown: 1 } }),
    "Needs checking",
  );
  assert.equal(
    runStatus({ status: "completed", counts: { failed: 1 } }),
    "Finished with failures",
  );
  assert.equal(itemStatus("unavailable"), "Unavailable on X");
  assert.equal(
    deletionProgress({
      planned: 4,
      counts: { deleted: 1, unknown: 2, pending: 1 },
    }),
    25,
  );
  assert.equal(
    outcomeSummary({ unknown: 1, pending: 2 }),
    "0 deleted · 1 need checking · 2 waiting",
  );
});

test("history refresh retains expanded DOM and pagination is local", async () => {
  const h = setup(),
    calls = [];
  globalThis.fetch = async (path) => {
    calls.push(path);
    return response();
  };
  await h.controller.show(run.id);
  const list = h.ids.get("activity-list"),
    article = list.children[0].children[1],
    toggle = article.children[0];
  h.controller.detailLoaded(run.id);
  const detail = h.ids.get("run-detail"),
    parent = detail.parentElement,
    replacements = list.replacements;
  assert.equal(toggle.attrs["aria-expanded"], "true");
  assert.equal(detail.hidden, false);
  globalThis.fetch = async (path) => {
    calls.push(path);
    return response([{ ...run, counts: { deleted: 1, pending: 1 } }]);
  };
  await h.controller.refresh();
  assert.equal(list.replacements, replacements);
  assert.equal(detail.parentElement, parent);
  assert.equal(toggle.attrs["aria-expanded"], "true");
  toggle.handlers.click();
  assert.deepEqual(h.toggles, [null]);
  h.ids.get("activity-next").handlers.click();
  assert.deepEqual(h.pages, [2]);
  assert.ok(calls.every((path) => path.startsWith("/v1/runs?")));
  assert.deepEqual(h.notices, []);
});

test("late history response after logout cannot restore private history", async () => {
  const h = setup();
  let finish;
  globalThis.fetch = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const pending = h.controller.show(run.id);
  h.logout();
  finish(response());
  await pending;
  assert.equal(h.ids.get("activity-list").children.length, 0);
  assert.equal(h.ids.get("run-detail").hidden, true);
});
