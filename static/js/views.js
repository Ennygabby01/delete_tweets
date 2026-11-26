import { itemStatus } from "./run-status.js";
export const $ = (id) => document.getElementById(id);
export const text = (id, value) => {
  $(id).textContent = value;
};
export const visible = (id, show) => {
  $(id).hidden = !show;
};
export const count = (value) => Number(value || 0).toLocaleString();
export const date = (value) => {
  const parsed = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(parsed.valueOf())
    ? "Date unavailable"
    : parsed.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
};
const node = (tag, className, content) => {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (content !== undefined) el.textContent = content;
  return el;
};

export function accountAvatar(profile) {
  const avatar = $("account-avatar");
  const url = profile?.profile_image_url || "";
  if (profile && avatar.dataset.imageUrl === url && avatar.hasChildNodes())
    return;
  avatar.dataset.imageUrl = url;
  avatar.textContent = profile?.name.slice(0, 1).toUpperCase() || "";
  if (!url) return;
  const image = document.createElement("img");
  image.alt = "";
  image.width = 34;
  image.height = 34;
  image.decoding = "async";
  image.referrerPolicy = "no-referrer";
  image.addEventListener(
    "load",
    () => {
      if (avatar.dataset.imageUrl === url) avatar.replaceChildren(image);
    },
    { once: true },
  );
  image.src = url;
}

export function postRows(posts, profile, selected, onSelect) {
  const fragment = document.createDocumentFragment();
  for (const post of posts) {
    const row = $("post-template").content.firstElementChild.cloneNode(true);
    const checkbox = row.querySelector("input");
    checkbox.checked = selected(post.id);
    row.querySelector(".sr-only").textContent =
      `Select post from ${date(post.created_at)}: ${post.text.slice(0, 70)}`;
    checkbox.addEventListener("change", () =>
      onSelect(post.id, checkbox.checked),
    );
    row.querySelector(".post-author").textContent = `@${profile.username}`;
    row.querySelector(".post-date").textContent = date(post.created_at);
    row.querySelector(".post-text").textContent = post.text;
    row.querySelector(".post-link").href = `https://x.com/i/status/${post.id}`;
    for (const url of post.urls) {
      let parsed;
      try {
        parsed = new URL(url);
      } catch {
        continue;
      }
      if (
        !["https:", "http:"].includes(parsed.protocol) ||
        parsed.username ||
        parsed.password
      )
        continue;
      const a = node(
        "a",
        "",
        parsed.hostname + (parsed.pathname === "/" ? "" : parsed.pathname),
      );
      a.href = parsed.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      row.querySelector(".post-links").append(a);
    }
    fragment.append(row);
  }
  $("post-list").replaceChildren(fragment);
}

export function filterChips(filters, remove) {
  for (const target of ["active-filters", "settings-filters"]) {
    const fragment = document.createDocumentFragment();
    for (const [key, kind] of [
      ["sites", "site"],
      ["keywords", "keyword"],
    ]) {
      for (const value of filters[key]) {
        const chip = node("span", "filter-chip");
        chip.append(node("span", "", value));
        const button = node("button", "", "×");
        button.type = "button";
        button.setAttribute("aria-label", `Remove ${value} filter`);
        button.addEventListener("click", () => remove(kind, value));
        chip.append(button);
        fragment.append(chip);
      }
    }
    $(target).replaceChildren(fragment);
  }
}

export function runItems(items) {
  const fragment = document.createDocumentFragment();
  for (const item of items) {
    const row = node("article", "run-item");
    const meta = node("div");
    const badge = node("span", "badge", itemStatus(item.status));
    badge.dataset.status = item.status;
    meta.append(node("span", "fine-print", date(item.created_at)), badge);
    row.append(meta, node("p", "", item.text), node("small", "", item.message));
    fragment.append(row);
  }
  $("run-items").replaceChildren(fragment);
}

export function previewSummary(planned, later, remaining) {
  const fragment = document.createDocumentFragment();
  for (const [label, value] of [
    ["To delete", planned],
    ["Left for a later run", later],
    ["Requests available", remaining],
  ]) {
    const div = node("div");
    div.append(node("dt", "", label), node("dd", "", count(value)));
    fragment.append(div);
  }
  $("preview-summary").replaceChildren(fragment);
}

export function initializePostBleed() {
  const list = $("post-list");
  const main = $("main");
  const sidebar = document.querySelector(".sidebar");
  const aside = document.querySelector(".run-panel");
  const update = () => {
    const bounds = list.getBoundingClientRect();
    const mainBounds = main.getBoundingClientRect();
    const asideBounds = aside.getBoundingClientRect();
    const leftEdge =
      getComputedStyle(sidebar).position === "fixed"
        ? sidebar.getBoundingClientRect().right
        : mainBounds.left;
    const rightEdge =
      asideBounds.left >= bounds.right ? asideBounds.left : mainBounds.right;
    list.style.setProperty(
      "--post-bleed-left",
      `${Math.max(0, bounds.left - leftEdge)}px`,
    );
    list.style.setProperty(
      "--post-bleed-right",
      `${Math.max(0, rightEdge - bounds.right)}px`,
    );
  };
  const observer = new ResizeObserver(update);
  observer.observe(main);
  observer.observe(sidebar);
  observer.observe(list);
  window.addEventListener("resize", update, { passive: true });
  update();
}
