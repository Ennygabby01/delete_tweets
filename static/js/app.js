import { createActivity } from "./activity.js";
import { createRunner } from "./runner.js";
import { createPostOrder } from "./post-order.js";
import { runStatus, outcomeSummary, deletionProgress } from "./run-status.js";
import { createSessionActivity } from "./session-activity.js";
import { createModal } from "./modal.js";
import { createRunDrawer } from "./run-drawer.js";
import { createReadPreferences } from "./read-preferences.js";
import { createPostSync } from "./post-sync.js";
import { initializeControls, initializeQuotaInfo } from "./controls.js";
import {
  api,
  onSessionLost,
  setSession,
  PublicError,
  publicMessage,
} from "./api.js";
import {
  $,
  text,
  visible,
  count,
  postRows,
  accountAvatar,
  filterChips,
  runItems,
  previewSummary,
  initializePostBleed,
} from "./views.js";

const state = {
  session: null,
  workspace: null,
  posts: [],
  selected: new Set(),
  all: false,
  page: 1,
  pages: 1,
  total: 0,
  search: "",
  activeId: null,
  detail: null,
  preview: null,
  requestKey: null,
  generation: 0,
  postsRequest: 0,
};
let notificationTimer, pollTimer;
let syncing = false;
let currentView = "posts";
const channel =
  "BroadcastChannel" in window
    ? new BroadcastChannel("tweet-deleter-session")
    : null;

function notify(message) {
  text("notice", message);
  visible("notice", true);
  clearTimeout(notificationTimer);
  notificationTimer = setTimeout(() => visible("notice", false), 6500);
}
async function safely(action) {
  try {
    return await action();
  } catch (error) {
    notify(publicMessage(error));
    return null;
  }
}
function signedOut() {
  state.generation++;
  state.postsRequest++;
  state.workspace = null;
  state.posts = [];
  state.selected.clear();
  state.all = false;
  state.detail = null;
  state.activeId = null;
  state.preview = null;
  state.session = null;
  activity.reset();
  runner.reset();
  postOrder.reset();
  sessionActivity.reset();
  postSync.reset();
  readPreferences.reset();
  runDrawer.reset();
  accountAvatar(null);
  setSession(null);
  clearInterval(pollTimer);
  closeDialog();
  $("post-list").replaceChildren();
  $("activity-list").replaceChildren();
  $("run-items").replaceChildren();
  $("confirmation-posts").replaceChildren();
  visible("confirmation-posts-section", false);
  for (const id of ["account-name", "account-handle", "connection-profile"])
    text(id, "");
  visible("workspace", false);
  visible("boot", false);
  visible("sign-in", true);
}
onSessionLost(() => {
  signedOut();
  notify("Your session ended. Connect with X to continue.");
});
channel?.addEventListener("message", (event) => {
  if (event.data === "logout") signedOut();
});

async function syncSession() {
  const generation = state.generation;
  const session = await api("/session");
  if (generation !== state.generation) return false;
  setSession(session);
  state.session = session;
  $("login-callback").value = session.callback_url;
  $("settings-callback").value = session.callback_url;
  visible("login-setup", !session.configured);
  $("connect").disabled = !session.configured;

  if (!session.authenticated) {
    signedOut();
    state.session = session;
    setSession(session);
    return false;
  }
  visible("sign-in", false);
  const profile = session.profile;
  accountAvatar(profile);
  text("account-name", profile.name);
  text("account-handle", `@${profile.username}`);
  text("connection-profile", `Connected as @${profile.username}`);
  return true;
}
function route({ focus = false } = {}) {
  runDrawer.close({ restoreFocus: false });
  confirmationModal.close({ restoreFocus: false });
  const parts = location.hash.replace(/^#\//, "").split("/");
  const view = ["posts", "activity", "settings", "runner"].includes(parts[0])
    ? parts[0]
    : "posts";
  if (focus && view !== currentView) {
    $("main").scrollTop = 0;
    window.scrollTo(0, 0);
  }
  currentView = view;
  for (const name of ["posts", "activity", "settings", "runner"])
    visible(`${name}-view`, name === view);
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === (view === "runner" ? "settings" : view))
      link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  text(
    "page-heading",
    view === "runner"
      ? "Deletion runner"
      : view[0].toUpperCase() + view.slice(1),
  );
  if (view === "runner") runner.render(state.workspace);
  state.activeId =
    view === "activity" && /^[a-f0-9]{32}$/.test(parts[1] || "")
      ? parts[1]
      : null;
  if (view === "activity") {
    const target = state.activeId;
    safely(async () => {
      await activity.show(target);
      if (target && state.activeId === target) await loadDetail();
    });
  }
  updateRunDrawer();
  if (focus) $("page-heading").focus({ preventScroll: true });
}
window.addEventListener("hashchange", () => {
  route({ focus: true });
  if (location.hash === "#/posts" && state.session?.authenticated)
    safely(() => loadPosts());
});

