export function createSessionActivity({
  target,
  authenticated,
  visible,
  send,
  onError,
  clock = Date.now,
}) {
  let lastAttempt = null;
  let pending = false;
  let generation = 0;
  const activity = async (event) => {
    if (!event.isTrusted || !authenticated() || !visible() || pending) return;
    if (event.type === "pointermove" && !event.buttons) return;
    const now = clock();
    if (lastAttempt !== null && now - lastAttempt < 60000) return;
    lastAttempt = now;
    pending = true;
    const current = generation;
    try {
      await send();
    } catch (error) {
      if (current === generation) onError(error);
    } finally {
      if (current === generation) pending = false;
    }
  };
  for (const name of [
    "pointerdown",
    "keydown",
    "wheel",
    "touchstart",
    "pointermove",
  ])
    target.addEventListener(name, activity, { passive: true });
  return {
    reset() {
      generation++;
      pending = false;
      lastAttempt = null;
    },
  };
}
