export function runStatus(run) {
  if (run.counts?.unknown) return "Needs checking";
  if (
    run.status === "running" &&
    run.schedule?.waiting_until > Date.now() / 1000
  )
    return "Cooling down";
  if (run.status === "completed" && run.counts?.failed)
    return "Finished with failures";
  return (
    {
      draft: "Ready to review",
      running: "Deleting",
      paused: "Paused",
      completed: "Finished",
      cancelled: "Cancelled",
    }[run.status] || "Unknown status"
  );
}
export const itemStatus = (status) =>
  ({
    pending: "Waiting",
    sending: "Deleting",
    deleted: "Deleted",
    unavailable: "Unavailable on X",
    failed: "Failed",
    unknown: "Needs checking",
  })[status] || "Unknown status";
export function outcomeSummary(counts = {}) {
  return [
    ["deleted", "deleted"],
    ["unavailable", "unavailable"],
    ["failed", "failed"],
    ["unknown", "need checking"],
    ["sending", "deleting"],
    ["pending", "waiting"],
  ]
    .filter(([key]) => key === "deleted" || counts[key] > 0)
    .map(
      ([key, label]) => `${Number(counts[key] || 0).toLocaleString()} ${label}`,
    )
    .join(" · ");
}
export function deletionProgress(detail) {
  const c = detail.counts || {};
  const done = (c.deleted || 0) + (c.unavailable || 0) + (c.failed || 0);
  return Math.min(100, Math.round((100 * done) / Math.max(1, detail.planned)));
}
export function groupRuns(runs, now = new Date()) {
  const key = (date) =>
    `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
  const today = key(now),
    yesterday = key(
      new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1),
    );
  const groups = new Map();
  for (const run of runs) {
    const date = new Date(run.created * 1000),
      id = key(date);
    const label =
      id === today
        ? "Today"
        : id === yesterday
          ? "Yesterday"
          : date.toLocaleDateString(undefined, {
              day: "numeric",
              month: "long",
              year: "numeric",
            });
    if (!groups.has(id)) groups.set(id, { id, label, runs: [] });
    groups.get(id).runs.push(run);
  }
  return [...groups.values()];
}
