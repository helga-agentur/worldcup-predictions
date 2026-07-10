(function () {
  var cookieName = "helga_theme";
  var root = document.documentElement;
  var toggles = Array.prototype.slice.call(document.querySelectorAll("[data-theme-toggle]"));
  var menu = document.querySelector("[data-site-menu]");
  var menuSurface = document.querySelector("[data-site-menu-surface]");
  var menuToggle = document.querySelector("[data-menu-toggle]");
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var reducedMotionMedia = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var focusBeforeMenu = null;
  var focusableSelector = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function cookieTheme() {
    var match = document.cookie.match(new RegExp("(?:^|; )" + cookieName + "=(dark|light)"));
    return match ? match[1] : "";
  }

  function preferredTheme() {
    return media && media.matches ? "dark" : "light";
  }

  function applyTheme(theme, explicit) {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    toggles.forEach(function (toggle) {
      if (toggle.type === "checkbox") {
        toggle.checked = theme === "dark";
      }
      toggle.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    });
    if (explicit) {
      document.cookie = cookieName + "=" + theme + "; Path=/; Max-Age=31536000; SameSite=Lax";
    }
  }

  applyTheme(cookieTheme() || preferredTheme(), false);

  toggles.forEach(function (toggle) {
    toggle.addEventListener(toggle.type === "checkbox" ? "change" : "click", function () {
      var nextTheme = toggle.type === "checkbox"
        ? (toggle.checked ? "dark" : "light")
        : (root.dataset.theme === "dark" ? "light" : "dark");
      applyTheme(nextTheme, true);
    });
  });

  function prefersReducedMotion() {
    return reducedMotionMedia && reducedMotionMedia.matches;
  }

  function menuIsOpenish() {
    return menu && (menu.dataset.state === "opening" || menu.dataset.state === "open");
  }

  function finishMenuClose() {
    if (!menu) {
      return;
    }
    menu.dataset.state = "closed";
    delete document.body.dataset.menuOpen;
  }

  function finishMenuOpen() {
    if (menu && menu.dataset.state === "opening") {
      menu.dataset.state = "open";
    }
  }

  function menuFocusables() {
    if (!menu || !menuToggle) {
      return [];
    }
    return [menuToggle].concat(Array.prototype.slice.call(menu.querySelectorAll(focusableSelector)));
  }

  function trapMenuFocus(event) {
    if (event.key !== "Tab" || !menuIsOpenish()) {
      return;
    }
    var focusables = menuFocusables();
    if (!focusables.length) {
      return;
    }
    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function setMenuOpen(open, restoreFocus) {
    if (!menu || !menuToggle) {
      return;
    }
    var state = menu.dataset.state || "closed";
    if (open && (state === "opening" || state === "open")) {
      return;
    }
    if (!open && (state === "closing" || state === "closed")) {
      return;
    }

    menu.setAttribute("aria-hidden", open ? "false" : "true");
    menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    menuToggle.setAttribute("aria-label", menuToggle.getAttribute(open ? "data-close-label" : "data-open-label") || "");
    if (open) {
      hideAllTooltips();
      focusBeforeMenu = document.activeElement;
      document.body.dataset.menuOpen = "true";
      menu.dataset.state = "opening";
      var firstLink = menu.querySelector(".site-menu__link");
      if (firstLink) {
        firstLink.focus();
      }
      if (!menuSurface || prefersReducedMotion()) {
        finishMenuOpen();
      }
    } else {
      menu.dataset.state = "closing";
      if (restoreFocus && focusBeforeMenu && focusBeforeMenu.focus) {
        focusBeforeMenu.focus();
      }
      focusBeforeMenu = null;
      if (!menuSurface || prefersReducedMotion()) {
        finishMenuClose();
      }
    }
  }

  if (menu && menuToggle) {
    menu.dataset.state = menu.dataset.state || "closed";
    menu.setAttribute("aria-hidden", menuIsOpenish() ? "false" : "true");
    menuToggle.setAttribute("aria-expanded", menuIsOpenish() ? "true" : "false");
  }

  if (menuToggle && menu) {
    menuToggle.addEventListener("click", function () {
      setMenuOpen(!menuIsOpenish(), true);
    });

    menu.addEventListener("click", function (event) {
      var link = event.target && event.target.closest ? event.target.closest("a") : null;
      if (link) {
        setMenuOpen(false, false);
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menuIsOpenish()) {
        setMenuOpen(false, true);
        return;
      }
      trapMenuFocus(event);
    });

    if (menuSurface) {
      menuSurface.addEventListener("transitionend", function (event) {
        if (event.target !== menuSurface || event.propertyName !== "transform") {
          return;
        }
        if (menu.dataset.state === "opening") {
          finishMenuOpen();
        } else if (menu.dataset.state === "closing") {
          finishMenuClose();
        }
      });
    }
  }

  var tooltipTriggers = Array.prototype.slice.call(document.querySelectorAll("[data-tooltip-trigger]"));
  var tooltipTimers = new WeakMap();

  function tooltipForTrigger(trigger) {
    return trigger ? document.getElementById(trigger.getAttribute("data-tooltip-trigger")) : null;
  }

  function clearTooltipTimer(tooltip) {
    var timer = tooltipTimers.get(tooltip);
    if (timer) {
      window.clearTimeout(timer);
      tooltipTimers.delete(tooltip);
    }
  }

  // Keep in sync with --tooltip-arrow-anchor in site.css (65 / 340).
  var SUMMARY_ARROW_ANCHOR_RATIO = 65 / 340;

  function alignSummaryTooltip(trigger, tooltip) {
    var parent = tooltip.offsetParent;
    if (!parent) {
      return;
    }
    var gutter = 16;
    var arrowMinEdge = 18;
    var parentRect = parent.getBoundingClientRect();
    var triggerRect = trigger.getBoundingClientRect();
    var width = tooltip.getBoundingClientRect().width;
    if (!width) {
      return;
    }
    // The tooltip may use the full content column, not just the card: any
    // spare room up to the content edge goes to the short arrow segment while
    // the arrow itself stays on the trigger.
    var container = trigger.closest ? trigger.closest(".content-width") : null;
    var containerRect = container ? container.getBoundingClientRect() : null;
    var boundLeft = Math.max(containerRect ? containerRect.left : gutter, gutter);
    var boundRight = Math.min(
      containerRect ? containerRect.right : window.innerWidth - gutter,
      window.innerWidth - gutter
    );
    var triggerCenter = triggerRect.left + triggerRect.width / 2 - parentRect.left;
    var left = triggerCenter - width * SUMMARY_ARROW_ANCHOR_RATIO;
    var minLeft = boundLeft - parentRect.left;
    var maxLeft = boundRight - width - parentRect.left;
    left = Math.max(minLeft, Math.min(left, maxLeft));
    var arrowCenter = triggerCenter - left;
    arrowCenter = Math.max(arrowMinEdge, Math.min(arrowCenter, width - arrowMinEdge));
    tooltip.style.setProperty("--tooltip-aligned-left", left.toFixed(2) + "px");
    tooltip.style.setProperty("--tooltip-aligned-arrow-center", arrowCenter.toFixed(2) + "px");
    tooltip.classList.add("summary-tooltip--aligned");
  }

  function alignTooltip(trigger, tooltip) {
    if (!trigger || !tooltip || !tooltip.classList) {
      return;
    }
    if (
      !tooltip.classList.contains("summary-tooltip--inline") &&
      !tooltip.classList.contains("summary-tooltip--chip")
    ) {
      if (tooltip.classList.contains("summary-tooltip")) {
        alignSummaryTooltip(trigger, tooltip);
      }
      return;
    }

    var anchor = trigger.closest ? trigger.closest(".hit-explainer") : null;
    if (!anchor) {
      return;
    }

    var gutter = 16;
    var arrowInset = 14;
    var anchorRect = anchor.getBoundingClientRect();
    var triggerRect = trigger.getBoundingClientRect();
    var tooltipRect = tooltip.getBoundingClientRect();
    // Same anchor policy as the stat-card tooltip: the ratio position is the
    // standard, the content column bounds the box, and the arrow stays on
    // the trigger when the two conflict.
    var container = trigger.closest ? trigger.closest(".content-width") : null;
    var containerRect = container ? container.getBoundingClientRect() : null;
    var boundLeft = Math.max(containerRect ? containerRect.left : gutter, gutter);
    var boundRight = Math.min(
      containerRect ? containerRect.right : window.innerWidth - gutter,
      window.innerWidth - gutter
    );
    var triggerCenter = triggerRect.left + triggerRect.width / 2 - anchorRect.left;
    var minLeft = boundLeft - anchorRect.left;
    var maxLeft = boundRight - tooltipRect.width - anchorRect.left;
    var preferredArrowLeft = tooltipRect.width * SUMMARY_ARROW_ANCHOR_RATIO;
    var tooltipLeft = triggerCenter - preferredArrowLeft;

    tooltipLeft = Math.max(minLeft, Math.min(tooltipLeft, maxLeft));

    var arrowLeft = triggerCenter - tooltipLeft;
    var maxArrowLeft = Math.max(arrowInset, tooltipRect.width - arrowInset);
    arrowLeft = Math.max(arrowInset, Math.min(arrowLeft, maxArrowLeft));

    tooltip.style.setProperty("--tooltip-inline-left", tooltipLeft.toFixed(2) + "px");
    tooltip.style.setProperty("--tooltip-inline-arrow-left", arrowLeft.toFixed(2) + "px");

    if (tooltip.classList.contains("summary-tooltip--inline")) {
      // Anchor the tooltip to the trigger's own line instead of the top of
      // the positioned ancestor (the whole intro block): distance from the
      // ancestor's bottom edge up to just above the trigger. 18px matches
      // --space-md, clearing the 18px arrow like the stat-card tooltips.
      var verticalGap = 18;
      var tooltipBottom = anchorRect.bottom - triggerRect.top + verticalGap;
      tooltip.style.setProperty("--tooltip-inline-bottom", tooltipBottom.toFixed(2) + "px");
    }
  }

  function setTooltip(trigger, tooltip, open, pinned) {
    if (!trigger || !tooltip) {
      return;
    }
    clearTooltipTimer(tooltip);
    if (open) {
      hideAllTooltips(tooltip);
      tooltip.hidden = false;
      alignTooltip(trigger, tooltip);
      tooltip.dataset.state = pinned ? "pinned" : "hover";
      trigger.setAttribute("aria-expanded", "true");
    } else {
      tooltip.hidden = true;
      delete tooltip.dataset.state;
      trigger.setAttribute("aria-expanded", "false");
    }
  }

  function hideTooltip(trigger, tooltip, force) {
    if (!force && tooltip && tooltip.dataset.state === "pinned") {
      return;
    }
    setTooltip(trigger, tooltip, false, false);
  }

  function hideAllTooltips(exceptTooltip) {
    tooltipTriggers.forEach(function (trigger) {
      var tooltip = tooltipForTrigger(trigger);
      if (tooltip && tooltip !== exceptTooltip && !tooltip.hidden) {
        hideTooltip(trigger, tooltip, true);
      }
    });
  }

  function scheduleTooltipHide(trigger, tooltip) {
    if (!trigger || !tooltip || tooltip.dataset.state === "pinned") {
      return;
    }
    clearTooltipTimer(tooltip);
    tooltipTimers.set(tooltip, window.setTimeout(function () {
      if (!trigger.matches(":hover") && !tooltip.matches(":hover")) {
        hideTooltip(trigger, tooltip, true);
      }
    }, 80));
  }

  tooltipTriggers.forEach(function (trigger) {
    var tooltip = tooltipForTrigger(trigger);
    if (!tooltip) {
      return;
    }

    trigger.addEventListener("mouseenter", function () {
      if (tooltip.dataset.state !== "pinned") {
        setTooltip(trigger, tooltip, true, false);
      }
    });

    trigger.addEventListener("mouseleave", function () {
      scheduleTooltipHide(trigger, tooltip);
    });

    trigger.addEventListener("click", function () {
      if (tooltip.dataset.state === "pinned") {
        hideTooltip(trigger, tooltip, true);
        return;
      }
      setTooltip(trigger, tooltip, true, true);
    });

    tooltip.addEventListener("mouseenter", function () {
      clearTooltipTimer(tooltip);
    });

    tooltip.addEventListener("mouseleave", function () {
      scheduleTooltipHide(trigger, tooltip);
    });

    tooltip.addEventListener("click", function (event) {
      var closeButton = event.target && event.target.closest ? event.target.closest("[data-tooltip-close]") : null;
      if (closeButton) {
        hideTooltip(trigger, tooltip, true);
        trigger.focus();
      }
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    tooltipTriggers.forEach(function (trigger) {
      var tooltip = tooltipForTrigger(trigger);
      if (tooltip && !tooltip.hidden) {
        hideTooltip(trigger, tooltip, true);
        trigger.focus();
      }
    });
  });

  if (media && media.addEventListener) {
    media.addEventListener("change", function () {
      if (!cookieTheme()) {
        applyTheme(preferredTheme(), false);
      }
    });
  }

  function pushAnalyticsEvent(eventName, metadata) {
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(Object.assign({
      event: eventName,
      page_path: window.location.pathname,
      page_language: document.documentElement.lang || ""
    }, metadata || {}));
  }

  document.addEventListener("click", function (event) {
    var link = event.target && event.target.closest ? event.target.closest("a") : null;
    if (!link) {
      return;
    }

    var trackedEvent = link.getAttribute("data-analytics-event");
    var href = link.getAttribute("href") || "";
    if (!trackedEvent && href.indexOf("https://github.com/helga-agentur/worldcup-predictions") === 0) {
      trackedEvent = "helga_github_click";
    }
    if (!trackedEvent) {
      return;
    }

    pushAnalyticsEvent(trackedEvent, {
      link_url: link.href,
      link_text: (link.textContent || "").trim(),
      target_language: link.getAttribute("data-analytics-language") || ""
    });
  });

  var scrollThresholds = [25, 50, 75, 90, 100];
  var sentScrollThresholds = {};

  function maxScrollPercent() {
    var documentElement = document.documentElement;
    var body = document.body;
    var scrollTop = window.scrollY || documentElement.scrollTop || body.scrollTop || 0;
    var viewportHeight = window.innerHeight || documentElement.clientHeight || 0;
    var scrollHeight = Math.max(
      body.scrollHeight,
      documentElement.scrollHeight,
      body.offsetHeight,
      documentElement.offsetHeight,
      body.clientHeight,
      documentElement.clientHeight
    );
    if (scrollHeight <= viewportHeight) {
      return 100;
    }
    return Math.min(100, Math.round(((scrollTop + viewportHeight) / scrollHeight) * 100));
  }

  function trackScrollDepth() {
    var percent = maxScrollPercent();
    scrollThresholds.forEach(function (threshold) {
      if (percent >= threshold && !sentScrollThresholds[threshold]) {
        sentScrollThresholds[threshold] = true;
        pushAnalyticsEvent("helga_scroll_depth", {
          scroll_depth: threshold
        });
      }
    });
  }

  var scrollTimer = 0;
  window.addEventListener("scroll", function () {
    if (scrollTimer) {
      return;
    }
    scrollTimer = window.setTimeout(function () {
      scrollTimer = 0;
      trackScrollDepth();
    }, 250);
  }, { passive: true });
  window.addEventListener("load", trackScrollDepth);
  trackScrollDepth();
})();
