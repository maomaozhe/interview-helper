/* Sidebar layout owns its preferences independently of conversation state. */
(() => {
  "use strict";

  const sidebar = document.getElementById("workspace-sidebar");
  const toggle = document.getElementById("sidebar-toggle");
  const close = document.getElementById("sidebar-close");
  const resizer = document.getElementById("sidebar-resizer");
  const backdrop = document.getElementById("sidebar-backdrop");
  const main = document.getElementById("main");
  const historyToggle = document.getElementById("history-toggle");
  if (!sidebar || !toggle || !close || !resizer || !backdrop || !main) return;

  const WIDTH_KEY = "interview-workspace.sidebar-width";
  const COLLAPSED_KEY = "interview-workspace.sidebar-collapsed";
  const MIN_WIDTH = 192;
  const MAX_WIDTH = 420;
  const CONTENT_MIN_WIDTH = 360;
  const mobileQuery = window.matchMedia("(max-width: 760px)");
  const readPreference = (key) => {
    try { return window.localStorage.getItem(key); } catch { return null; }
  };
  const savePreference = (key, value) => {
    try { window.localStorage.setItem(key, String(value)); } catch { /* Layout still works without storage. */ }
  };
  const storedWidth = Number(readPreference(WIDTH_KEY));
  let preferredWidth = Number.isFinite(storedWidth) && storedWidth >= MIN_WIDTH && storedWidth <= MAX_WIDTH
    ? storedWidth : (window.innerWidth <= 1000 ? 210 : 244);
  let desktopCollapsed = readPreference(COLLAPSED_KEY) === "true";
  let mobile = mobileQuery.matches;
  let mobileOpen = false;
  let opener = null;
  let drag = null;

  const maximumWidth = () => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, window.innerWidth - CONTENT_MIN_WIDTH));
  const clampWidth = (width) => Math.round(Math.max(MIN_WIDTH, Math.min(maximumWidth(), width)));
  const canFocus = (element) => element instanceof HTMLElement && element.isConnected
    && !element.closest("[hidden], [inert]") && !element.disabled && element.getClientRects().length > 0;
  const focusableItems = () => Array.from(sidebar.querySelectorAll(
    "a[href], button, input, select, textarea, [tabindex]"
  )).filter((element) => element.tabIndex >= 0 && canFocus(element));

  function render() {
    const visible = mobile ? mobileOpen : !desktopCollapsed;
    const width = clampWidth(preferredWidth);
    document.documentElement.style.setProperty("--sidebar-width", `${width}px`);
    document.body.classList.toggle("sidebar-collapsed", !mobile && desktopCollapsed);
    document.body.classList.toggle("sidebar-drawer-open", mobile && mobileOpen);
    sidebar.hidden = !visible;
    sidebar.inert = !visible;
    sidebar.setAttribute("aria-hidden", String(!visible));
    if (mobile) {
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
    } else {
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
    }
    main.inert = mobile && mobileOpen;
    backdrop.hidden = !mobile || !mobileOpen;
    resizer.hidden = mobile;
    resizer.setAttribute("aria-valuemax", String(maximumWidth()));
    resizer.setAttribute("aria-valuenow", String(width));
    resizer.setAttribute("aria-valuetext", `${width} 像素`);
    toggle.setAttribute("aria-expanded", String(visible));
    const label = visible ? (mobile ? "关闭侧边栏" : "收起侧边栏") : "展开侧边栏";
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
    if (historyToggle) {
      historyToggle.setAttribute("aria-controls", "workspace-sidebar");
      historyToggle.setAttribute("aria-expanded", String(visible));
    }
  }

  function setMobileOpen(open, source = toggle) {
    if (!mobile || mobileOpen === open) return;
    finishDrag();
    if (open) opener = canFocus(source) ? source : document.activeElement;
    mobileOpen = open;
    render();
    if (open) {
      close.focus({preventScroll: true});
    } else {
      const destination = canFocus(opener) ? opener : toggle;
      destination.focus({preventScroll: true});
      opener = null;
    }
  }

  function finishDrag() {
    if (!drag) return;
    const pointerId = drag.pointerId;
    drag = null;
    document.body.classList.remove("sidebar-resizing");
    if (resizer.hasPointerCapture(pointerId)) resizer.releasePointerCapture(pointerId);
    savePreference(WIDTH_KEY, preferredWidth);
  }

  toggle.addEventListener("click", () => {
    if (mobile) setMobileOpen(!mobileOpen);
    else {
      finishDrag();
      desktopCollapsed = !desktopCollapsed;
      savePreference(COLLAPSED_KEY, desktopCollapsed);
      render();
    }
  });
  close.addEventListener("click", () => setMobileOpen(false));
  backdrop.addEventListener("click", () => setMobileOpen(false));

  // The existing handler still loads conversations; drawer visibility replaces
  // its mobile-only history-visible presentation.
  historyToggle?.addEventListener("click", () => {
    if (mobile) setMobileOpen(true, historyToggle);
  }, true);
  sidebar.addEventListener("click", (event) => {
    if (mobileOpen && event.target.closest("[data-page], [data-conversation], #sidebar-new-chat, .brand")) {
      setMobileOpen(false);
      // Navigation may hide the original history opener with its page.
      toggle.focus({preventScroll: true});
    }
  }, true);

  resizer.addEventListener("pointerdown", (event) => {
    if (mobile || desktopCollapsed || event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault();
    finishDrag();
    drag = {pointerId: event.pointerId, startX: event.clientX, startWidth: clampWidth(preferredWidth)};
    document.body.classList.add("sidebar-resizing");
    resizer.focus({preventScroll: true});
    try { resizer.setPointerCapture(event.pointerId); } catch { /* Window listeners also finish the drag. */ }
  });
  window.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    preferredWidth = clampWidth(drag.startWidth + event.clientX - drag.startX);
    render();
  });
  for (const type of ["pointerup", "pointercancel"]) {
    window.addEventListener(type, (event) => {
      if (drag && event.pointerId === drag.pointerId) finishDrag();
    });
  }
  resizer.addEventListener("lostpointercapture", finishDrag);
  window.addEventListener("blur", finishDrag);
  resizer.addEventListener("keydown", (event) => {
    if (mobile || desktopCollapsed) return;
    const width = clampWidth(preferredWidth);
    const step = event.shiftKey ? 32 : 8;
    const next = {ArrowLeft: width - step, ArrowRight: width + step, Home: MIN_WIDTH, End: maximumWidth()}[event.key];
    if (next === undefined) return;
    event.preventDefault();
    preferredWidth = clampWidth(next);
    savePreference(WIDTH_KEY, preferredWidth);
    render();
  });

  document.addEventListener("keydown", (event) => {
    if (!mobileOpen) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setMobileOpen(false);
    } else if (event.key === "Tab") {
      const items = focusableItems();
      const first = items[0];
      const last = items[items.length - 1];
      if (!first) return;
      if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !sidebar.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    }
  }, true);
  document.addEventListener("focusin", (event) => {
    if (mobileOpen && !sidebar.contains(event.target)) close.focus({preventScroll: true});
  });

  window.addEventListener("resize", () => {
    finishDrag();
    const nextMobile = mobileQuery.matches;
    const focusedElement = document.activeElement;
    const focusWasInSidebar = sidebar.contains(focusedElement);
    if (nextMobile !== mobile) {
      mobile = nextMobile;
      mobileOpen = false;
      opener = null;
    }
    render();
    if (focusWasInSidebar && (sidebar.hidden || !canFocus(focusedElement))) toggle.focus({preventScroll: true});
  });

  render();
})();
