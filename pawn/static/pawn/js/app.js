(function () {
  "use strict";

  /* ---------- small helpers ---------- */
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function csrf() { var m = document.cookie.match(/csrftoken=([^;]+)/); return m ? m[1] : ""; }
  function peso(n) { return "\u20B1" + Number(n).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  /* ---------- service worker + install button ---------- */
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }
  var deferred;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault(); deferred = e;
    $$(".install").forEach(function (b) { b.style.display = "inline-block"; });
  });

  /* ---------- toasts ---------- */
  function toast(msg, kind) {
    var box = $("#toasts");
    if (!box) { box = el("div"); box.id = "toasts"; box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    var t = el("div", "toast " + (kind || ""), msg);
    box.appendChild(t);
    setTimeout(function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 300); }, 3200);
  }
  window.toast = toast;

  /* ---------- flash messages: closable, auto-fade the friendly ones ---------- */
  $$(".flash").forEach(function (f) {
    var x = el("button", "x", "\u00D7"); x.type = "button"; x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", function () { f.classList.add("out"); setTimeout(function () { f.remove(); }, 250); });
    f.appendChild(x);
    if (f.classList.contains("success") || f.classList.contains("info") && f.getAttribute("role") === "status") {
      setTimeout(function () { if (f.parentNode) x.click(); }, 7000);
    }
  });

  /* ---------- confirm dialog (data-confirm on a form or button) ---------- */
  function confirmDialog(message, okLabel, danger) {
    return new Promise(function (resolve) {
      var back = el("div", "modal-back"), box = el("div", "modal");
      box.setAttribute("role", "alertdialog"); box.setAttribute("aria-modal", "true");
      var p = el("p", "", message);
      var row = el("div", "row");
      var ok = el("button", "btn" + (danger ? " danger" : ""), okLabel || "Yes, continue"); ok.type = "button";
      var no = el("button", "btn ghost", "Cancel"); no.type = "button";
      row.appendChild(ok); row.appendChild(no); box.appendChild(p); box.appendChild(row); back.appendChild(box);
      document.body.appendChild(back);
      function done(v) { document.removeEventListener("keydown", key); back.remove(); resolve(v); }
      function key(e) { if (e.key === "Escape") done(false); }
      ok.addEventListener("click", function () { done(true); });
      no.addEventListener("click", function () { done(false); });
      back.addEventListener("click", function (e) { if (e.target === back) done(false); });
      document.addEventListener("keydown", key);
      no.focus();
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-confirm]");
    if (!b || b.dataset.confirmed) return;
    var form = b.form || b.closest("form");
    if (b.tagName === "A") {
      e.preventDefault();
      confirmDialog(b.dataset.confirm, b.dataset.ok, b.classList.contains("danger")).then(function (v) { if (v) location.href = b.href; });
      return;
    }
    if (!form) return;
    e.preventDefault();
    confirmDialog(b.dataset.confirm, b.dataset.ok, b.classList.contains("danger")).then(function (v) {
      if (!v) return;
      b.dataset.confirmed = "1";
      if (form.requestSubmit) form.requestSubmit(b); else form.submit();
    });
  }, true);

  /* ---------- general clicks ---------- */
  document.addEventListener("click", function (e) {
    var inst = e.target.closest(".install");
    if (inst && deferred) { deferred.prompt(); deferred = null; inst.style.display = "none"; }

    var m = e.target.closest(".menu-btn");
    var side = $(".side");
    if (m && side) { side.classList.toggle("open"); return; }
    if (side && side.classList.contains("open") && !e.target.closest(".side")) side.classList.remove("open");

    var c = e.target.closest("[data-copy]");
    if (c) {
      var text = c.dataset.copy;
      var done = function () { toast("Copied " + text, "success"); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () { toast("Couldn't copy", "error"); });
      else { var t = el("textarea"); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); done(); } catch (x) {} t.remove(); }
    }

    var q = e.target.closest("img.qr");
    if (q) {
      var back = el("div", "modal-back qr-zoom"), big = el("img"); big.src = q.src; big.alt = q.alt;
      back.appendChild(big); back.appendChild(el("p", "soft small", "Tap anywhere to close. Raise your screen brightness for easier scanning."));
      back.addEventListener("click", function () { back.remove(); });
      document.body.appendChild(back);
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { var s = $(".side.open"); if (s) s.classList.remove("open"); }
    // "/" jumps to the search box (staff)
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      var f = $("#quick-find") || $(".search .in") || $(".tbl-filter input");
      if (f) { e.preventDefault(); f.focus(); f.select(); }
    }
  });

  /* ---------- no double submits, with a spinner on the button ---------- */
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f || f.method.toLowerCase() === "get" || f.dataset.noLoad !== undefined) return;
    if (f.dataset.busy) { e.preventDefault(); return; }
    if (f.id === "chat-form") return;
    f.dataset.busy = "1";
    var b = e.submitter || $("button:not([type=button]), input[type=submit]", f);
    if (b) b.classList.add("loading");
    f.setAttribute("aria-busy", "true");
    setTimeout(function () { delete f.dataset.busy; if (b) b.classList.remove("loading"); f.removeAttribute("aria-busy"); }, 15000);
  });
  window.addEventListener("pageshow", function () {
    $$("form[data-busy]").forEach(function (f) { delete f.dataset.busy; });
    $$(".btn.loading").forEach(function (b) { b.classList.remove("loading"); });
  });

  /* ---------- inline validation: only after the field was touched ---------- */
  document.addEventListener("blur", function (e) {
    if (e.target.classList && e.target.classList.contains("in")) e.target.classList.add("touched");
  }, true);

  /* ---------- online / offline banner ---------- */
  function net() {
    var b = $("#offline-bar");
    if (!navigator.onLine) {
      if (!b) { b = el("div", "", "You're offline. Some things won't work until you have signal."); b.id = "offline-bar"; document.body.appendChild(b); }
    } else if (b) { b.remove(); toast("Back online", "success"); }
  }
  window.addEventListener("online", net); window.addEventListener("offline", net); net();

  /* ---------- PIN boxes: digits only, show/hide, auto-open when complete ---------- */
  $$("input.pin").forEach(function (inp) {
    inp.addEventListener("input", function () {
      var v = inp.value.replace(/\D/g, "").slice(0, 6);
      if (v !== inp.value) inp.value = v;
      var f = inp.form;
      if (f && inp.value.length === 6) {
        var step = f.querySelector("input[name=step][value=pin]");
        if (step) { if (f.requestSubmit) f.requestSubmit(); else f.submit(); }
      }
      var two = $("#pin2"), one = $("#pin");
      if (two && one) {
        var hint = $("#pin-match");
        if (!hint) { hint = el("div", "help"); hint.id = "pin-match"; two.parentNode.appendChild(hint); }
        if (two.value.length === 0) hint.textContent = "";
        else if (two.value === one.value && one.value.length === 6) { hint.textContent = "PINs match"; hint.className = "help okc"; }
        else { hint.textContent = "PINs don't match yet"; hint.className = "help"; }
      }
    });
    var eye = el("button", "eye", "Show"); eye.type = "button"; eye.setAttribute("aria-label", "Show or hide PIN");
    eye.addEventListener("click", function () {
      var show = inp.type === "password"; inp.type = show ? "text" : "password"; eye.textContent = show ? "Hide" : "Show";
    });
    inp.parentNode.style.position = "relative"; inp.parentNode.appendChild(eye);
  });

  /* ---------- Philippine mobile numbers ---------- */
  $$("input[name=mobile], input[name=mobile_no]").forEach(function (inp) {
    inp.setAttribute("inputmode", "tel"); inp.setAttribute("maxlength", "13");
    inp.addEventListener("input", function () {
      inp.value = inp.value.replace(/[^\d+]/g, "");
      var d = inp.value.replace(/^\+63/, "0");
      var ok = /^09\d{9}$/.test(d);
      inp.setCustomValidity(ok || !inp.value ? "" : "Use a mobile number like 09171234567");
      inp.classList.toggle("good", ok);
    });
  });

  /* ---------- progress to maturity on ticket cards ---------- */
  $$(".prog[data-start][data-end]").forEach(function (p) {
    var s = new Date(p.dataset.start).getTime(), en = new Date(p.dataset.end).getTime(), n = Date.now();
    var pct = en > s ? Math.max(0, Math.min(100, (n - s) / (en - s) * 100)) : 100;
    var bar = p.firstElementChild;
    requestAnimationFrame(function () { bar.style.width = pct + "%"; });
    if (pct >= 100) p.classList.add("bad"); else if (pct >= 80) p.classList.add("warn");
  });

  /* ---------- "what if I pay on..." calculator (uses /api/tickets/<id>/due/) ---------- */
  var wi = $("#whatif");
  if (wi) {
    var date = $("input[type=date]", wi), tid = wi.dataset.ticket, timer;
    function ask() {
      if (!date.value) return;
      wi.classList.add("busy");
      fetch("/api/tickets/" + tid + "/due/?date=" + encodeURIComponent(date.value), { credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          wi.classList.remove("busy");
          if (!res.ok) { $("#wi-note").textContent = res.j.detail || "Couldn't work that out."; return; }
          var d = res.j;
          $("#wi-redeem").textContent = peso(d.redeem_total);
          $("#wi-renew").textContent = peso(d.renew_total);
          var days = parseInt(d.days_to_maturity, 10), note;
          if (d.overdue === true || d.overdue === "True") note = "That date is past maturity. Pay soon so the item doesn't go to auction.";
          else if (days === 0) note = "That is your maturity date.";
          else note = days + " day" + (days === 1 ? "" : "s") + " before maturity. " + d.months + " month" + (d.months == 1 ? "" : "s") + " of interest counted.";
          $("#wi-note").textContent = note;
        }).catch(function () { wi.classList.remove("busy"); $("#wi-note").textContent = "You seem to be offline."; });
    }
    date.addEventListener("change", function () { clearTimeout(timer); timer = setTimeout(ask, 150); });
    $$("[data-days]", wi).forEach(function (b) {
      b.addEventListener("click", function () {
        var d = new Date(); d.setDate(d.getDate() + parseInt(b.dataset.days, 10));
        date.value = d.toISOString().slice(0, 10); ask();
      });
    });
    var mat = $("[data-maturity]", wi);
    if (mat) mat.addEventListener("click", function () { date.value = mat.dataset.maturity; ask(); });
  }

  /* ---------- cash change calculator on the staff payment screen ---------- */
  var chg = $("#change");
  if (chg) {
    var total = parseFloat(chg.dataset.total), cash = $("input[name=tendered]");
    function calc() {
      var v = parseFloat(cash.value);
      var out = $("#change-out");
      if (isNaN(v)) { out.textContent = "Enter the cash received"; out.className = "soft"; return; }
      var diff = v - total;
      if (diff < 0) { out.textContent = "Short by " + peso(-diff); out.className = "bad"; }
      else { out.textContent = "Change to give back: " + peso(diff); out.className = "okc"; }
    }
    cash.addEventListener("input", calc);
    var exact = $("#exact"); if (exact) exact.addEventListener("click", function () { cash.value = total.toFixed(2); calc(); cash.focus(); });
    $$("[data-bill]", chg).forEach(function (b) { b.addEventListener("click", function () { cash.value = b.dataset.bill; calc(); }); });
    cash.focus(); calc();
  }

  /* ---------- tables: filter this page + click-to-sort ---------- */
  $$("table.tbl").forEach(function (tb) {
    var body = tb.tBodies[0], head = tb.tHead;
    if (!body || !head) return;
    var rows = $$("tr", body);
    if (rows.length < 6) return;
    var wrap = tb.closest(".tbl-wrap") || tb.parentNode;
    var bar = el("div", "tbl-filter no-print"), box = el("input", "in"); box.type = "search";
    box.placeholder = "Filter this page (press /)"; box.setAttribute("aria-label", "Filter rows");
    var count = el("span", "soft small");
    bar.appendChild(box); bar.appendChild(count); wrap.parentNode.insertBefore(bar, wrap);
    function run() {
      var q = box.value.trim().toLowerCase(), n = 0;
      rows.forEach(function (r) { var show = !q || r.textContent.toLowerCase().indexOf(q) > -1; r.style.display = show ? "" : "none"; if (show) n++; });
      count.textContent = q ? n + " of " + rows.length + " shown" : "";
    }
    box.addEventListener("input", run);
    $$("th", head).forEach(function (th, i) {
      if (!th.textContent.trim()) return;
      th.classList.add("sortable"); th.tabIndex = 0;
      function sort() {
        var dir = th.dataset.dir === "asc" ? "desc" : "asc";
        $$("th", head).forEach(function (x) { delete x.dataset.dir; });
        th.dataset.dir = dir;
        function val(r) {
          var t = (r.cells[i] ? r.cells[i].textContent : "").trim();
          var num = parseFloat(t.replace(/[\u20B1,\s]/g, ""));
          return isNaN(num) || !/\d/.test(t) ? t.toLowerCase() : num;
        }
        rows.sort(function (a, b) {
          var x = val(a), y = val(b);
          return (x > y ? 1 : x < y ? -1 : 0) * (dir === "asc" ? 1 : -1);
        }).forEach(function (r) { body.appendChild(r); });
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) { if (e.key === "Enter") sort(); });
    });
  });

  /* ---------- Bot Star chat ---------- */
  var form = $("#chat-form");
  if (form) {
    var box = $("#chat-box"), input = $("#chat-input"), session = form.dataset.session, sending = false;
    function bottom() { window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }); }
    function time() { return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
    function add(text, who) {
      var d = el("div", "bub " + who); d.appendChild(document.createTextNode(text));
      d.appendChild(el("span", "ts", time())); box.appendChild(d); bottom(); return d;
    }
    function typing() {
      var d = el("div", "bub bot typing"); d.innerHTML = "<i></i><i></i><i></i>"; d.setAttribute("aria-label", "Bot Star is typing");
      box.appendChild(d); bottom(); return d;
    }
    bottom();
    function send(text) {
      text = text.trim();
      if (!text || sending) return;
      sending = true; form.classList.add("wait");
      var chips = $(".chips"); if (chips) chips.style.display = "none";
      add(text, "me"); input.value = "";
      var t = typing();
      fetch("/api/bot/", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ message: text, session: session })
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.session) session = data.session;
        setTimeout(function () {
          t.remove();
          add(data.reply || data.detail || "Something went wrong. Try again.", "bot");
          if (data.support_ticket) {
            var a = el("a", "btn ghost sm", "Open your support request"); a.href = "/app/help/" + data.support_ticket + "/"; box.appendChild(a); bottom();
          }
          sending = false; form.classList.remove("wait"); input.focus();
        }, 350);
      }).catch(function () {
        t.remove(); add("You seem to be offline. Try again when you have signal.", "bot"); sending = false; form.classList.remove("wait");
      });
    }
    form.addEventListener("submit", function (e) { e.preventDefault(); send(input.value); });
    $$(".chip").forEach(function (c) { c.addEventListener("click", function () { send(c.textContent); }); });
  }

  /* ---------- theme: auto / light / dark ---------- */
  (function () {
    var root = document.documentElement;
    function apply(pref) {
      var dark = pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      root.setAttribute("data-theme", dark ? "dark" : "light"); root.setAttribute("data-theme-pref", pref);
      $$("[data-theme-choice]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.themeChoice === pref)); });
      var m = document.querySelector("meta[name=theme-color]"); if (m) m.setAttribute("content", dark ? "#0c0a14" : "#f6eff5");
    }
    apply(root.getAttribute("data-theme-pref") || "system");
    $$("[data-theme-choice]").forEach(function (b) {
      b.addEventListener("click", function () { try { localStorage.setItem("op-theme", b.dataset.themeChoice); } catch (e) {} apply(b.dataset.themeChoice); });
    });
    if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      if (root.getAttribute("data-theme-pref") === "system") apply("system");
    });
  })();

  /* ---------- small screens: tables turn into stacked cards ---------- */
  $$("table.tbl").forEach(function (tb) {
    var hr = tb.tHead ? tb.tHead.rows[0] : (tb.rows[0] && tb.rows[0].querySelector("th") ? tb.rows[0] : null);
    if (!hr) return;
    var labels = $$("th", hr).map(function (th) { return th.textContent.trim(); });
    $$("tr", tb).forEach(function (r) {
      if (r === hr) return;
      Array.prototype.forEach.call(r.cells, function (c, i) { if (labels[i]) c.setAttribute("data-label", labels[i]); });
    });
    if (!tb.tHead) { hr.style.display = "none"; }
    tb.classList.add("stack");
  });

  /* ---------- place search box with suggestions (shared by the branch map and the staff picker) ---------- */
  function placeSearch(input, list, onPick) {
    var timer, ctl;
    function hide() { list.hidden = true; list.replaceChildren(); }
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 3) { hide(); return; }
      timer = setTimeout(function () {
        if (ctl) ctl.abort(); ctl = window.AbortController ? new AbortController() : null;
        fetch("/geo/search/?q=" + encodeURIComponent(q), { credentials: "same-origin", signal: ctl ? ctl.signal : undefined })
          .then(function (r) { return r.json(); }).then(function (j) {
            list.replaceChildren();
            if (j.error) { toast(j.error, "error"); return; }
            (j.items || []).forEach(function (it) {
              var li = el("li", "", it.label); li.setAttribute("role", "option");
              li.addEventListener("mousedown", function (e) { e.preventDefault(); input.value = it.label; hide(); onPick(it); });
              list.appendChild(li);
            });
            list.hidden = !list.children.length;
          }).catch(function () {});
      }, 350);
    });
    input.addEventListener("blur", function () { setTimeout(hide, 150); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") hide();
      if (e.key === "Enter" && !list.hidden && list.firstChild) { e.preventDefault(); list.firstChild.dispatchEvent(new Event("mousedown")); }
    });
  }
  function pin(cls) { return window.L ? L.divIcon({ className: "", html: '<div class="pin-dot ' + (cls || "") + '"></div>', iconSize: [22, 22], iconAnchor: [11, 22], popupAnchor: [0, -20] }) : null; }
  function km(a, b, c, d) {
    var R = 6371, r = Math.PI / 180, dLat = (c - a) * r, dLon = (d - b) * r;
    var x = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.cos(a * r) * Math.cos(c * r) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(x));
  }
  function tileLayer(map) {
    var warned = false;
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "&copy; OpenStreetMap", referrerPolicy: "strict-origin-when-cross-origin" })
      .on("tileerror", function () { if (!warned) { warned = true; toast("Some map tiles couldn't load. Check your connection.", "error"); } }).addTo(map);
  }

  /* ---------- Branch map: markers, open now, nearest branch, weather, place search ---------- */
  var mapEl = document.getElementById("map");
  if (mapEl && window.L && document.getElementById("branch-data")) {
    var data = JSON.parse(document.getElementById("branch-data").textContent);
    var mp = L.map("map", { zoomControl: true, scrollWheelZoom: false });
    tileLayer(mp);
    var cards = $$(".bcard"), markers = [], pts = [], youMarker, youRing;
    data.forEach(function (b, i) {
      var m = L.marker([b.lat, b.lng], { icon: pin(), title: b.name, keyboard: true }).addTo(mp);
      var pop = document.createElement("div"); pop.innerHTML = "<b></b><br><span></span>"; pop.firstChild.textContent = b.name; pop.lastChild.textContent = b.address;
      m.bindPopup(pop);
      m.on("click", function () { if (cards[i]) { cards[i].scrollIntoView({ behavior: "smooth", block: "center" }); } });
      markers.push(m); pts.push([b.lat, b.lng]);
    });
    if (pts.length) mp.fitBounds(pts, { padding: [30, 30], maxZoom: 13 });
    mapEl.addEventListener("click", function () { mp.scrollWheelZoom.enable(); }, { once: true });

    // open now / closed, from the times printed on each card
    function minutes(t) { var p = t.split(":"); return (+p[0]) * 60 + (+p[1]); }
    function refreshOpen() {
      var now = new Date(), cur = now.getHours() * 60 + now.getMinutes();
      cards.forEach(function (c) {
        var flag = $(".open-flag", c), open = cur >= minutes(c.dataset.open) && cur < minutes(c.dataset.close);
        flag.textContent = open ? "Open now" : "Closed now"; flag.className = "badge open-flag " + (open ? "ok" : "mute");
      });
    }
    refreshOpen(); setInterval(refreshOpen, 60000);

    // weather chip per branch (Open-Meteo through our server, cached)
    function weather() {
      cards.forEach(function (c, i) {
        fetch("/geo/weather/?lat=" + c.dataset.lat + "&lon=" + c.dataset.lng, { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : null; }).then(function (w) {
            if (!w) return; var tag = $(".wx", c);
            tag.textContent = w.temp + "\u00B0C, " + w.desc.toLowerCase() + (w.rain ? ", bring an umbrella" : "");
          }).catch(function () {});
      });
    }
    weather();

    // distances and nearest branch from a point
    function from(lat, lng, label) {
      var best = -1, bestD = 1e9;
      cards.forEach(function (c, i) {
        var d = km(lat, lng, +c.dataset.lat, +c.dataset.lng);
        c.dataset.d = d; $(".dist", c).textContent = (d < 1 ? Math.round(d * 1000) + " m" : d.toFixed(1) + " km") + " away";
        c.classList.remove("near"); markers[i].setIcon(pin());
        if (d < bestD) { bestD = d; best = i; }
      });
      var list = $("#blist");
      cards.slice().sort(function (a, b) { return a.dataset.d - b.dataset.d; }).forEach(function (c) { list.appendChild(c); });
      if (best > -1) {
        cards[best].classList.add("near"); markers[best].setIcon(pin("near"));
        $("#nearest").textContent = "Closest to " + label + ": " + cards[best].dataset.name + ", " + (bestD < 1 ? Math.round(bestD * 1000) + " m" : bestD.toFixed(1) + " km") + ".";
        mp.fitBounds([[lat, lng], [+cards[best].dataset.lat, +cards[best].dataset.lng]], { padding: [50, 50], maxZoom: 15 });
      }
    }
    var loc = $("#locate");
    if (loc) loc.addEventListener("click", function () {
      if (!("geolocation" in navigator)) { toast("This device can't share its location. Try the search box.", "error"); return; }
      loc.classList.add("loading");
      navigator.geolocation.getCurrentPosition(function (p) {
        loc.classList.remove("loading");
        var ll = [p.coords.latitude, p.coords.longitude];
        if (youMarker) mp.removeLayer(youMarker); if (youRing) mp.removeLayer(youRing);
        youMarker = L.marker(ll, { icon: pin("you"), interactive: false }).addTo(mp);
        if (p.coords.accuracy) youRing = L.circle(ll, { radius: p.coords.accuracy, interactive: false, color: "#2f8cff", weight: 1, fillOpacity: .08 }).addTo(mp);
        from(ll[0], ll[1], "you");
      }, function (err) {
        loc.classList.remove("loading");
        toast(err.code === 1 ? "Location is blocked. Allow it in your browser, or search for a place instead." : "Couldn't find you. Try the search box.", "error");
      }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    });
    var ps = $("#place");
    if (ps) {
      placeSearch(ps, $("#suggest"), function (it) { from(it.lat, it.lon, "that place"); });
      $("#place-form").addEventListener("submit", function (e) { e.preventDefault(); });
    }
    $$("[data-fly]").forEach(function (e) {
      e.addEventListener("click", function () { mp.flyTo([+e.dataset.lat, +e.dataset.lng], 16); mapEl.scrollIntoView({ behavior: "smooth", block: "center" }); });
    });
  }

  /* ---------- Staff: pick a branch location on the map ---------- */
  var pick = document.getElementById("pick-map");
  if (pick && window.L) {
    var latI = document.getElementById("id_latitude"), lngI = document.getElementById("id_longitude");
    var start = [parseFloat(latI.value) || 9.7392, parseFloat(lngI.value) || 118.7353];
    var pm = L.map("pick-map").setView(start, latI.value ? 16 : 12);
    tileLayer(pm);
    var pmk = L.marker(start, { draggable: true, icon: pin(), keyboard: true, title: "Branch location. Drag to move it." }).addTo(pm);
    function setPt(ll, move) {
      pmk.setLatLng(ll); latI.value = ll.lat.toFixed(6); lngI.value = ll.lng.toFixed(6);
      if (move) pm.setView(ll, Math.max(pm.getZoom(), 16));
    }
    pmk.on("dragend", function () { setPt(pmk.getLatLng()); });
    pm.on("click", function (e) { setPt(e.latlng); });
    function fromInputs() { var a = parseFloat(latI.value), b = parseFloat(lngI.value); if (!isNaN(a) && !isNaN(b)) { pmk.setLatLng([a, b]); pm.panTo([a, b]); } }
    latI.addEventListener("change", fromInputs); lngI.addEventListener("change", fromInputs);
    placeSearch(document.getElementById("pick-search"), document.getElementById("pick-suggest"), function (it) { setPt(L.latLng(it.lat, it.lon), true); });
    setTimeout(function () { pm.invalidateSize(); }, 200);
  }

  /* ---------- Google Sign-In callback ---------- */
  window.onGoogle = function (resp) {
    var input = $("input[name=credential]");
    input.value = resp.credential;
    try {
      var payload = JSON.parse(atob(resp.credential.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      var tag = document.getElementById("g-email");
      if (tag) tag.textContent = "Signed in with Google as " + payload.email;
      toast("Google account chosen", "success");
    } catch (e) {}
    var f = input.form;
    if (f && f.dataset.autosubmit) f.submit();
  };
})();
