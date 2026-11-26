import { api, publicMessage } from "./api.js";
import { $, count, visible } from "./views.js";
import { groupRuns, runStatus, outcomeSummary } from "./run-status.js";

const node = (tag, className, value) => {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (value !== undefined) result.textContent = value;
  return result;
};
export function createActivity({ generation, onToggle, onPage, notify }) {
  let page = 1,
    pages = 1,
    active = null,
    request = 0,
    structure = "";
  const rows = new Map();
  const park = () => {
    if ($("run-detail").parentElement !== $("run-detail-parking"))
      $("run-detail-parking").append($("run-detail"));
    visible("run-detail", false);
  };
  function row(run) {
    const article = node("article", "history-run");
    const button = node("button", "history-toggle");
    button.type = "button";
    const content = node("span", "history-title");
    const title = node("strong"),
      summary = node("span", "history-outcomes");
    content.append(title, summary);
    const meta = node("span", "history-meta");
    const status = node("span", "badge"),
      time = node("time");
    meta.append(status, time);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "chevron");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "/static/icons.svg#chevron-down");
    svg.append(use);
    button.append(content, meta, svg);
    const panel = node("div", "history-panel");
    panel.id = `history-panel-${run.id}`;
    panel.hidden = true;
    button.id = `history-toggle-${run.id}`;
    button.setAttribute("aria-controls", panel.id);
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-labelledby", button.id);
    button.addEventListener("click", () =>
      onToggle(active === run.id ? null : run.id),
    );
    const loading = node("p", "history-loading", "Loading run…");
    loading.setAttribute("role", "status");
    panel.append(loading);
    article.append(button, panel);
    return { article, button, panel, title, summary, status, time, loading };
  }
  function expand() {
    const selected = rows.get(active);
    if (selected) {
      if ($("run-detail").parentElement !== selected.panel)
        selected.panel.append($("run-detail"));
      const ready = $("run-detail").dataset.runId === active;
      selected.loading.hidden = ready;
      visible("run-detail", ready);
    } else park();
    for (const [id, entry] of rows) {
      entry.panel.hidden = id !== active;
      entry.button.setAttribute("aria-expanded", String(id === active));
    }
  }
  function render(data) {
    page = data.page;
    pages = data.pages;
    const groups = groupRuns(data.runs);
    const signature = groups
      .map(
        (group) => group.id + ":" + group.runs.map((run) => run.id).join(","),
      )
      .join("|");
    const keep = new Set(data.runs.map((run) => run.id));
    for (const run of data.runs) {
      const entry = rows.get(run.id) || row(run);
      rows.set(run.id, entry);
      entry.title.textContent = `${run.schedule ? "Runner · " : ""}${count(run.planned)} ${run.planned === 1 ? "post" : "posts"}`;
      entry.summary.textContent = outcomeSummary(run.counts);
      entry.status.textContent = runStatus(run);
      entry.status.dataset.status = run.counts.unknown ? "unknown" : run.status;
      const date = new Date(run.created * 1000);
      entry.time.dateTime = date.toISOString();
      entry.time.textContent = date.toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      });
    }
    if (signature !== structure) {
      const focused = document.activeElement;
      park();
      const fragment = document.createDocumentFragment();
      for (const group of groups) {
        const section = node("section", "history-group");
        section.append(node("h3", "history-date", group.label));
        for (const run of group.runs) section.append(rows.get(run.id).article);
        fragment.append(section);
      }
      $("activity-list").replaceChildren(fragment);
      structure = signature;
      for (const id of rows.keys()) if (!keep.has(id)) rows.delete(id);
      expand();
      if (focused?.isConnected) focused.focus({ preventScroll: true });
    } else expand();
    visible("activity-empty", data.total === 0);
    visible("activity-pagination", data.pages > 1);
    $("activity-previous").disabled = page <= 1;
    $("activity-next").disabled = page >= pages;
    $("activity-page-count").textContent =
      `Page ${count(page)} of ${count(pages)} · ${count(data.total)} runs`;
  }
  async function load(next = page, focus = null) {
    const current = generation(),
      serial = ++request;
    $("activity-list").setAttribute("aria-busy", "true");
    try {
      const query = new URLSearchParams({ page: String(next), limit: "10" });
      if (focus) query.set("focus", focus);
      const data = await api(`/runs?${query}`);
      if (current === generation() && serial === request) render(data);
    } finally {
      if (current === generation() && serial === request)
        $("activity-list").setAttribute("aria-busy", "false");
    }
  }
  for (const [id, direction] of [
    ["activity-previous", -1],
    ["activity-next", 1],
  ])
    $(id).addEventListener("click", () =>
      onPage(Math.max(1, Math.min(pages, page + direction))),
    );
  return {
    async show(id, next = page) {
      const current = generation();
      active = id;
      if (!id) park();
      try {
        await load(next, id && !rows.has(id) ? id : null);
      } catch (error) {
        if (current === generation()) notify(publicMessage(error));
      }
    },
    refresh: () => load(),
    detailLoaded(id) {
      $("run-detail").dataset.runId = id;
      expand();
    },
    reset() {
      request++;
      active = null;
      page = 1;
      pages = 1;
      structure = "";
      park();
      rows.clear();
      $("activity-list").replaceChildren();
    },
  };
}