async function loadWorkspace() {
  const generation = state.generation;
  const data = await api("/workspace");
  if (generation !== state.generation || !state.session?.authenticated) return;
  const changed =
    state.workspace &&
    (state.workspace.sync?.completed !== data.sync.completed ||
      state.workspace.stats.total !== data.stats.total ||
      state.workspace.stats.deleted !== data.stats.deleted ||
      state.workspace.stats.unavailable !== data.stats.unavailable);
  const orderChanged =
    state.workspace &&
    state.workspace.preferences.post_order !== data.preferences.post_order;
  state.workspace = data;
  postOrder.render(data.preferences);
  if (orderChanged) clearSelection();
  runner.render(data);
  readPreferences.render(data.preferences);
  $("reconcile-run").disabled = !data.preferences.allow_post_reads;
  postSync.render(data.sync);
  text("matched-count", count(data.stats.matched));
  text("archive-total", count(data.stats.total));
  text("deleted-total", count(data.stats.deleted));
  const filters = data.filters;
  text(
    "filter-description",
    filters.sites.length + filters.keywords.length
      ? " matching your filters"
      : "",
  );
  filterChips(filters, (kind, value) =>
    safely(async () => {
      await api("/filters", { method: "DELETE", body: { kind, value } });
      clearSelection();
      await loadWorkspace();
      await loadPosts(1);
    }),
  );
  const quota = data.quota;
  text("capacity", count(quota.remaining));
  text("capacity-limit", count(quota.limit));
  $("capacity-meter").max = quota.limit;
  $("capacity-meter").value = quota.remaining;
  text(
    "quota-source",
    quota.source === "x_response" ? "Reported by X" : "Estimated allowance",
  );
  text(
    "quota-reset",
    quota.resets
      ? `Resets ${new Date(quota.resets * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
      : "X confirms the window after a request.",
  );
  const active = data.runs.find((run) =>
    ["draft", "running", "paused"].includes(run.status),
  );
  visible("active-run", !!active);
  text(
    "active-run-label",
    active
      ? `${active.status === "draft" ? "Preview ready" : `Run ${active.status}`}`
      : "",
  );
  $("open-active").onclick = () => {
    if (active) openRun(active.id);
  };
  updateSelection();
  if ((changed || orderChanged) && !postSync.busy)
    await loadPosts(orderChanged ? 1 : state.page);
}
function clearSelection() {
  state.selected.clear();
  state.all = false;
  state.requestKey = null;
  updateSelection();
}
function selectedCount() {
  return state.all ? state.total : state.selected.size;
}
function updateSelection() {
  const selected = selectedCount(),
    capacity = state.workspace?.quota.can_plan || 0;
  text("selection-count", `${count(selected)} selected`);
  text("run-selected", count(selected));
  text("run-planned", count(Math.min(selected, capacity)));
  text("run-later", count(Math.max(0, selected - capacity)));
  $("review").disabled =
    selected === 0 ||
    capacity === 0 ||
    !state.workspace ||
    postSync.busy ||
    state.workspace.sync?.status === "running" ||
    state.workspace.runs.some((r) =>
      ["draft", "running", "paused"].includes(r.status),
    );
  text(
    "select-all",
    state.all ? "Clear selection" : `Select all ${count(state.total)} matching`,
  );
  $("select-all").disabled = state.total === 0;
  const checked = state.posts.filter(
    (post) => state.all || state.selected.has(post.id),
  ).length;
  $("select-page").checked =
    state.posts.length > 0 && checked === state.posts.length;
  $("select-page").indeterminate = checked > 0 && checked < state.posts.length;
  $("select-page").disabled = state.posts.length === 0;
  updateRunDrawer();
}
function updateRunDrawer() {
  runDrawer.render({
    selected: selectedCount(),
    active: !!state.workspace?.runs.some((run) =>
      ["draft", "running", "paused"].includes(run.status),
    ),
    visible: !!state.session?.authenticated && !$("posts-view").hidden,
  });
}
function renderPosts() {
  postRows(
    state.posts,
    state.session.profile,
    (id) => state.all || state.selected.has(id),
    (id, checked) => {
      if (state.all) {
        state.all = false;
        state.selected = new Set(state.posts.map((post) => post.id));
      }
      if (checked) state.selected.add(id);
      else state.selected.delete(id);
      state.requestKey = null;
      updateSelection();
    },
  );
  updateSelection();
}
async function loadPosts(page = state.page) {
  const generation = state.generation,
    request = ++state.postsRequest;
  $("post-list").setAttribute("aria-busy", "true");
  try {
    const data = await api(
      `/posts?${new URLSearchParams({ page, search: state.search })}`,
    );
    if (
      generation !== state.generation ||
      request !== state.postsRequest ||
      !state.session?.authenticated
    )
      return;
    state.posts = data.posts;
    state.page = data.page;
    state.pages = data.pages;
    state.total = data.total;
    renderPosts();
    visible("posts-empty", data.posts.length === 0);
    text(
      "empty-heading",
      state.workspace?.stats.total
        ? "No posts match this view"
        : "No posts saved yet",
    );
    text(
      "empty-copy",
      state.workspace?.stats.total
        ? "Try another search or adjust your filters in Settings."
        : "Sync posts from X or import an archive in Settings.",
    );
    text("page-count", `Page ${count(data.page)} of ${count(data.pages)}`);
    $("previous-page").disabled = data.page <= 1;
    $("next-page").disabled = data.page >= data.pages;
  } finally {
    if (request === state.postsRequest)
      $("post-list").setAttribute("aria-busy", "false");
  }
}
function openRun(id) {
  state.activeId = id;
  const target = `#/activity/${id}`;
  if (location.hash === target)
    safely(async () => {
      await activity.show(id);
      await loadDetail();
    });
  else location.hash = target;
}
async function loadDetail() {
  if (!state.activeId) return;
  const id = state.activeId,
    generation = state.generation;
  const detail = await api(`/runs/${id}`);
  if (id !== state.activeId || generation !== state.generation) return;
  state.detail = detail;
  visible("run-detail", true);
  text("run-detail-heading", "Results");
  text("run-detail-note", detail.note);
  text("run-status", runStatus(detail));
  $("run-status").dataset.status = detail.counts.unknown
    ? "unknown"
    : detail.status;
  const c = detail.counts;
  const progress = deletionProgress(detail);
  $("run-progress-fill").style.width = `${progress}%`;
  $("run-progress").setAttribute("aria-valuenow", String(progress));
  $("run-progress").setAttribute("aria-valuetext", outcomeSummary(c));
  $("run-progress").classList.toggle(
    "is-running",
    detail.status === "running" &&
      !(detail.schedule?.waiting_until > Date.now() / 1000),
  );
  $("run-progress").dataset.tone =
    c.unknown || c.failed || detail.status === "paused"
      ? "warning"
      : detail.status === "completed"
        ? "success"
        : "default";
  text("run-detail-summary", outcomeSummary(c));
  visible(
    "resume-run",
    ["draft", "paused"].includes(detail.status) &&
      !!c.pending &&
      !c.unknown &&
      !c.sending,
  );
  visible("pause-run", detail.status === "running");
  visible(
    "reconcile-run",
    ["paused", "cancelled"].includes(detail.status) && !!c.unknown,
  );
  $("reconcile-run").disabled = !state.workspace?.preferences?.allow_post_reads;
  visible("cancel-run", ["draft", "paused", "running"].includes(detail.status));
  runItems(detail.items);
  activity.detailLoaded(id);
}
function showDialog(detail) {
  state.preview = detail;
  visible("confirmation-posts-section", !!detail.schedule);
  $("confirmation-posts-toggle").setAttribute("aria-expanded", "false");
  visible("confirmation-posts", false);
  $("confirmation-posts").replaceChildren();
  if (detail.schedule) {
    const fragment = document.createDocumentFragment();
    for (const item of detail.items.filter(
      (item) => item.status === "pending",
    )) {
      const row = document.createElement("li");
      row.textContent = item.text;
      fragment.append(row);
    }
    $("confirmation-posts").append(fragment);
  }
  text(
    "confirmation-copy",
    detail.schedule
      ? `${count(detail.counts.pending)} posts are included, ${detail.post_order === "oldest" ? "oldest" : "newest"} first. The runner continues in batches of up to ${count(detail.schedule.batch_size)}, waiting for X's rate window between batches. Deleted posts cannot be recovered.`
      : `${count(detail.counts.pending)} ${detail.counts.pending === 1 ? "post is" : "posts are"} included in this run, ${detail.post_order === "oldest" ? "oldest" : "newest"} first.`,
  );
  previewSummary(
    detail.counts.pending,
    detail.remaining_selection,
    detail.quota.remaining,
  );
  const included = detail.counts.pending;
  const total = included + detail.remaining_selection;
  const coverage = Math.round((100 * included) / Math.max(1, total));
  $("review-progress-fill").style.width = `${coverage}%`;
  $("review-progress").setAttribute("aria-valuenow", String(coverage));
  $("review-progress").setAttribute(
    "aria-valuetext",
    `${count(included)} of ${count(total)} selected posts fit this run`,
  );
  text(
    "review-progress-label",
    `${count(included)} of ${count(total)} selected posts in this run`,
  );
  $("confirmation-input").value = "";
  $("confirm-delete").disabled = true;
  text("confirmation-error", "");
  confirmationModal.open($("confirmation-input"));
}
function closeDialog() {
  confirmationModal.close();
}
$("close-confirmation").addEventListener("click", closeDialog);
$("cancel-confirmation").addEventListener("click", closeDialog);
$("confirmation-input").addEventListener("input", () => {
  $("confirm-delete").disabled = $("confirmation-input").value !== "DELETE";
});
$("confirm-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("confirm-delete").disabled = true;
  try {
    const id = state.preview.id;
    await api(`/runs/${id}/confirm`, {
      method: "POST",
      body: { confirmation: $("confirmation-input").value },
    });
    closeDialog();
    clearSelection();
    await loadWorkspace();
    if (state.preview?.schedule) location.hash = "/runner";
    else openRun(id);
  } catch (error) {
    text("confirmation-error", publicMessage(error));
  } finally {
    $("confirm-delete").disabled = $("confirmation-input").value !== "DELETE";
  }
});
$("review").addEventListener("click", () =>
  safely(async () => {
    $("review").disabled = true;
    state.requestKey ||= crypto.randomUUID().replaceAll("-", "");
    try {
      const detail = await api("/runs/preview", {
        method: "POST",
        body: {
          ids: state.all ? [] : [...state.selected],
          all_matching: state.all,
          search: state.search,
          request_key: state.requestKey,
          post_order: state.workspace.preferences.post_order,
        },
      });
      await loadWorkspace();
      state.activeId = detail.id;
      showDialog(detail);
    } finally {
      updateSelection();
    }
  }),
);
$("resume-run").addEventListener("click", () =>
  safely(async () => {
    await loadDetail();
    showDialog(state.detail);
  }),
);
for (const [button, action] of [
  ["pause-run", "pause"],
  ["cancel-run", "cancel"],
  ["reconcile-run", "reconcile"],
]) {
  $(button).addEventListener("click", () =>
    safely(async () => {
      $(button).disabled = true;
      try {
        await api(`/runs/${state.activeId}/${action}`, { method: "POST" });
        await loadDetail();
        await loadWorkspace();
      } finally {
        $(button).disabled = false;
      }
    }),
  );
}
$("select-page").addEventListener("change", () => {
  if (state.all) {
    state.all = false;
    state.selected.clear();
  }
  for (const post of state.posts) {
    if ($("select-page").checked) state.selected.add(post.id);
    else state.selected.delete(post.id);
  }
  state.requestKey = null;
  renderPosts();
});
$("select-all").addEventListener("click", () => {
  state.all = !state.all;
  state.selected.clear();
  state.requestKey = null;
  renderPosts();
});
$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.search = $("search").value.trim();
  clearSelection();
  safely(() => loadPosts(1));
});
$("previous-page").addEventListener("click", () =>
  safely(() => loadPosts(state.page - 1)),
);
$("next-page").addEventListener("click", () =>
  safely(() => loadPosts(state.page + 1)),
);
$("filter-form").addEventListener("submit", (event) => {
  event.preventDefault();
  safely(async () => {
    await api("/filters", {
      method: "POST",
      body: {
        kind: $("filter-kind").value,
        value: $("filter-value").value.trim(),
      },
    });
    $("filter-value").value = "";
    clearSelection();
    await loadWorkspace();
    await loadPosts(1);
  });
});
$("filter-kind").addEventListener("change", () => {
  $("filter-value").placeholder =
    $("filter-kind").value === "site" ? "example.com" : "Word or phrase";
});
$("upload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  safely(async () => {
    const file = $("archive-file").files[0];
    if (!file || !file.name.toLowerCase().endsWith(".js"))
      throw new PublicError("Choose tweets.js from your X archive.");
    if (file.size > state.session.max_archive_bytes)
      throw new PublicError("This archive exceeds the configured upload size.");
    $("upload-button").disabled = true;
    text("upload-status", "Importing archive…");
    try {
      const data = await api("/archive", {
        method: "POST",
        raw: true,
        body: file,
      });
      text("upload-status", `${count(data.imported)} posts imported.`);
      clearSelection();
      await loadWorkspace();
      await loadPosts(1);
    } catch (error) {
      text("upload-status", "Import was not completed.");
      throw error;
    } finally {
      $("upload-button").disabled = false;
    }
  });
});
$("disconnect").addEventListener("click", () =>
  safely(async () => {
    $("disconnect").disabled = true;
    try {
      const data = await api("/auth/logout", { method: "POST" });
      signedOut();
      channel?.postMessage("logout");
      await syncSession();
      notify(data.message);
    } finally {
      $("disconnect").disabled = false;
    }
  }),
);
for (const id of ["connect", "reconnect"])
  $(id).addEventListener("click", () =>
    safely(async () => {
      $(id).disabled = true;
      try {
        const session = await api("/session");
        setSession(session);
        state.session = session;
        const result = await api("/auth/start", { method: "POST" });
        const url = new URL(result.url);
        if (
          url.origin !== "https://x.com" ||
          url.pathname !== "/i/oauth2/authorize"
        )
          throw new PublicError("The sign-in destination is invalid.");
        location.assign(url.href);
      } finally {
        $(id).disabled = !state.session?.configured;
      }
    }),
  );
