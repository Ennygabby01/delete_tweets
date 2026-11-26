import assert from "node:assert/strict";
import { test } from "node:test";
import { createModal } from "../../static/js/modal.js";
import { createRunDrawer } from "../../static/js/run-drawer.js";

class Element {
  constructor() {
    this.listeners = new Map();
    this.style = { top: "3px", paddingRight: "10px" };
    this.classes = new Set();
    this.classList = {
      contains: (name) => this.classes.has(name),
      add: (name) => this.classes.add(name),
      remove: (name) => this.classes.delete(name),
    };
    this.attrs = {};
    this.children = [];
    this.isConnected = true;
    this.open = false;
    this.hidden = false;
    this.capturedPointers = new Set();
    this.id = "";
  }
  setPointerCapture(id) {
    this.capturedPointers.add(id);
  }
  hasPointerCapture(id) {
    return this.capturedPointers.has(id);
  }
  releasePointerCapture(id) {
    this.capturedPointers.delete(id);
  }
  closest() {
    return this.id === "run-drawer-grip" || this.id === "close-run-drawer"
      ? this
      : null;
  }
  addEventListener(type, callback) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), callback]);
  }
  emit(type, values = {}) {
    const event = {
      target: this,
      preventDefault() {
        this.prevented = true;
      },
      stopPropagation() {
        this.stopped = true;
      },
      ...values,
    };
    for (const callback of this.listeners.get(type) || []) callback(event);
    return event;
  }
  append(child) {
    if (child.parentElement)
      child.parentElement.children = child.parentElement.children.filter(
        (item) => item !== child,
      );
    child.parentElement = this;
    this.children.push(child);
  }
  contains(target) {
    return (
      target === this || this.children.some((child) => child.contains(target))
    );
  }
  setAttribute(name, value) {
    this.attrs[name] = value;
  }
  focus() {
    document.activeElement = this;
  }
  showModal() {
    this.open = true;
  }
  close() {
    this.open = false;
  }
  getBoundingClientRect() {
    return { left: 100, right: 500, top: 100, bottom: 700, height: 600 };
  }
}
function setup(mobile = true) {
  const ids = new Map();
  for (const id of [
    "main",
    "run-panel",
    "run-panel-slot",
    "run-drawer-content",
    "run-fab",
    "close-run-drawer",
    "run-drawer",
    "run-fab-count",
    "run-drawer-drag",
    "run-drawer-grip",
    "page-heading",
  ]) {
    const element = new Element();
    element.id = id;
    ids.set(id, element);
  }
  ids.get("run-panel-slot").append(ids.get("run-panel"));
  ids.get("run-drawer").append(ids.get("run-drawer-content"));
  ids.get("run-drawer").append(ids.get("close-run-drawer"));
  const body = new Element();
  const trigger = new Element();
  const media = new Element();
  media.matches = mobile;
  globalThis.document = {
    body,
    activeElement: trigger,
    documentElement: { clientWidth: 384 },
    getElementById: (id) => ids.get(id),
  };
  const scrolls = [];
  globalThis.window = {
    scrollX: 0,
    scrollY: 300,
    innerWidth: 400,
    scrollTo(x, y) {
      this.scrollX = x;
      this.scrollY = y;
      scrolls.push([x, y]);
    },
    matchMedia: () => media,
  };
  globalThis.getComputedStyle = () => ({ paddingRight: "10px" });
  return { ids, body, trigger, media, scrolls };
}

test("modal locks and restores scroll/styles, Escape and backdrop dismissal", () => {
  const h = setup();
  const dialog = h.ids.get("run-drawer");
  const modal = createModal(dialog, { backdropDismiss: true });
  modal.open(h.ids.get("close-run-drawer"));
  assert.equal(h.body.style.top, "-300px");
  assert.equal(h.body.style.paddingRight, "26px");
  assert.equal(h.body.classes.has("dialog-open"), true);
  dialog.emit("pointerdown", { clientX: 150, clientY: 150 });
  dialog.emit("click", { clientX: 0, clientY: 0 });
  assert.equal(dialog.open, true, "a drag begun inside cannot dismiss");
  const cancel = dialog.emit("cancel");
  assert.equal(cancel.prevented, true);
  assert.equal(dialog.open, false);
  assert.equal(h.body.style.top, "3px");
  assert.equal(h.body.style.paddingRight, "10px");
  assert.equal(h.body.classes.has("dialog-open"), false);
  assert.equal(document.activeElement, h.trigger);
  assert.deepEqual(h.scrolls.at(-1), [0, 300]);
  modal.open();
  dialog.emit("pointerdown", { clientX: 0, clientY: 0 });
  dialog.emit("click", { clientX: 0, clientY: 0 });
  assert.equal(dialog.open, false);
});

test("confirmation handoff preserves original return focus and late close cannot unlock it", () => {
  const h = setup();
  const drawer = h.ids.get("run-drawer");
  const review = new Element();
  h.ids.get("run-drawer-content").append(review);
  const sheet = createModal(drawer);
  sheet.open(review, h.trigger);
  const confirmation = new Element();
  const input = new Element();
  const confirm = createModal(confirmation);
  confirm.open(input);
  assert.equal(drawer.open, false);
  assert.equal(confirmation.open, true);
  assert.equal(document.activeElement, input);
  drawer.emit("close");
  assert.equal(h.body.classes.has("dialog-open"), true);
  confirm.close();
  assert.equal(document.activeElement, h.trigger);
  assert.equal(h.body.classes.has("dialog-open"), false);
});

test("failed modal opening releases its lock and leaves existing style intact", () => {
  const h = setup();
  const dialog = new Element();
  dialog.showModal = () => {
    throw new Error("synthetic modal failure");
  };
  assert.throws(() => createModal(dialog).open(), /synthetic/);
  assert.equal(h.body.classes.has("dialog-open"), false);
  assert.equal(h.body.style.top, "3px");
});

