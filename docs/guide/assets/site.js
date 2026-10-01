/* Gemstone Guide — language and theme. Loaded in <head> so the first paint is already correct. */
(function () {
  var root = document.documentElement;
  var LANG_KEY = "gemstone-guide-lang";
  var THEME_KEY = "gemstone-guide-theme";

  function load(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function save(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ } }

  function initialLang() {
    var q = null;
    try { q = new URLSearchParams(window.location.search).get("lang"); } catch (e) { q = null; }
    if (q === "en" || q === "ko") return q;
    var stored = load(LANG_KEY);
    if (stored === "en" || stored === "ko") return stored;
    var nav = (navigator.language || "en").toLowerCase();
    return nav.indexOf("ko") === 0 ? "ko" : "en";
  }

  function applyLang(lang) {
    root.setAttribute("data-lang", lang);
    root.setAttribute("lang", lang);
    var t = root.getAttribute("data-title-" + lang);
    if (t) document.title = t;
    var btn = document.getElementById("lang-toggle");
    if (btn) {
      btn.setAttribute("aria-label", lang === "ko" ? "Switch to English" : "한국어로 보기");
    }
  }

  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }

  function currentTheme() {
    var set = root.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  applyLang(initialLang());
  applyTheme(load(THEME_KEY));

  document.addEventListener("DOMContentLoaded", function () {
    applyLang(root.getAttribute("data-lang"));

    var langBtn = document.getElementById("lang-toggle");
    if (langBtn) langBtn.addEventListener("click", function () {
      var next = root.getAttribute("data-lang") === "ko" ? "en" : "ko";
      applyLang(next);
      save(LANG_KEY, next);
    });

    var themeBtn = document.getElementById("theme-toggle");
    if (themeBtn) themeBtn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      applyTheme(next);
      save(THEME_KEY, next);
    });
  });
})();
