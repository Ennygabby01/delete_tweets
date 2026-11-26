export function createSheetDrag(surface, dialog, dismiss) {
  let gesture = null;
  let suppressClick = false;
  const reset = () => {
    const previous = gesture;
    gesture = null;
    suppressClick ||= previous?.moved || false;
    dialog.classList.remove("is-dragging");
    dialog.style.transform = "";
    if (previous && surface.hasPointerCapture(previous.id))
      surface.releasePointerCapture(previous.id);
  };
  surface.addEventListener("pointerdown", (event) => {
    if (
      !dialog.open ||
      gesture ||
      event.isPrimary === false ||
      event.button !== 0
    )
      return;
    const control = event.target.closest("button, a, input");
    if (control && control.id !== "run-drawer-grip") return;
    suppressClick = false;
    gesture = {
      id: event.pointerId,
      y: event.clientY,
      at: event.timeStamp,
      distance: 0,
      moved: false,
    };
    surface.setPointerCapture(event.pointerId);
  });
  surface.addEventListener("pointermove", (event) => {
    if (gesture?.id !== event.pointerId) return;
    gesture.distance = Math.max(0, event.clientY - gesture.y);
    if (gesture.distance > 3) {
      gesture.moved = true;
      dialog.classList.add("is-dragging");
      dialog.style.transform = `translateY(${gesture.distance}px)`;
    }
  });
  surface.addEventListener("pointerup", (event) => {
    if (gesture?.id !== event.pointerId) return;
    const { distance, at } = gesture;
    const threshold = Math.max(
      80,
      dialog.getBoundingClientRect().height * 0.22,
    );
    const velocity = distance / Math.max(1, event.timeStamp - at);
    reset();
    if (distance >= threshold || (distance > 32 && velocity > 0.6)) dismiss();
  });
  for (const name of ["pointercancel", "lostpointercapture"])
    surface.addEventListener(name, (event) => {
      if (gesture?.id === event.pointerId) reset();
    });
  surface.addEventListener(
    "click",
    (event) => {
      if (!suppressClick || event.detail === 0) return;
      suppressClick = false;
      event.preventDefault();
      event.stopPropagation();
    },
    true,
  );
  return { reset };
}