test("selection reveals FAB without auto opening; resize, route and logout clean up", () => {
  const h = setup();
  const controller = createRunDrawer();
  const panel = h.ids.get("run-panel"),
    fab = h.ids.get("run-fab"),
    dialog = h.ids.get("run-drawer");
  assert.equal(panel.parentElement, h.ids.get("run-drawer-content"));
  controller.render({ selected: 0, active: false, visible: true });
  assert.equal(fab.hidden, true);
  controller.render({ selected: 3, active: false, visible: true });
  assert.equal(fab.hidden, false);
  assert.equal(dialog.open, false);
  assert.equal(fab.attrs["aria-label"], "Review 3 selected posts");
  fab.emit("click");
  assert.equal(dialog.open, true);
  assert.equal(fab.attrs["aria-expanded"], "true");
  h.media.matches = false;
  h.media.emit("change");
  assert.equal(dialog.open, false);
  assert.equal(panel.parentElement, h.ids.get("run-panel-slot"));
  assert.equal(fab.hidden, true);
  assert.equal(h.body.classes.has("dialog-open"), false);
  assert.equal(document.activeElement, h.ids.get("page-heading"));
  h.media.matches = true;
  h.media.emit("change");
  fab.emit("click");
  controller.render({ selected: 3, active: false, visible: false });
  assert.equal(dialog.open, false);
  assert.equal(fab.hidden, true);
  controller.render({ selected: 0, active: true, visible: true });
  assert.equal(fab.hidden, false);
  fab.emit("click");
  controller.reset();
  assert.equal(dialog.open, false);
  assert.equal(fab.hidden, true);
  assert.equal(h.body.classes.has("dialog-open"), false);
});

test("stable scrollbar gutter avoids double compensation", () => {
  const h = setup();
  globalThis.getComputedStyle = () => ({
    paddingRight: "10px",
    scrollbarGutter: "stable",
  });
  const modal = createModal(new Element());
  modal.open();
  assert.equal(h.body.style.paddingRight, "10px");
  modal.close();
});

test("drawer drag snaps back, suppresses a dragged handle click and dismisses a long pull", () => {
  const h = setup();
  const drawer = createRunDrawer();
  drawer.render({ selected: 6120, active: false, visible: true });
  assert.equal(h.ids.get("run-fab-count").textContent, "99+");
  h.ids.get("run-fab").emit("click");
  const surface = h.ids.get("run-drawer-drag"),
    dialog = h.ids.get("run-drawer"),
    grip = h.ids.get("run-drawer-grip");
  const down = (id) =>
    surface.emit("pointerdown", {
      target: grip,
      pointerId: id,
      button: 0,
      isPrimary: true,
      clientY: 100,
      timeStamp: 0,
    });
  down(1);
  surface.emit("pointermove", { pointerId: 1, clientY: 125 });
  assert.equal(dialog.style.transform, "translateY(25px)");
  surface.emit("pointerup", { pointerId: 1, timeStamp: 400 });
  assert.equal(dialog.open, true);
  assert.equal(dialog.style.transform, "");
  const click = surface.emit("click", { detail: 1 });
  assert.equal(click.prevented, true);
  assert.equal(click.stopped, true);
  down(2);
  surface.emit("pointermove", { pointerId: 2, clientY: 300 });
  surface.emit("pointerup", { pointerId: 2, timeStamp: 1000 });
  assert.equal(dialog.open, false);
  assert.equal(h.body.classes.has("dialog-open"), false);
  assert.equal(h.ids.get("run-fab").attrs["aria-expanded"], "false");
});

test("drawer drag cancellation restores position and ignores close-button drags", () => {
  const h = setup();
  const drawer = createRunDrawer();
  drawer.render({ selected: 1, active: false, visible: true });
  h.ids.get("run-fab").emit("click");
  const surface = h.ids.get("run-drawer-drag"),
    dialog = h.ids.get("run-drawer");
  surface.emit("pointerdown", {
    target: h.ids.get("close-run-drawer"),
    pointerId: 1,
    button: 0,
    clientY: 100,
    timeStamp: 0,
  });
  surface.emit("pointermove", { pointerId: 1, clientY: 300 });
  assert.notEqual(dialog.style.transform, "translateY(200px)");
  surface.emit("pointerdown", {
    pointerId: 2,
    button: 0,
    clientY: 100,
    timeStamp: 0,
  });
  surface.emit("pointermove", { pointerId: 2, clientY: 300 });
  surface.emit("pointercancel", { pointerId: 2 });
  assert.equal(dialog.open, true);
  assert.equal(dialog.style.transform, "");
  assert.equal(surface.capturedPointers.size, 0);
  drawer.reset();
});

test("modal locks main's independent scrolling and restores it across handoff", () => {
  const h = setup(false),
    main = h.ids.get("main");
  main.style.overflowX = "clip";
  main.style.overflowY = "auto";
  main.scrollTop = 420;
  main.scrollLeft = 8;
  const sheet = createModal(h.ids.get("run-drawer"));
  sheet.open(h.ids.get("close-run-drawer"));
  assert.equal(main.style.overflowY, "hidden");
  assert.equal(main.style.overflowX, "hidden");
  const confirmation = new Element(),
    modal = createModal(confirmation);
  modal.open();
  assert.equal(main.style.overflowY, "hidden");
  h.ids.get("run-drawer").emit("close");
  assert.equal(main.style.overflowY, "hidden");
  modal.close();
  assert.equal(main.style.overflowY, "auto");
  assert.equal(main.style.overflowX, "clip");
  assert.equal(main.scrollTop, 420);
  assert.equal(main.scrollLeft, 8);
});
