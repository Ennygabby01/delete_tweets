export function initializeControls({ notify }) {
  for (const button of document.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      const field = document.getElementById(button.dataset.copy);
      try {
        await navigator.clipboard.writeText(field.value);
        button.classList.add("copied");
        button.setAttribute("aria-label", "Callback URL copied");
        notify("Callback URL copied");
        setTimeout(() => {
          button.classList.remove("copied");
          button.setAttribute("aria-label", "Copy callback URL");
        }, 1800);
      } catch {
        field.focus();
        field.select();
        notify("Select and copy the callback URL.");
      }
    });
  }
  for (const toggle of document.querySelectorAll(".disclosure-toggle")) {
    const panel = document.getElementById(toggle.getAttribute("aria-controls"));
    const setExpanded = (expanded) => {
      toggle.setAttribute("aria-expanded", String(expanded));
      panel.hidden = !expanded;
    };
    toggle.addEventListener("click", () =>
      setExpanded(toggle.getAttribute("aria-expanded") !== "true"),
    );
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        setExpanded(false);
        toggle.focus();
      }
    });
  }
  initializeSelect("filter-kind");
  initializeSelect("post-order");
  const file = document.getElementById("archive-file");
  document
    .getElementById("choose-archive")
    .addEventListener("click", () => file.click());
  file.addEventListener("change", () => {
    document.getElementById("archive-filename").textContent =
      file.files[0]?.name || "No file selected";
  });
}

export function initializeSelect(prefix) {
  const toggle = document.getElementById(`${prefix}-toggle`);
  const list = document.getElementById(`${prefix}-options`);
  const options = [...list.querySelectorAll('[role="option"]')];
  const input = document.getElementById(prefix);
  const close = (focus = false) => {
    list.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    if (focus) toggle.focus();
  };
  const open = (last = false) => {
    if (toggle.getAttribute("aria-busy") === "true") return;
    list.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    (last
      ? options.at(-1)
      : options.find((option) => option.dataset.value === input.value)
    ).focus();
  };
  const choose = (option) => {
    if (toggle.getAttribute("aria-busy") === "true") return;
    input.value = option.dataset.value;
    document.getElementById(`${prefix}-value`).textContent =
      option.firstChild.textContent;
    for (const item of options)
      item.setAttribute("aria-selected", String(item === option));
    input.dispatchEvent(new Event("change"));
    close(true);
  };
  toggle.addEventListener("click", () => (list.hidden ? open() : close()));
  toggle.addEventListener("keydown", (event) => {
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      open(event.key === "ArrowUp");
    }
  });
  for (const [index, option] of options.entries()) {
    option.addEventListener("click", () => choose(option));
    option.addEventListener("keydown", (event) => {
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? options.length - 1
              : (index +
                  (event.key === "ArrowDown" ? 1 : -1) +
                  options.length) %
                options.length;
        options[next].focus();
      } else if (event.key === "Escape") {
        event.preventDefault();
        close(true);
      } else if (event.key === "Tab") close();
    });
  }
  document.addEventListener("pointerdown", (event) => {
    if (!toggle.closest(".custom-select").contains(event.target)) close();
  });
  window.addEventListener("hashchange", () => close());
}

export function initializeQuotaInfo() {
  initializeInfo("quota-info-host", "quota-learn-more", "quota-info");
  initializeInfo("filters-help-host", "filters-help-toggle", "filters-help");
}

export function initializeInfo(hostId, triggerId, panelId) {
  const host = document.getElementById(hostId);
  const trigger = document.getElementById(triggerId);
  const panel = document.getElementById(panelId);
  let restoringFocus = false;
  let pinned = false;
  let leaveTimer;
  const show = () => {
    clearTimeout(leaveTimer);
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
  };
  const close = (restore = false) => {
    clearTimeout(leaveTimer);
    pinned = false;
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restore) {
      restoringFocus = true;
      trigger.focus({ preventScroll: true });
      restoringFocus = false;
    }
  };
  host.addEventListener("pointerenter", show);
  trigger.addEventListener("focus", () => {
    if (!restoringFocus) show();
  });
  host.addEventListener("pointerleave", () => {
    leaveTimer = setTimeout(() => {
      if (!pinned && !host.contains(document.activeElement)) close();
    }, 150);
  });
  trigger.addEventListener("click", () => {
    if (pinned) close();
    else {
      pinned = true;
      show();
    }
  });
  host.addEventListener("focusout", (event) => {
    if (!host.contains(event.relatedTarget)) close();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!host.contains(event.target)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      event.preventDefault();
      close(host.contains(document.activeElement));
    }
  });
  window.addEventListener("hashchange", () => close());
}
