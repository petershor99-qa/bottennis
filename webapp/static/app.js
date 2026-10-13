// Mini App клуба. v2.160.0 — «Рейтинг клуба»; v2.161.0 — экраны «только
// чтение»; v2.162.0 — свой интерфейс приложения: вкладки «Клуб / Рейтинг / Я»,
// главная с подиумом и матчем дня, профиль-табло с разделами на одном экране,
// ачивки сеткой с уровнями, «ты против него», фото игроков, анимации и вибрация.
// Тексты приходят с сервера (bot/webapp/api.py). Данные вставляются через
// textContent — имена не интерпретируются как HTML.
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  var root = document.documentElement;
  var app = document.getElementById("app");
  var tabbar = document.getElementById("tabbar");
  var DARK_BG = "#0A0F1F";
  var SVG = "http://www.w3.org/2000/svg";
  var renderToken = 0;
  var viewerId = null;

  // ── Тема, вибрация ─────────────────────────────────────────────────────────
  function applyTheme() {
    var dark = tg && tg.colorScheme === "dark";
    root.setAttribute("data-theme", dark ? "dark" : "light");
    if (!tg) return;
    try {
      var bg = dark ? DARK_BG : (tg.themeParams.secondary_bg_color || "#EFEFF4");
      tg.setHeaderColor(bg);
      tg.setBackgroundColor(bg);
      if (tg.setBottomBarColor) tg.setBottomBarColor(bg);
    } catch (e) { /* старые клиенты Telegram */ }
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "select") tg.HapticFeedback.selectionChanged();
      else tg.HapticFeedback.impactOccurred("light");
    } catch (e) { /* нет вибрации — не страшно */ }
  }

  // ── Разметка ───────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svg(tag, attrs) {
    var node = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  // Линейные значки: только контуры, толщина 1.6, цвет — currentColor
  var ICONS = {
    home: "M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z",
    rating: "M5 20V10M12 20V4M19 20v-7",
    user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 20c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5",
    trophy: "M8 4h8v5a4 4 0 0 1-8 0zM8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8 20h8M10 17h4",
    crown: "M4 8l4 4 4-7 4 7 4-4-2 10H6z",
    calendar: "M5 6h14v14H5zM5 10h14M9 4v4M15 4v4",
    list: "M9 6h11M9 12h11M9 18h11M5 6h.01M5 12h.01M5 18h.01",
    flag: "M5 21V4M5 4h11l-2 4 2 4H5",
    flame: "M12 3c3 4 6 6 6 11a6 6 0 0 1-12 0c0-3 2-5 3-7 1 2 2 3 3 3 0-3 0-5 0-7z",
    target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 12h.01",
    bars: "M4 20h16M6 16v-5M10 16V7M14 16v-8M18 16V5",
    users: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM3 20c1-3 3.5-5 6-5s5 2 6 5M16 4.5a3.5 3.5 0 0 1 0 6.5M18 15c1.5.6 2.5 2.4 3 5",
    lock: "M6.5 11h11v9h-11zM9 11V8a3 3 0 0 1 6 0v3",
    swords: "M4 4l9 9M4 4h4M4 4v4M20 4l-9 9M20 4h-4M20 4v4M7 15l-3 3 2 2 3-3M17 15l3 3-2 2-3-3",
    chevron: "M9 6l6 6-6 6"
  };

  function icon(name, cls) {
    var s = svg("svg", { "class": "ico" + (cls ? " " + cls : ""), viewBox: "0 0 24 24", "aria-hidden": "true" });
    s.appendChild(svg("path", { d: ICONS[name] || ICONS.chevron }));
    return s;
  }

  // Иконка из набора static/icons.svg (Tabler Icons, MIT) — иконки ачивок
  function useIcon(name, cls) {
    var s = svg("svg", { "class": "ico" + (cls ? " " + cls : ""), viewBox: "0 0 24 24", "aria-hidden": "true" });
    var u = svg("use", { href: "static/icons.svg#ti-" + name });
    s.appendChild(u);
    return s;
  }

  function go(route) { haptic("impact"); location.hash = "#/" + route; }

  function clickable(node, route) {
    node.classList.add("tap");
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.addEventListener("click", function () { go(route); });
    node.addEventListener("keydown", function (e) { if (e.key === "Enter") go(route); });
    return node;
  }

  function header(title, sub) {
    var h = el("header", "screen-head");
    h.appendChild(el("h1", "screen-title", title));
    if (sub) h.appendChild(el("p", "screen-sub", sub));
    return h;
  }

  function section(title) {
    var wrap = el("section", "group");
    if (title) wrap.appendChild(el("h2", "group-title", title));
    var list = el("ul", "list");
    wrap.appendChild(list);
    wrap.list = list;
    return wrap;
  }

  function navRow(title, value, route, iconName) {
    var li = clickable(el("li", "row row-nav"), route);
    if (iconName) li.appendChild(icon(iconName, "ico-lead"));
    var body = el("div", "body");
    body.appendChild(el("div", "name", title));
    li.appendChild(body);
    if (value) li.appendChild(el("div", "meta", value));
    li.appendChild(icon("chevron", "chev"));
    return li;
  }

  function progressBar(fraction) {
    var bar = el("div", "progress");
    var fill = el("i");
    fill.style.width = Math.round(Math.max(0, Math.min(1, fraction)) * 100) + "%";
    bar.appendChild(fill);
    return bar;
  }

  function formStrip(marks, cls) {
    var wrap = el("div", "strip " + (cls || ""));
    marks.forEach(function (m) { wrap.appendChild(el("i", "cell cell-" + m)); });
    return wrap;
  }

  // Элемент из строк бота: короткая подпись — в две колонки, длинная — стопкой
  function itemRow(item) {
    var li = el("li", "row");
    if (item.form) {
      li.classList.add("row-pair");
      li.appendChild(el("div", "label", item.label));
      li.appendChild(formStrip(item.form, "strip-inline"));
      return li;
    }
    var shortPair = item.label && item.value !== undefined && item.label.length <= 24 && item.value.length <= 28;
    if (shortPair && item.progress === undefined) {
      li.classList.add("row-pair");
      li.appendChild(el("div", "label", item.label));
      li.appendChild(el("div", "value", item.value));
      if (item.note) li.appendChild(el("div", "sub sub-wide", item.note));
      return li;
    }
    var body = el("div", "body");
    if (item.label) {
      var top = el("div", "pair-top");
      top.appendChild(el("span", "name", item.label));
      if (item.progress !== undefined) top.appendChild(el("span", "value", item.value));
      body.appendChild(top);
      if (item.progress === undefined) body.appendChild(el("div", "sub", item.value));
    } else {
      body.appendChild(el("div", "text", item.text));
    }
    if (item.note) body.appendChild(el("div", "sub", item.note));
    if (item.progress !== undefined) body.appendChild(progressBar(item.progress));
    li.appendChild(body);
    return li;
  }

  function emptyState(container, text) { container.appendChild(el("p", "state", text)); }

  // ── Фото игроков: буква сразу, фото подменяет её, когда загрузится ─────────
  var photoCache = {};

  function avatar(person, size) {
    var a = el("div", "avatar avatar-" + (size || "md"), person.initial);
    a.style.setProperty("--hue", String((person.id * 47) % 360));
    loadPhoto(person.id, a);
    return a;
  }

  function loadPhoto(id, node) {
    function apply(url) {
      if (!url) return;
      node.style.backgroundImage = "url(" + url + ")";
      node.classList.add("has-photo");
      node.textContent = "";
    }
    var cached = photoCache[id];
    if (cached !== undefined) {
      if (cached && typeof cached.then === "function") cached.then(apply);
      else apply(cached);
      return;
    }
    var initData = tg && tg.initData;
    if (!initData) return;
    var pending = fetch("api/photo/" + id, { headers: { "Authorization": "tma " + initData } })
      .then(function (r) { return r.ok ? r.blob() : null; })
      .then(function (b) { var url = b ? URL.createObjectURL(b) : null; photoCache[id] = url; return url; },
            function () { photoCache[id] = null; return null; });
    photoCache[id] = pending;
    pending.then(apply);
  }

  // ── Анимации ───────────────────────────────────────────────────────────────
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function countUp(node, text) {
    // «1080.1», «60%», «+12.4» — досчитывается число, остальное как есть
    var m = /^([^\d−-]*)([−-]?)(\d+(?:\.\d+)?)(.*)$/.exec(text);
    if (!m || reduceMotion) { node.textContent = text; return; }
    var target = parseFloat(m[3]), decimals = (m[3].split(".")[1] || "").length;
    var start = null, duration = 650;
    function frame(ts) {
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / duration);
      var eased = 1 - Math.pow(1 - p, 3);
      node.textContent = m[1] + m[2] + (target * eased).toFixed(decimals) + m[4];
      if (p < 1) requestAnimationFrame(frame);
    }
    node.textContent = m[1] + m[2] + (0).toFixed(decimals) + m[4];
    requestAnimationFrame(frame);
  }

  function num(cls, text) { var n = el("div", cls); countUp(n, text); return n; }

  // ── API ────────────────────────────────────────────────────────────────────
  // Ответы держим минуту: переключение разделов профиля и возврат «назад» не
  // ходят на сервер заново. Ошибки не кэшируются. Данные меняются, только когда
  // вносят матч, — минутная давность не заметна.
  var CACHE_TTL_MS = 60 * 1000;
  var apiCache = {};

  function api(path) {
    var initData = tg && tg.initData;
    if (!initData) return Promise.reject(new Error("Откройте приложение из бота."));
    var hit = apiCache[path];
    if (hit && Date.now() - hit.t < CACHE_TTL_MS) return hit.p;
    var p = fetch("api/" + path, { headers: { "Authorization": "tma " + initData } })
      .then(function (resp) {
        return resp.json().then(function (body) {
          if (!resp.ok) throw new Error(body.error || "Не удалось загрузить данные.");
          return body;
        });
      }, function () { throw new Error("Нет связи с сервером. Попробуйте ещё раз."); });
    apiCache[path] = { t: Date.now(), p: p };
    p.catch(function () { delete apiCache[path]; });
    return p;
  }

  // Счётчик экранов (v2.166.0): какой экран показан — в ту же статистику, что
  // и у бота. Первый экран приносит время загрузки (в лог сервера, не в базу).
  var firstTrack = true;
  function track(route) {
    var initData = tg && tg.initData;
    if (!initData) return;
    var screen = route
      .replace(/^player\/(\d+)/, function (_, id) { return id === String(viewerId) ? "me" : "player"; })
      .replace(/\/\d+/g, "")
      .replace(/\//g, "_") || "home";
    var body = { screen: screen };
    if (firstTrack && window.performance) { body.boot_ms = Math.round(performance.now()); firstTrack = false; }
    try {
      fetch("api/track", {
        method: "POST", keepalive: true,
        headers: { "Authorization": "tma " + initData, "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).catch(function () {});
    } catch (e) { /* счётчик не должен мешать приложению */ }
  }

  // Серые «скелеты» вместо слова «Загрузка…»
  function skeleton(kind) {
    var wrap = el("div", "sk");
    var shapes = kind === "profile" ? ["sk-hero", "sk-tabs", "sk-tiles"] :
      kind === "body" ? ["sk-tiles", "sk-card"] : ["sk-title", "sk-card", "sk-card"];
    shapes.forEach(function (c) { wrap.appendChild(el("div", "sk-block " + c)); });
    return wrap;
  }

  // ── Графики ────────────────────────────────────────────────────────────────
  function ratingChart(chart) {
    var W = 320, H = 110, L = 2, R = 2, T = 8, B = 16;
    var vals = chart.values;
    var lo = Math.min.apply(null, vals.concat([chart.reference])) - 8;
    var hi = Math.max.apply(null, vals.concat([chart.reference])) + 8;
    function x(i) { return L + (W - L - R) * (vals.length === 1 ? 0.5 : i / (vals.length - 1)); }
    function y(v) { return T + (H - T - B) * (1 - (v - lo) / (hi - lo)); }
    var wrap = el("div", "chart-wrap");
    var s = svg("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img" });
    var ref = y(chart.reference);
    s.appendChild(svg("line", { "class": "chart-ref", x1: L, x2: W - R, y1: ref, y2: ref }));
    var pts = vals.map(function (v, i) { return x(i).toFixed(1) + "," + y(v).toFixed(1); });
    var bottom = (H - B).toFixed(1);
    s.appendChild(svg("path", { "class": "chart-area",
      d: "M" + x(0).toFixed(1) + "," + bottom + " L" + pts.join(" L") + " L" + x(vals.length - 1).toFixed(1) + "," + bottom + " Z" }));
    s.appendChild(svg("polyline", { "class": "chart-line", points: pts.join(" "), pathLength: "1" }));
    var last = vals.length - 1;
    var dot = svg("circle", { "class": "chart-dot", cx: x(last), cy: y(vals[last]), r: 3.5 });
    var marker = svg("line", { "class": "chart-marker", x1: 0, x2: 0, y1: T, y2: H - B });
    s.appendChild(marker);
    s.appendChild(dot);
    var first = svg("text", { "class": "chart-label", x: L, y: H - 3 });
    first.textContent = chart.labels[0];
    var end = svg("text", { "class": "chart-label", x: W - R, y: H - 3, "text-anchor": "end" });
    end.textContent = chart.labels[last];
    s.appendChild(first);
    s.appendChild(end);
    wrap.appendChild(s);

    var meta = el("div", "chart-meta");
    var rest = "от " + chart.min.toFixed(1) + " до " + chart.max.toFixed(1) + " · нажмите на график";
    meta.textContent = rest;
    wrap.appendChild(meta);

    function pick(ev) {
      var box = s.getBoundingClientRect();
      if (!box.width) return;
      var px = (ev.clientX - box.left) / box.width * W;
      var i = Math.max(0, Math.min(vals.length - 1, Math.round((px - L) / (W - L - R) * (vals.length - 1))));
      marker.setAttribute("x1", x(i)); marker.setAttribute("x2", x(i));
      marker.classList.add("is-on");
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(vals[i]));
      meta.textContent = chart.labels[i] + " · " + vals[i].toFixed(1);
    }
    function release() {
      marker.classList.remove("is-on");
      dot.setAttribute("cx", x(last)); dot.setAttribute("cy", y(vals[last]));
      meta.textContent = rest;
    }
    s.addEventListener("pointerdown", pick);
    s.addEventListener("pointermove", function (ev) { if (ev.buttons || ev.pointerType === "touch") pick(ev); });
    s.addEventListener("pointerup", release);
    s.addEventListener("pointerleave", release);
    s.addEventListener("pointercancel", release);
    return wrap;
  }

  function heatmap(days) {
    var cell = 12, gap = 3, weeks = Math.ceil(days.length / 7);
    var s = svg("svg", { "class": "heat", viewBox: "0 0 " + weeks * (cell + gap) + " " + 7 * (cell + gap), role: "img" });
    days.forEach(function (d, i) {
      var tier = d.count === null ? "out" : d.count === 0 ? 0 : d.count === 1 ? 1 : d.count <= 3 ? 2 : 3;
      var r = svg("rect", { "class": "heat-" + tier, x: Math.floor(i / 7) * (cell + gap), y: (i % 7) * (cell + gap),
        width: cell, height: cell, rx: 3 });
      if (d.count !== null) { var t = svg("title"); t.textContent = d.date + ": " + d.count; r.appendChild(t); }
      s.appendChild(r);
    });
    return s;
  }

  function radarChart(axes) {
    var size = 300, c = size / 2, rad = 100, n = axes.length;
    var s = svg("svg", { "class": "radar", viewBox: "0 0 " + size + " " + size, role: "img" });
    function point(i, k) {
      var a = -Math.PI / 2 + 2 * Math.PI * i / n;
      return [c + Math.cos(a) * rad * k, c + Math.sin(a) * rad * k];
    }
    [0.25, 0.5, 0.75, 1].forEach(function (k) {
      s.appendChild(svg("polygon", { "class": "radar-grid",
        points: axes.map(function (_, i) { return point(i, k).join(","); }).join(" ") }));
    });
    axes.forEach(function (ax, i) {
      var p = point(i, 1);
      s.appendChild(svg("line", { "class": "radar-grid", x1: c, y1: c, x2: p[0], y2: p[1] }));
      var lp = point(i, 1.22);
      var t = svg("text", { "class": "radar-label", x: lp[0], y: lp[1] + 4, "text-anchor": "middle" });
      t.textContent = ax.name;
      s.appendChild(t);
    });
    s.appendChild(svg("polygon", { "class": "radar-shape",
      points: axes.map(function (ax, i) { return point(i, Math.max(0.02, ax.value / 100)).join(","); }).join(" ") }));
    return s;
  }

  // ── Клуб (главная) ─────────────────────────────────────────────────────────
  function podium(items) {
    var wrap = el("div", "podium");
    [1, 0, 2].forEach(function (idx) {
      var p = items[idx];
      if (!p) return;
      var col = clickable(el("div", "podium-col podium-" + p.rank), "player/" + p.id);
      var crownSlot = el("div", "crown-slot");
      if (p.champion) crownSlot.appendChild(icon("crown", "crown"));
      col.appendChild(crownSlot);
      col.appendChild(avatar(p, p.rank === 1 ? "xl" : "lg"));
      col.appendChild(el("div", "podium-name", p.name));
      var block = el("div", "podium-block");
      block.appendChild(el("div", "podium-rank", String(p.rank)));
      block.appendChild(num("podium-rating", p.rating));
      col.appendChild(block);
      wrap.appendChild(col);
    });
    return wrap;
  }

  function setChips(sets, winner) {
    var wrap = el("div", "chips");
    sets.forEach(function (sc) {
      var parts = sc.split(":");
      var aWon = parseInt(parts[0], 10) > parseInt(parts[1], 10);
      var won = winner && ((aWon && winner === "a") || (!aWon && winner === "b"));
      wrap.appendChild(el("span", won ? "chip chip-win" : "chip", sc));
    });
    return wrap;
  }

  function versusLine(m) {
    var line = el("div", "vs-line");
    line.appendChild(avatar(m.a, "sm"));
    line.appendChild(el("span", "vs-name" + (m.winner === "a" ? " win" : ""), m.a.name));
    line.appendChild(el("span", "vs-sep", m.winner ? "—" : "ничья"));
    line.appendChild(el("span", "vs-name" + (m.winner === "b" ? " win" : ""), m.b.name));
    line.appendChild(avatar(m.b, "sm"));
    return line;
  }

  function renderHome(c, d) {
    c.appendChild(header("Клуб сегодня", d.today.label));
    if (d.empty) { emptyState(c, d.empty); return; }
    c.appendChild(podium(d.podium));

    var me = clickable(el("div", "card me-card"), "player/" + d.me.id);
    me.appendChild(avatar({ id: d.me.id, initial: d.me.initial }, "md"));
    var mb = el("div", "body");
    mb.appendChild(el("div", "name", "Ты · " + d.me.place));
    if (d.me.week) mb.appendChild(el("div", "sub " + (d.me.week_up ? "tone-win" : "tone-loss"), d.me.week));
    me.appendChild(mb);
    me.appendChild(num("me-rating", d.me.rating));
    c.appendChild(me);

    if (d.featured) {
      var f = el("div", "card feature");
      var top = el("div", "pair-top");
      top.appendChild(el("span", "eyebrow", d.featured.title));
      top.appendChild(el("span", "eyebrow-meta", [d.featured.date, d.featured.boss ? "боссфайт" : ""].filter(Boolean).join(" · ")));
      f.appendChild(top);
      f.appendChild(versusLine(d.featured));
      f.appendChild(setChips(d.featured.sets, d.featured.winner));
      if (d.featured.reason) f.appendChild(el("p", "feature-reason", d.featured.reason));
      c.appendChild(f);
    }

    if (d.records.length) {
      var rs = section("Свежие рекорды");
      d.records.forEach(function (r) {
        var li = el("li", "row");
        li.appendChild(icon("trophy", "ico-lead ico-gold"));
        var b = el("div", "body");
        b.appendChild(el("div", "name", r.title));
        b.appendChild(el("div", "sub", r.name + " · " + r.value + " · " + r.date));
        li.appendChild(b);
        rs.list.appendChild(li);
      });
      c.appendChild(rs);
    }

    if (d.today.matches.length) {
      var ts = section("Ещё сегодня");
      d.today.matches.forEach(function (m) {
        var li = el("li", "row");
        var b = el("div", "body");
        b.appendChild(versusLine(m));
        b.appendChild(setChips(m.sets, m.winner));
        li.appendChild(b);
        ts.list.appendChild(li);
      });
      c.appendChild(ts);
    }

    if (d.links.length) {
      var links = section("Клуб");
      d.links.forEach(function (l) { links.list.appendChild(navRow(l.title, l.value, l.route, l.icon)); });
      c.appendChild(links);
    }
  }

  // ── Рейтинг ────────────────────────────────────────────────────────────────
  function boardRow(row) {
    var li = clickable(el("li", "row" + (row.is_viewer ? " is-viewer" : "")), "player/" + row.player_id);
    li.appendChild(el("div", "rank-num", String(row.rank)));
    li.appendChild(avatar({ id: row.player_id, initial: row.initial }, "md"));
    var body = el("div", "body");
    var nameLine = el("div", "name");
    nameLine.appendChild(document.createTextNode(row.is_viewer ? row.name + ", ты" : row.name));
    if (row.subtitle.indexOf("Чемпион") === 0) nameLine.appendChild(icon("crown", "crown-inline"));
    body.appendChild(nameLine);
    body.appendChild(el("div", "sub", row.subtitle));
    li.appendChild(body);
    var value = el("div", "value");
    value.appendChild(el("div", "rating", row.rating));
    if (row.week_change !== 0) {
      value.appendChild(el("div", "place " + (row.week_change > 0 ? "tone-win" : "tone-loss"),
        (row.week_change > 0 ? "▲ " : "▼ ") + Math.abs(row.week_change)));
    }
    li.appendChild(value);
    return li;
  }

  function renderLeaderboard(c, d) {
    c.appendChild(header(d.title, d.players_label));
    if (d.empty) { emptyState(c, d.empty); return; }
    var board = section();
    board.list.classList.add("board");
    d.rows.forEach(function (row) { board.list.appendChild(boardRow(row)); });
    c.appendChild(board);
    if (d.gap) c.appendChild(el("p", "footnote", d.gap));
  }

  // ── Профиль ────────────────────────────────────────────────────────────────
  var PROFILE_TABS = [["", "Обзор"], ["matches", "Матчи"], ["achievements", "Ачивки"], ["style", "Стиль"]];

  function segmented(playerId, active, counts) {
    var seg = el("div", "segmented");
    PROFILE_TABS.forEach(function (t) {
      var b = el("button", "seg" + (t[0] === active ? " is-active" : ""), t[1]);
      b.type = "button";
      if (t[0] === "style" && counts && !counts.style) b.classList.add("is-dim");
      b.addEventListener("click", function () {
        // Каркас живёт между разделами, поэтому «текущий» берём из адреса,
        // а не из значения при создании переключателя.
        var target = "#/player/" + playerId + (t[0] ? "/" + t[0] : "");
        if (location.hash === target) return;
        haptic("select");
        location.replace(target);
      });
      seg.appendChild(b);
    });
    return seg;
  }

  function profileHero(c, d, playerId) {
    var h = d.head;
    var hero = el("div", "card hero");
    var top = el("div", "hero-top");
    top.appendChild(avatar({ id: +playerId, initial: h.initial }, "xl"));
    var who = el("div", "body");
    who.appendChild(el("div", "hero-name", h.name));
    who.appendChild(el("div", "sub", h.subtitle));
    top.appendChild(who);
    hero.appendChild(top);
    var line = el("div", "hero-rating");
    line.appendChild(num("hero-value", h.rating));
    if (h.week) line.appendChild(el("span", "hero-week " + (h.week_up ? "tone-win" : "tone-loss"), h.week));
    hero.appendChild(line);
    if (d.chart) hero.appendChild(ratingChart(d.chart));
    c.appendChild(hero);
  }

  // Каркас профиля (шапка с графиком, переключатель разделов) строится один раз
  // на игрока; при смене раздела меняется только содержимое под переключателем.
  var shell = null;

  function buildShell(c, d, playerId) {
    profileHero(c, d, playerId);
    var seg = null;
    if (!d.empty) { seg = segmented(playerId, "", d.counts); c.appendChild(seg); }
    var body = el("div", "tab-body");
    c.appendChild(body);
    shell = { playerId: playerId, data: d, body: body, seg: seg };
  }

  function setSegment(tab) {
    if (!shell || !shell.seg) return;
    var tabs = shell.seg.children;
    PROFILE_TABS.forEach(function (t, i) {
      tabs[i].classList.toggle("is-active", t[0] === tab);
    });
  }

  function loadTab(tab, token) {
    var d = shell.data, id = shell.playerId, body = shell.body;
    body.textContent = "";
    if (d.empty) { emptyState(body, d.empty); return; }
    if (tab === "") { renderOverview(body, d, id); return; }
    body.appendChild(skeleton("body"));
    var jobs = tab === "matches" ? [api("player/" + id + "/history")] :
      tab === "achievements" ? [api("player/" + id + "/achievements")] :
        [api("player/" + id + "/radar"), api("player/" + id + "/activity")];
    Promise.all(jobs).then(function (r) {
      if (token !== renderToken) return;
      body.textContent = "";
      if (tab === "matches") renderMatchesTab(body, r[0]);
      else if (tab === "achievements") renderAchievementsTab(body, r[0]);
      else renderStyleTab(body, r[0], r[1]);
    }, function (err) {
      if (token !== renderToken) return;
      body.textContent = "";
      emptyState(body, err.message);
    });
  }

  function renderOverview(c, d, playerId) {
    if (d.empty) { emptyState(c, d.empty); return; }
    var tiles = el("div", "tiles");
    d.tiles.forEach(function (t) {
      var tile = el("div", "tile" + (t.tone ? " tile-" + t.tone : ""));
      tile.appendChild(el("div", "tile-label", t.label));
      tile.appendChild(num("tile-value", t.value));
      if (t.sub) tile.appendChild(el("div", "tile-sub", t.sub));
      if (t.hint) {
        tile.classList.add("tap", "tile-hinted");
        tile.addEventListener("click", function () { sheetOpen(null, t.label, t.hint, ""); });
      }
      tiles.appendChild(tile);
    });
    c.appendChild(tiles);

    if (d.last10.length) {
      var f = el("div", "card last10");
      var head = el("div", "pair-top");
      head.appendChild(el("span", "eyebrow", "Последние матчи"));
      var wins = d.last10.filter(function (x) { return x === "w"; }).length;
      head.appendChild(el("span", "eyebrow-meta", wins + " из " + d.last10.length + " — победы"));
      f.appendChild(head);
      f.appendChild(formStrip(d.last10, "strip-wide"));
      c.appendChild(f);
    }

    if (d.versus) {
      var btn = el("button", "button");
      btn.type = "button";
      btn.appendChild(icon("swords", "ico-btn"));
      btn.appendChild(document.createTextNode(d.versus));
      btn.addEventListener("click", function () { go("h2h/" + playerId); });
      c.appendChild(btn);
    }

    if (d.goals.length) {
      var g = section("Цели");
      d.goals.forEach(function (it) { g.list.appendChild(itemRow(it)); });
      c.appendChild(g);
    }
    d.blocks.forEach(function (b) {
      var s = section(b.title);
      b.items.forEach(function (it) { s.list.appendChild(itemRow(it)); });
      c.appendChild(s);
    });
  }

  function renderMatchesTab(body, d) {
    if (d.empty) { emptyState(body, d.empty); return; }
    var s = section();
    d.matches.forEach(function (m) { s.list.appendChild(matchRow(m)); });
    body.appendChild(s);
  }

  function badgeClass(a) {
    if (!a.earned) return a.hidden ? "badge-hidden" : "badge-locked";
    return a.tier ? "badge-" + a.tier : "badge-earned";
  }

  function sheetOpen(badgeNode, title, text, meta) {
    var old = document.querySelector(".sheet-wrap");
    if (old) old.remove();
    var wrap = el("div", "sheet-wrap");
    var sheet = el("div", "sheet");
    if (badgeNode) sheet.appendChild(badgeNode);
    sheet.appendChild(el("div", "sheet-title", title));
    sheet.appendChild(el("div", "sheet-text", text));
    if (meta) sheet.appendChild(el("div", "sheet-meta", meta));
    wrap.appendChild(sheet);
    wrap.addEventListener("click", function () { wrap.remove(); });
    document.body.appendChild(wrap);
    haptic("impact");
  }

  function achievementSheet(a) {
    var badge = el("div", "badge badge-lg " + badgeClass(a));
    if (a.hidden) badge.appendChild(el("span", "badge-q", "?"));
    else badge.appendChild(useIcon(a.icon || "trophy"));
    var tierWord = { bronze: "бронза", silver: "серебро", gold: "золото" }[a.tier];
    sheetOpen(badge, a.name, a.desc,
      a.earned ? (tierWord ? "Получено · " + tierWord : "Получено") : "Ещё не получено");
  }

  function renderAchievementsTab(body, d) {
    body.appendChild(el("p", "footnote footnote-top", "Получено " + d.count + ". Нажмите на значок, чтобы прочитать условие."));
    d.categories.forEach(function (cat, ci) {
      var wrap = el("section", "group");
      var t = el("div", "group-head");
      t.appendChild(el("h2", "group-title", cat.title));
      t.appendChild(el("span", "group-count", cat.value));
      wrap.appendChild(t);
      var grid = el("div", "badges");
      function badgeCell(a) {
        var cell = el("button", "badge-cell");
        cell.type = "button";
        var badge = el("div", "badge " + badgeClass(a));
        if (a.hidden) badge.appendChild(el("span", "badge-q", "?"));
        else badge.appendChild(useIcon(a.icon || "trophy"));
        cell.appendChild(badge);
        cell.appendChild(el("span", "badge-name", a.name));
        cell.addEventListener("click", function () { achievementSheet(a); });
        return cell;
      }
      // Полученные — все, закрытых показываем ближайшие три, остальное и скрытые
      // сворачиваем: стена из замков ничего не говорит.
      var earned = cat.items.filter(function (a) { return a.earned; });
      var open = cat.items.filter(function (a) { return !a.earned && !a.hidden; });
      var hidden = cat.items.filter(function (a) { return !a.earned && a.hidden; });
      var shown = earned.concat(open.slice(0, 3));
      var folded = open.slice(3).concat(hidden);
      shown.forEach(function (a) { grid.appendChild(badgeCell(a)); });
      wrap.appendChild(grid);
      if (folded.length) {
        var more = el("button", "more", "Показать остальные (" + folded.length + ")");
        more.type = "button";
        more.addEventListener("click", function () {
          folded.forEach(function (a) { grid.appendChild(badgeCell(a)); });
          more.remove();
        });
        wrap.appendChild(more);
      }
      body.appendChild(wrap);
    });
  }

  function legend(activity) {
    var lg = el("div", "legend");
    activity.legend.forEach(function (label, i) {
      var item = el("span", "legend-item");
      item.appendChild(el("i", "swatch heat-" + i));
      item.appendChild(document.createTextNode(label));
      lg.appendChild(item);
    });
    lg.appendChild(el("span", "legend-total", activity.total));
    return lg;
  }

  function renderStyleTab(body, radar, activity) {
    if (radar.empty) {
      emptyState(body, radar.empty);
    } else {
      var card = el("div", "card");
      card.appendChild(radarChart(radar.axes));
      if (radar.archetype) {
        card.appendChild(el("div", "eyebrow", "Архетип"));
        card.appendChild(el("div", "hero-name", radar.archetype));
        if (radar.description) card.appendChild(el("p", "feature-reason", radar.description));
        if (radar.hint) card.appendChild(el("p", "feature-reason", radar.hint));
      }
      body.appendChild(card);
      var axes = section("Оси стиля");
      radar.axes.forEach(function (ax) {
        axes.list.appendChild(itemRow({ label: ax.name, value: ax.value + "%", note: ax.hint, progress: ax.value / 100 }));
      });
      body.appendChild(axes);
      if (radar.narrative) body.appendChild(el("p", "footnote", radar.narrative));
    }
    var act = section("Активность · " + activity.period);
    var li = el("li", "row row-chart");
    li.appendChild(heatmap(activity.days));
    act.list.appendChild(li);
    body.appendChild(act);
    body.appendChild(legend(activity));
  }

  // ── Матчи, «ты против него» ────────────────────────────────────────────────
  var RESULT_WORDS = { w: "Победа", l: "Поражение", d: "Ничья" };

  function matchRow(m, sameOpponent) {
    var li = clickable(el("li", "row match-row result-" + m.result), "player/" + m.opponent_id);
    var body = el("div", "body");
    body.appendChild(el("div", "name", sameOpponent ? RESULT_WORDS[m.result] : m.opponent));
    body.appendChild(el("div", "sub", [m.date, m.score, m.boss ? "боссфайт" : ""].filter(Boolean).join(" · ")));
    li.appendChild(body);
    if (m.delta) li.appendChild(el("div", "delta " + (m.delta.charAt(0) === "+" ? "tone-win" : "tone-loss"), m.delta));
    return li;
  }

  function renderVersus(c, d) {
    var card = el("div", "card versus");
    var row = el("div", "versus-row");
    var me = el("div", "versus-side");
    me.appendChild(avatar(d.me, "lg"));
    me.appendChild(el("div", "versus-name", "Ты"));
    var them = clickable(el("div", "versus-side"), "player/" + d.them.id);
    them.appendChild(avatar(d.them, "lg"));
    them.appendChild(el("div", "versus-name", d.them.name));
    var score = el("div", "versus-score");
    if (d.score) {
      score.appendChild(num("versus-num" + (d.score.me > d.score.them ? " tone-win" : ""), String(d.score.me)));
      score.appendChild(el("span", "versus-colon", ":"));
      score.appendChild(num("versus-num" + (d.score.them > d.score.me ? " tone-loss" : ""), String(d.score.them)));
    } else {
      score.appendChild(el("span", "versus-colon", "—"));
    }
    row.appendChild(me);
    row.appendChild(score);
    row.appendChild(them);
    card.appendChild(row);
    if (d.score && d.score.draws) card.appendChild(el("div", "sub center", "ничьих: " + d.score.draws));
    c.appendChild(card);

    var fc = el("div", "card forecast");
    var ft = el("div", "pair-top");
    ft.appendChild(el("span", "eyebrow", "Шанс победы сейчас"));
    ft.appendChild(num("forecast-num", d.forecast.chance + "%"));
    fc.appendChild(ft);
    fc.appendChild(progressBar(d.forecast.chance / 100));
    var wl = el("div", "forecast-split");
    wl.appendChild(el("span", "tone-win", "Победа " + d.forecast.win));
    wl.appendChild(el("span", "tone-loss", "Поражение " + d.forecast.lose));
    fc.appendChild(wl);
    c.appendChild(fc);

    if (d.axes.length) {
      var ax = section("Стиль: ты и " + d.them.name);
      var key = el("li", "row axis-key");
      var k1 = el("span", "legend-item");
      k1.appendChild(el("i", "swatch swatch-me"));
      k1.appendChild(document.createTextNode("Ты"));
      var k2 = el("span", "legend-item");
      k2.appendChild(el("i", "swatch swatch-them"));
      k2.appendChild(document.createTextNode(d.them.name));
      key.appendChild(k1);
      key.appendChild(k2);
      ax.list.appendChild(key);
      d.axes.forEach(function (a) {
        var li = el("li", "row axis-row");
        li.appendChild(el("div", "axis-name", a.name));
        var bars = el("div", "axis-bars");
        var left = el("div", "axis-half axis-me");
        var lf = el("i"); lf.style.width = a.me + "%"; left.appendChild(lf);
        var right = el("div", "axis-half axis-them");
        var rf = el("i"); rf.style.width = a.them + "%"; right.appendChild(rf);
        bars.appendChild(el("span", "axis-num", String(a.me)));
        bars.appendChild(left);
        bars.appendChild(right);
        bars.appendChild(el("span", "axis-num", String(a.them)));
        li.appendChild(bars);
        ax.list.appendChild(li);
      });
      c.appendChild(ax);
    }

    if (d.empty) { emptyState(c, d.empty); return; }
    var m = section("Итог встреч");
    d.metrics.forEach(function (it) { m.list.appendChild(itemRow(it)); });
    c.appendChild(m);
    var s = section("Все встречи");
    d.matches.forEach(function (x) { s.list.appendChild(matchRow(x, true)); });
    c.appendChild(s);
  }

  // ── Разделы клуба ──────────────────────────────────────────────────────────
  function clubMatchRow(m) {
    var li = el("li", "row");
    var body = el("div", "body");
    var names = el("div", "name name-pair");
    names.appendChild(el("span", m.winner === "a" ? "win" : "", m.a));
    names.appendChild(document.createTextNode(" — "));
    names.appendChild(el("span", m.winner === "b" ? "win" : "", m.b));
    body.appendChild(names);
    body.appendChild(el("div", "sub", [m.date, m.score, m.winner ? "" : "ничья"].filter(Boolean).join(" · ")));
    li.appendChild(body);
    return li;
  }

  function renderClubMatches(c, d) {
    c.appendChild(header(d.title, d.subtitle));
    if (d.empty) { emptyState(c, d.empty); return; }
    var s = section();
    d.matches.forEach(function (m) { s.list.appendChild(clubMatchRow(m)); });
    c.appendChild(s);
    var next = d.next_offset;
    if (next !== null) {
      var btn = el("button", "button button-quiet", "Показать ещё");
      btn.type = "button";
      btn.addEventListener("click", function () {
        btn.disabled = true;
        api("matches?offset=" + next).then(function (more) {
          more.matches.forEach(function (m) { s.list.appendChild(clubMatchRow(m)); });
          next = more.next_offset;
          btn.disabled = false;
          if (next === null) btn.remove();
        }, function () { btn.disabled = false; });
      });
      c.appendChild(btn);
    }
  }

  function renderRecords(c, d) {
    c.appendChild(header(d.title));
    if (d.empty) { emptyState(c, d.empty); return; }
    d.categories.forEach(function (cat) {
      var s = section(cat.title);
      cat.items.forEach(function (it) { s.list.appendChild(itemRow(it)); });
      c.appendChild(s);
    });
  }

  function renderClubActivity(c, d) {
    c.appendChild(header(d.title, d.period + " · " + d.total));
    var s = section();
    var li = el("li", "row row-chart");
    li.appendChild(heatmap(d.days));
    s.list.appendChild(li);
    c.appendChild(s);
    c.appendChild(legend(d));
  }

  function renderThrone(c, d) {
    c.appendChild(header(d.title));
    if (d.empty) { emptyState(c, d.empty); return; }
    if (d.current) {
      var card = clickable(el("div", "card throne-card"), "player/" + d.current.player_id);
      card.appendChild(icon("crown", "crown crown-big"));
      card.appendChild(avatar({ id: d.current.player_id, initial: d.current.name.charAt(0).toUpperCase() }, "xl"));
      card.appendChild(el("div", "hero-name", d.current.name));
      card.appendChild(el("div", "sub", "на троне " + d.current.duration + " · с " + d.current.since));
      c.appendChild(card);
    }
    var facts = section();
    d.facts.forEach(function (f) { facts.list.appendChild(itemRow(f)); });
    c.appendChild(facts);
    if (d.reigns.length) {
      var s = section("Прошлые правления");
      d.reigns.forEach(function (r) {
        var li = clickable(el("li", "row"), "player/" + r.player_id);
        li.appendChild(icon("crown", "ico-lead ico-gold"));
        var body = el("div", "body");
        body.appendChild(el("div", "name", r.name));
        body.appendChild(el("div", "sub", r.period + " · " + r.duration));
        if (r.note) body.appendChild(el("div", "sub", r.note));
        li.appendChild(body);
        s.list.appendChild(li);
      });
      c.appendChild(s);
    }
  }

  // ── Маршруты ───────────────────────────────────────────────────────────────
  // [шаблон адреса, вкладка снизу, загрузка данных, отрисовка]
  function one(path) { return function () { return api(path); }; }

  var PLAYER_ROUTE = /^player\/(\d+)(?:\/(matches|achievements|style))?$/;

  var routes = [
    [/^$/, "home", one("home"), renderHome, "home"],
    [/^rating$/, "rating", one("leaderboard"), renderLeaderboard, "rating"],
    [/^h2h\/(\d+)$/, null, function (m) { return api("h2h/" + m[1]); }, function (c, d) { renderVersus(c, d); }, "h2h"],
    [/^records$/, "home", one("records"), renderRecords, "records"],
    [/^matches$/, "home", one("matches"), renderClubMatches, "matches"],
    [/^activity\/club$/, "home", one("activity/club"), renderClubActivity, "activity"],
    [/^throne$/, "home", one("throne"), renderThrone, "throne"]
  ];

  var TABS = [["home", "", "Клуб", "home"], ["rating", "rating", "Рейтинг", "rating"], ["me", "me", "Я", "user"]];

  function drawTabbar(active) {
    tabbar.textContent = "";
    TABS.forEach(function (t) {
      var b = el("button", "tab" + (t[0] === active ? " is-active" : ""));
      b.type = "button";
      b.appendChild(icon(t[3]));
      b.appendChild(el("span", "tab-label", t[2]));
      b.addEventListener("click", function () {
        haptic("select");
        var target = t[1] === "me" ? "player/" + viewerId : t[1];
        if (currentRoute() === target) { window.scrollTo({ top: 0, behavior: "smooth" }); return; }
        location.hash = "#/" + target;
      });
      tabbar.appendChild(b);
    });
  }

  function currentRoute() { return location.hash.replace(/^#\/?/, ""); }

  // Позиция прокрутки по адресу: возврат «назад» попадает на то же место
  var scrollPos = {};
  var renderedRoute = null;

  function finishRender(route, keepScroll) {
    renderedRoute = route;
    track(route);
    if (keepScroll) return;
    window.scrollTo(0, scrollPos[route] || 0);
  }

  function render() {
    var route = currentRoute();
    if (renderedRoute !== null && renderedRoute !== route) scrollPos[renderedRoute] = window.scrollY;
    if (route === "me") { location.replace("#/player/" + viewerId); return; }

    var token = ++renderToken;
    var pm = route.match(PLAYER_ROUTE);

    if (pm) {
      var playerId = pm[1], tab = pm[2] || "";
      drawTabbar(playerId === String(viewerId) ? "me" : null);
      var isOwn = route === "player/" + viewerId;
      if (tg && tg.BackButton) { if (isOwn) tg.BackButton.hide(); else tg.BackButton.show(); }
      if (shell && shell.playerId === playerId && app.contains(shell.body)) {
        // тот же игрок — переключаем только раздел, шапку не трогаем
        setSegment(tab);
        loadTab(tab, token);
        renderedRoute = route;
        track(route);
        return;
      }
      shell = null;
      app.textContent = "";
      app.appendChild(skeleton("profile"));
      api("player/" + playerId).then(function (d) {
        if (token !== renderToken) return;
        app.textContent = "";
        buildShell(app, d, playerId);
        setSegment(tab);
        loadTab(tab, token);
        finishRender(route, false);
      }, function (err) {
        if (token !== renderToken) return;
        app.textContent = "";
        emptyState(app, err.message);
      });
      return;
    }

    var match = null, entry = null;
    for (var i = 0; i < routes.length && !match; i++) { match = route.match(routes[i][0]); entry = routes[i]; }
    if (!match) { location.replace("#/"); return; }

    shell = null;
    var tabName = entry[1];
    drawTabbar(tabName);
    var isRoot = route === "" || route === "rating";
    if (tg && tg.BackButton) { if (isRoot) tg.BackButton.hide(); else tg.BackButton.show(); }

    app.textContent = "";
    app.appendChild(skeleton("list"));
    entry[2](match).then(function (data) {
      if (token !== renderToken) return;
      app.textContent = "";
      entry[3](app, data, match);
      finishRender(route, false);
    }, function (err) {
      if (token !== renderToken) return;
      app.textContent = "";
      emptyState(app, err.message);
    });
  }

  applyTheme();
  if (tg) {
    tg.ready();
    tg.expand();
    tg.onEvent("themeChanged", function () { applyTheme(); shell = null; render(); });
    if (tg.BackButton) tg.BackButton.onClick(function () { history.back(); });
  }
  window.addEventListener("hashchange", render);
  api("me").then(function (d) { viewerId = d.id; }, function () { viewerId = null; }).then(render);
})();
