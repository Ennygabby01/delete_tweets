import { createSheetDrag } from "./sheet-drag.js";
import { createModal } from "./modal.js";
import { $, count } from "./views.js";

export function createRunDrawer() {
  const media = window.matchMedia("(max-width: 900px)");
  const panel = $("run-panel");
  const slot = $("run-panel-slot");
  const content = $("run-drawer-content");
  const fab = $("run-fab");
  const closeButton = $("close-run-drawer");
  let available = false;
  const modal = createModal($("run-drawer"), {
    backdropDismiss: true,
    onClose: () => {
      fab.setAttribute("aria-expanded", "false");
      drag.reset();
    },
  });
  const drag = createSheetDrag($("run-drawer-drag"), $("run-drawer"), () =>
    modal.close(),
  );
  $("run-drawer-grip").addEventListener("click", () => modal.close());
  const place = () => {
    const wasOpen = $("run-drawer").open;
    modal.close({ restoreFocus: false });
    (media.matches ? content : slot).append(panel);
    fab.hidden = !media.matches || !available;
    if (wasOpen) $("page-heading").focus({ preventScroll: true });
  };
  fab.addEventListener("click", () => {
    if (!media.matches || !available) return;
    modal.open(closeButton, fab);
    fab.setAttribute("aria-expanded", "true");
  });
  closeButton.addEventListener("click", () => modal.close());
  media.addEventListener("change", place);
  place();
  return {
    close: (options) => modal.close(options),
    render({ selected, active, visible }) {
      available = visible && (selected > 0 || active);
      fab.hidden = !media.matches || !available;
      fab.setAttribute(
        "aria-label",
        selected > 0
          ? `Review ${count(selected)} selected posts`
          : "Open current run",
      );
      $("run-fab-count").textContent = selected > 99 ? "99+" : count(selected);
      $("run-fab-count").hidden = selected === 0;
      if (!available) modal.close({ restoreFocus: false });
    },
    reset() {
      available = false;
      fab.hidden = true;
      modal.close({ restoreFocus: false });
    },
  };
}