async function activate() {
  if (syncing) return;
  syncing = true;
  try {
    if (await syncSession()) {
      await loadWorkspace();
      await loadPosts(1);
      route();
      if (state.session?.authenticated) {
        visible("boot", false);
        visible("workspace", true);
      }
      clearInterval(pollTimer);
      pollTimer = setInterval(poll, 4000);
    }
  } finally {
    syncing = false;
  }
}
async function poll() {
  if (document.hidden || !state.session?.authenticated || syncing) return;
  syncing = true;
  try {
    await loadWorkspace();
    if (!$("activity-view").hidden) await activity.refresh();
    if (state.activeId) await loadDetail();
  } catch (error) {
    notify(publicMessage(error));
  } finally {
    syncing = false;
  }
}
window.addEventListener("focus", () =>
  safely(async () => {
    if (syncing) return;
    const wasAuthenticated = state.session?.authenticated;
    if (await syncSession()) {
      if (!wasAuthenticated) await activate();
      else {
        await loadWorkspace();
        if (state.activeId) await loadDetail();
      }
    }
  }),
);
window.addEventListener("pageshow", (event) => {
  if (event.persisted) safely(activate);
});
const activity = createActivity({
  generation: () => state.generation,
  onToggle: (id) => {
    location.hash = id ? `/activity/${id}` : "/activity";
  },
  onPage: (page) =>
    safely(async () => {
      state.activeId = null;
      history.replaceState(null, "", "#/activity");
      await activity.show(null, page);
    }),
  notify,
});

