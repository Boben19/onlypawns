(function () {
  // service worker + install button
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }
  var deferred;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferred = e;
    document.querySelectorAll(".install").forEach(function (b) { b.style.display = "inline-block"; });
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".install");
    if (b && deferred) { deferred.prompt(); deferred = null; b.style.display = "none"; }
    var m = e.target.closest(".menu-btn");
    if (m) { document.querySelector(".side").classList.toggle("open"); }
  });

  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }

  // Bot Star chat
  var form = document.getElementById("chat-form");
  if (form) {
    var box = document.getElementById("chat-box");
    var input = document.getElementById("chat-input");
    var session = form.dataset.session;
    box.scrollTop = box.scrollHeight;
    window.scrollTo(0, document.body.scrollHeight);
    function add(text, who) {
      var d = document.createElement("div");
      d.className = "bub " + who;
      d.textContent = text;
      box.appendChild(d);
      window.scrollTo(0, document.body.scrollHeight);
    }
    function send(text) {
      if (!text.trim()) return;
      add(text, "me");
      input.value = "";
      fetch("/api/bot/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ message: text, session: session })
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.session) session = data.session;
        add(data.reply || data.detail || "Something went wrong. Try again.", "bot");
        if (data.support_ticket) {
          var a = document.createElement("a");
          a.href = "/app/help/" + data.support_ticket + "/";
          a.textContent = "Open your support request";
          a.className = "btn ghost sm";
          box.appendChild(a);
        }
      }).catch(function () { add("You seem to be offline. Try again when you have signal.", "bot"); });
    }
    form.addEventListener("submit", function (e) { e.preventDefault(); send(input.value); });
    document.querySelectorAll(".chip").forEach(function (c) { c.addEventListener("click", function () { send(c.textContent); }); });
  }

  // Branch map (Leaflet is loaded only on that page)
  var map = document.getElementById("map");
  if (map && window.L) {
    var data = JSON.parse(document.getElementById("branch-data").textContent);
    var m = L.map("map");
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "&copy; OpenStreetMap" }).addTo(m);
    var pts = data.map(function (b) {
      L.marker([b.lat, b.lng]).addTo(m).bindPopup("<b>" + b.name + "</b><br>" + b.address);
      return [b.lat, b.lng];
    });
    if (pts.length) m.fitBounds(pts, { padding: [30, 30], maxZoom: 12 });
    document.querySelectorAll("[data-fly]").forEach(function (el) {
      el.addEventListener("click", function () { m.flyTo([+el.dataset.lat, +el.dataset.lng], 15); window.scrollTo(0, 0); });
    });
  }

  // Google Sign-In callback: keeps the token in the form (and submits it on the login screen)
  window.onGoogle = function (resp) {
    var input = document.querySelector("input[name=credential]");
    input.value = resp.credential;
    try {
      var payload = JSON.parse(atob(resp.credential.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      var tag = document.getElementById("g-email");
      if (tag) tag.textContent = "Signed in with Google as " + payload.email;
    } catch (e) {}
    var f = input.form;
    if (f && f.dataset.autosubmit) f.submit();
  };
})();
