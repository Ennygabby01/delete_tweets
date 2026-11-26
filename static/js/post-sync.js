import { api, publicMessage } from "./api.js";
import { $, visible } from "./views.js";

export function createPostSync({
  context,
  generation,
  refresh,
  onBusy,
  notify,
}) {
  let busy = false;
  let saved = null;
  function render(value = saved) {
    saved = value;
    if (!value) return;
    const active = context().workspace?.runs.some((run) =>
      ["draft", "running", "paused"].includes(run.status),
    );
    const waiting = value.retry_after * 1000 > Date.now();
    const refreshing = busy || value.status === "running";
    const allowed = context().workspace?.preferences?.allow_post_reads === true;
    const blocked = !allowed || refreshing || active || waiting;
    $("refresh").disabled = blocked;
    $("refresh").classList.toggle("is-refreshing", refreshing);
    $("refresh").setAttribute("aria-busy", String(refreshing));
    $("sync-older").disabled = blocked;
    visible(
      "post-sync-controls",
      allowed && value.has_older && value.status !== "error",
    );
  }
  async function run(mode) {
    if (
      busy ||
      !context().session?.authenticated ||
      !context().workspace?.preferences?.allow_post_reads
    )
      return;
    const currentGeneration = generation();
    busy = true;
    render();
    onBusy();
    try {
      const result = await api("/posts/sync", {
        method: "POST",
        body: { mode },
      });
      if (currentGeneration !== generation()) return;
      saved = result;
      if (result.status === "error") notify(result.message);
      await refresh();
    } catch (error) {
      if (currentGeneration !== generation()) return;
      saved = { ...saved, status: "error" };
      notify(publicMessage(error));
    } finally {
      if (currentGeneration === generation()) {
        busy = false;
        render();
        onBusy();
      }
    }
  }
  $("refresh").addEventListener("click", () => run("recent"));
  $("sync-older").addEventListener("click", () => run("older"));
  return {
    get busy() {
      return busy;
    },
    render,
    reset() {
      busy = false;
      saved = null;
    },
  };
}
