(function () {
  var cookieName = "helga_theme";
  var root = document.documentElement;
  var toggle = document.querySelector("[data-theme-toggle]");
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

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
    if (toggle) {
      toggle.checked = theme === "dark";
    }
    if (explicit) {
      document.cookie = cookieName + "=" + theme + "; Path=/; Max-Age=31536000; SameSite=Lax";
    }
  }

  applyTheme(cookieTheme() || preferredTheme(), false);

  if (toggle) {
    toggle.addEventListener("change", function () {
      applyTheme(toggle.checked ? "dark" : "light", true);
    });
  }

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