const runner = createRunner({
  generation: () => state.generation,
  refresh: loadWorkspace,
  review: showDialog,
  notify,
});
const postOrder = createPostOrder({
  generation: () => state.generation,
  refresh: loadWorkspace,
  notify,
});

const sessionActivity = createSessionActivity({
  target: document,
  authenticated: () => !!state.session?.authenticated,
  visible: () => !document.hidden,
  send: () => api("/session/activity", { method: "POST" }),
  onError: (error) => notify(publicMessage(error)),
});

const postSync = createPostSync({
  context: () => state,
  generation: () => state.generation,
  refresh: async () => {
    await loadWorkspace();
    await loadPosts();
  },
  onBusy: updateSelection,
  notify,
});

const readPreferences = createReadPreferences({
  generation: () => state.generation,
  refresh: loadWorkspace,
  notify,
});

const confirmationModal = createModal($("confirmation"));
const runDrawer = createRunDrawer();

initializeControls({ notify });
initializeQuotaInfo();
initializePostBleed();

const outcome = new URLSearchParams(location.search).get("auth");
if (outcome) history.replaceState(null, "", location.pathname + location.hash);
let initialReady = false;
const startupDelay = setTimeout(() => {
  if (!initialReady) visible("boot", true);
}, 220);
async function start() {
  visible("startup-error", false);
  visible("retry-start", false);
  try {
    await activate();
    initialReady = true;
    clearTimeout(startupDelay);
    visible("boot", false);
    if (outcome === "failed")
      notify(
        "X sign-in was cancelled or expired. Check your app settings and try again.",
      );
  } catch (error) {
    clearTimeout(startupDelay);
    visible("boot", true);
    text("startup-error", publicMessage(error));
    visible("startup-error", true);
    visible("retry-start", true);
  }
}
$("retry-start").addEventListener("click", start);
start();
