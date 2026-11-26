import { api, publicMessage } from "./api.js";
import { $, text } from "./views.js";

export function createReadPreferences({ generation, refresh, notify }) {
  const toggle = $("allow-post-reads");
  let saved = { allow_post_reads: false };
  let busy = false;
  function render(value = saved) {
    saved = value;
    if (busy) return;
    const allowed = saved?.allow_post_reads === true;
    toggle.setAttribute("aria-checked", String(allowed));
    toggle.disabled = false;
    toggle.setAttribute("aria-busy", "false");
    text("post-reads-state", allowed ? "On" : "Off");
  }
  toggle.addEventListener("click", async () => {
    if (busy) return;
    const current = generation();
    const next = !saved.allow_post_reads;
    busy = true;
    toggle.disabled = true;
    toggle.setAttribute("aria-busy", "true");
    try {
      const result = await api("/preferences/post-reads", {
        method: "PUT",
        body: { allow_post_reads: next },
      });
      if (generation() !== current) return;
      saved = result;
      await refresh();
      if (generation() === current)
        notify(
          saved.allow_post_reads
            ? "X post reads allowed"
            : "X post reads blocked",
        );
    } catch (error) {
      if (generation() === current) notify(publicMessage(error));
    } finally {
      if (generation() === current) {
        busy = false;
        render();
      }
    }
  });
  return {
    render,
    reset() {
      busy = false;
      render({ allow_post_reads: false });
    },
  };
}
