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
})();
