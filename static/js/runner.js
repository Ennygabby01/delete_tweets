import { api, publicMessage } from "./api.js";
import { $, text, visible, count } from "./views.js";
import { runStatus, outcomeSummary } from "./run-status.js";

export function createRunner({ generation, refresh, review, notify }) {
  let busy = false,
    blocked = false,
    initialized = false,
    requestKey = null,
    currentId = null,
    loading = false;
  let postOrder = "newest";
  const form = $("runner-form");
  const setBusy = (value) => {
    busy = value;
    $("runner-review").disabled = value || blocked;
    for (const id of ["runner-pause", "runner-resume", "runner-stop"])
      $(id).disabled = value;
    form.setAttribute("aria-busy", String(value));
  };
  form.addEventListener("input", () => {
    requestKey = null;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    const current = generation();
    requestKey ||= crypto.randomUUID().replaceAll("-", "");
    const body = {
      all_matching: true,
      ids: [],
      search: $("runner-search").value.trim(),
      cap: Number($("runner-cap").value),
      batch_size: Number($("runner-batch").value),
      request_key: requestKey,
      post_order: postOrder,
    };
    setBusy(true);
    try {
      const detail = await api("/runs/scheduled/preview", {
        method: "POST",
        body,
      });
      if (current !== generation()) return;
      currentId = detail.id;
      await refresh();
      if (current === generation()) review(detail);
    } catch (error) {
      if (current === generation()) notify(publicMessage(error));
    } finally {
      if (current === generation()) setBusy(false);
    }
  });
  function renderDetail(detail) {
    visible("runner-status", true);
    text("runner-status", runStatus(detail));
    $("runner-status").dataset.status = detail.counts.unknown
      ? "unknown"
      : detail.status;
    text("runner-progress-label", outcomeSummary(detail.counts));
    $("runner-progress").max = Math.max(1, detail.planned);
    $("runner-progress").value = Math.min(
      detail.planned,
      (detail.counts.deleted || 0) +
        (detail.counts.unavailable || 0) +
        (detail.counts.failed || 0),
    );
    text(
      "runner-progress-count",
      `${count(detail.planned - (detail.counts.pending || 0) - (detail.counts.sending || 0) - (detail.counts.unknown || 0))} of ${count(detail.planned)} processed`,
    );
    const waiting =
      detail.status === "running" &&
      detail.schedule.waiting_until > Date.now() / 1000;
    text(
      "runner-next",
      waiting
        ? `Next batch after ${new Date(detail.schedule.waiting_until * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
        : detail.note ||
            (detail.status === "running"
              ? "Processing the confirmed posts."
              : ""),
    );
    visible("runner-pause", detail.status === "running");
    visible(
      "runner-resume",
      ["paused", "draft"].includes(detail.status) &&
        !!detail.counts.pending &&
        !detail.counts.unknown &&
        !detail.counts.sending,
    );
    text(
      "runner-resume",
      detail.status === "draft" ? "Review runner" : "Resume runner",
    );
    visible(
      "runner-stop",
      ["running", "paused", "draft"].includes(detail.status),
    );
    $("runner-activity").href = `#/activity/${detail.id}`;
    visible("runner-current", true);
    visible("runner-empty", false);
  }
  for (const [id, action] of [
    ["runner-pause", "pause"],
    ["runner-stop", "cancel"],
    ["runner-resume", "resume"],
  ]) {
    $(id).addEventListener("click", async () => {
      if (busy || !currentId) return;
      const current = generation(),
        runId = currentId;
      setBusy(true);
      try {
        if (action === "resume") {
          const detail = await api(`/runs/${runId}`);
          if (current === generation()) review(detail);
        } else {
          await api(`/runs/${runId}/${action}`, { method: "POST" });
          if (current === generation()) await refresh();
        }
      } catch (error) {
        if (current === generation()) notify(publicMessage(error));
      } finally {
        if (current === generation()) setBusy(false);
      }
    });
  }
  return {
    async render(workspace) {
      if (!workspace) return;
      const limits = workspace.runner_limits;
      const nextOrder = workspace.preferences?.post_order || "newest";
      if (nextOrder !== postOrder) requestKey = null;
      postOrder = nextOrder;
      if (!initialized) {
        $("runner-cap").value = Math.min(500, limits.max_posts);
        $("runner-batch").value = Math.min(
          50,
          limits.batch_size,
          limits.max_posts,
        );
        initialized = true;
      }
      $("runner-cap").max = limits.max_posts;
      $("runner-batch").max = limits.batch_size;
      const filters = [
        ...workspace.filters.sites,
        ...workspace.filters.keywords,
      ];
      text(
        "runner-scope",
        `${count(workspace.stats.matched)} available posts${filters.length ? ` matching ${filters.join(", ")}` : " in your archive"}. ${postOrder === "oldest" ? "Oldest" : "Newest"} first; the reviewed list stays fixed.`,
      );
      text(
        "runner-window",
        `Waits when the window allowance is exhausted. Without X headers, the fallback window is ${count(limits.fallback_window_seconds / 60)} minutes.`,
      );
      text(
        "runner-allowance",
        `${count(workspace.quota.remaining)} requests ${workspace.quota.source === "x_response" ? "reported by X" : "estimated"} in this window`,
      );
      const active = workspace.runs.find((run) =>
        ["draft", "running", "paused"].includes(run.status),
      );
      blocked = !!active || workspace.sync.status === "running";
      $("runner-review").disabled = busy || blocked;
      text(
        "runner-form-help",
        active
          ? "Finish or stop the current run before creating another."
          : "Review the exact posts before starting.",
      );
      const run =
        workspace.runs.find((item) => item.schedule && item.id === currentId) ||
        (active?.schedule
          ? active
          : workspace.runs.find((item) => item.schedule));
      if (!run) {
        visible("runner-status", false);
        visible("runner-current", false);
        visible("runner-empty", true);
        return;
      }
      currentId = run.id;
      if (loading || $("runner-view").hidden) return;
      const current = generation(),
        runId = currentId;
      loading = true;
      try {
        const detail = await api(`/runs/${runId}`);
        if (current === generation() && currentId === runId)
          renderDetail(detail);
      } catch {
        // Polling should not repeat a saved failure or replace the mounted result.
      } finally {
        if (current === generation()) loading = false;
      }
    },
    reset() {
      busy = false;
      blocked = false;
      initialized = false;
      loading = false;
      requestKey = null;
      currentId = null;
      form.reset();
      form.setAttribute("aria-busy", "false");
      visible("runner-current", false);
      visible("runner-status", false);
      visible("runner-empty", true);
      for (const id of [
        "runner-status",
        "runner-progress-label",
        "runner-progress-count",
        "runner-next",
        "runner-scope",
        "runner-allowance",
      ])
        text(id, "");
    },
  };
}
