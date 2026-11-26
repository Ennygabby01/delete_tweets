import { api, publicMessage } from "./api.js";
import { $, text } from "./views.js";

export function createPostOrder({ generation, refresh, notify }) {
  let saved = "newest",
    busy = false;
  const toggle = $("post-order-toggle"),
    input = $("post-order");
  const setValue = (value) => {
    input.value = value;
    text(
      "post-order-value",
      value === "oldest" ? "Oldest first" : "Newest first",
    );
    for (const option of $("post-order-options").querySelectorAll(
      '[role="option"]',
    ))
      option.setAttribute(
        "aria-selected",
        String(option.dataset.value === value),
      );
  };
  input.addEventListener("change", async () => {
    if (busy) return;
    const next = input.value;
    if (next === saved) return;
    const current = generation();
    busy = true;
    toggle.setAttribute("aria-busy", "true");
    try {
      const result = await api("/preferences/post-order", {
        method: "PUT",
        body: { post_order: next },
      });
      if (current !== generation()) return;
      saved = result.post_order;
      setValue(saved);
      await refresh();
    } catch (error) {
      if (current === generation()) {
        setValue(saved);
        notify(publicMessage(error));
      }
    } finally {
      if (current === generation()) {
        busy = false;
        toggle.setAttribute("aria-busy", "false");
      }
    }
  });
  return {
    render(preferences) {
      if (busy) return;
      saved = preferences.post_order || "newest";
      setValue(saved);
    },
    reset() {
      busy = false;
      saved = "newest";
      setValue(saved);
      toggle.setAttribute("aria-busy", "false");
    },
  };
}
