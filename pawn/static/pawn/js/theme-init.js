/* Runs before first paint so the page never flashes the wrong theme (idea from dexTHER). */
(function () {
  var pref = "system";
  try { pref = localStorage.getItem("op-theme") || "system"; } catch (e) {}
  if (pref !== "light" && pref !== "dark") pref = "system";
  var dark = pref === "dark" || (pref === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  var r = document.documentElement;
  r.setAttribute("data-theme", dark ? "dark" : "light");
  r.setAttribute("data-theme-pref", pref);
})();
