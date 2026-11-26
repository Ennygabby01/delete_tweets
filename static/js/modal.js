let activeModal = null;

export function createModal(
  dialog,
  { backdropDismiss = false, onClose = () => {} } = {},
) {
  let snapshot = null;
  let returnTarget = null;
  const finish = (restoreFocus = true) => {
    if (!snapshot) return;
    const previous = snapshot;
    snapshot = null;
    document.body.style.top = previous.top;
    document.body.style.paddingRight = previous.paddingRight;
    if (!previous.hadClass) document.body.classList.remove("dialog-open");
    if (previous.main) {
      previous.main.style.overflowX = previous.mainOverflowX;
      previous.main.style.overflowY = previous.mainOverflowY;
      previous.main.scrollLeft = previous.mainX;
      previous.main.scrollTop = previous.mainY;
    }
    window.scrollTo(previous.x, previous.y);
    if (activeModal === controller) activeModal = null;
    onClose();
    if (restoreFocus && returnTarget?.isConnected)
      returnTarget.focus({ preventScroll: true });
  };
  const controller = {
    element: dialog,
    get returnTarget() {
      return returnTarget;
    },
    open(focusTarget, target = document.activeElement) {
      if (dialog.open) return;
      if (activeModal && activeModal !== controller) {
        if (activeModal.element.contains(target))
          target = activeModal.returnTarget;
        activeModal.close({ restoreFocus: false });
      }
      returnTarget = target;
      const body = document.body;
      const main = document.getElementById("main");
      snapshot = {
        x: window.scrollX,
        y: window.scrollY,
        top: body.style.top,
        paddingRight: body.style.paddingRight,
        hadClass: body.classList.contains("dialog-open"),
        main,
        mainOverflowX: main?.style.overflowX,
        mainOverflowY: main?.style.overflowY,
        mainX: main?.scrollLeft || 0,
        mainY: main?.scrollTop || 0,
      };
      const stableGutter = getComputedStyle(
        document.documentElement,
      ).scrollbarGutter?.includes("stable");
      const gap = stableGutter
        ? 0
        : Math.max(0, window.innerWidth - document.documentElement.clientWidth);
      body.style.top = `-${snapshot.y}px`;
      if (gap)
        body.style.paddingRight = `${parseFloat(getComputedStyle(body).paddingRight) + gap}px`;
      body.classList.add("dialog-open");
      if (main) {
        main.style.overflowX = "hidden";
        main.style.overflowY = "hidden";
      }
      activeModal = controller;
      try {
        dialog.showModal();
        focusTarget?.focus({ preventScroll: true });
      } catch (error) {
        finish();
        throw error;
      }
    },
    close({ restoreFocus = true } = {}) {
      if (dialog.open) dialog.close();
      finish(restoreFocus);
    },
  };
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    controller.close();
  });
  dialog.addEventListener("close", () => {
    if (!dialog.open) finish();
  });
  if (backdropDismiss) {
    let beganOutside = false;
    const outside = (event) => {
      const bounds = dialog.getBoundingClientRect();
      return (
        event.target === dialog &&
        (event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom)
      );
    };
    dialog.addEventListener("pointerdown", (event) => {
      beganOutside = outside(event);
    });
    dialog.addEventListener("click", (event) => {
      if (beganOutside && outside(event)) controller.close();
      beganOutside = false;
    });
  }
  return controller;
}
