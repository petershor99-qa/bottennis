// Mini App клуба (v2.160.0 — «Рейтинг клуба», v2.161.0 — остальные экраны
// «только чтение» и переходы между ними). Одна страница, экраны по адресу
// после «#» (#/player/12, #/records …); «Назад» — кнопка Telegram.
// Все тексты приходят с сервера (bot/webapp/api.py), здесь только разметка.
// Данные вставляются через textContent — имена не интерпретируются как HTML.
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  var root = document.documentElement;
  var app = document.getElementById("app");
  var DARK_BG = "#0A0F1F";
  var SVG = "http://www.w3.org/2000/svg";
  var renderToken = 0;

  // ── Тема ───────────────────────────────────────────────────────────────────
  function isDark() { return root.getAttribute("data-theme") === "dark"; }

  function applyTheme() {
    var dark = tg && tg.colorScheme === "dark";
    root.setAttribute("data-theme", dark ? "dark" : "light");
    if (!tg) return;
    try {
      var bg = dark ? DARK_BG : (tg.themeParams.secondary_bg_color || "#EFEFF4");
      tg.setHeaderColor(bg);
      tg.setBackgroundColor(bg);
    } catch (e) { /* старые клиенты Telegram без этих методов */ }
  }

  // ── Мелкие помощники разметки ──────────────────────────────────────────────
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

  function chevron() {
    var s = svg("svg", { "class": "chev", viewBox: "0 0 8 14", "aria-hidden": "true" });
    s.appendChild(svg("path", { d: "M1 1l6 6-6 6", fill: "none", "stroke-width": "1.6",
      "stroke-linecap": "round", "stroke-linejoin": "round" }));
    return s;
  }

  function icon(kind) {
    // Линейные значки: «получено» (галочка в круге), «закрыто» (замок)
    var s = svg("svg", { "class": "ico ico-" + kind, viewBox: "0 0 24 24", "aria-hidden": "true" });
    var p = { fill: "none", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round" };
    if (kind === "done") {
      s.appendChild(svg("circle", Object.assign({ cx: 12, cy: 12, r: 9 }, p)));
      s.appendChild(svg("path", Object.assign({ d: "M8 12.5l2.8 2.8L16 9.8" }, p)));
    } else {
      s.appendChild(svg("rect", Object.assign({ x: 5.5, y: 10.5, width: 13, height: 9, rx: 2 }, p)));
      s.appendChild(svg("path", Object.assign({ d: "M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" }, p)));
    }
    return s;
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

  function go(route) { location.hash = "#/" + route; }

  function navRow(title, value, route, sub) {
    var li = el("li", "row row-nav");
    li.setAttribute("role", "button");
    li.tabIndex = 0;
    var body = el("div", "body");
    body.appendChild(el("div", "name", title));
    if (sub) body.appendChild(el("div", "sub", sub));
    li.appendChild(body);
    if (value) li.appendChild(el("div", "meta", value));
    li.appendChild(chevron());
    li.addEventListener("click", function () { go(route); });
    li.addEventListener("keydown", function (e) { if (e.key === "Enter") go(route); });
    return li;
  }

  function formDots(marks) {
    var wrap = el("span", "form");
    marks.forEach(function (m) { wrap.appendChild(el("i", "dot dot-" + m)); });
    return wrap;
  }

  // Элемент из строк бота: короткая подпись — в две колонки, длинная — стопкой
  function itemRow(item) {
    var li = el("li", "row");
    if (item.form) {
      li.classList.add("row-pair");
      li.appendChild(el("div", "label", item.label));
      var v = el("div", "value");
      v.appendChild(formDots(item.form));
      li.appendChild(v);
    } else if (item.label && item.value !== undefined && item.label.length <= 24 && item.value.length <= 28) {
      li.classList.add("row-pair");
      li.appendChild(el("div", "label", item.label));
      li.appendChild(el("div", "value", item.value));
    } else {
      var body = el("div", "body");
      if (item.label) {
        body.appendChild(el("div", "name", item.label));
        body.appendChild(el("div", "sub", item.value));
      } else {
        body.appendChild(el("div", "text", item.text));
      }
      if (item.note) body.appendChild(el("div", "sub", item.note));
      if (item.progress !== undefined) {
        var bar = el("div", "progress");
        var fill = el("i");
        fill.style.width = Math.round(item.progress * 100) + "%";
        bar.appendChild(fill);
        body.appendChild(bar);
      }
      li.appendChild(body);
      return li;
    }
    if (item.note) li.appendChild(el("div", "sub sub-wide", item.note));
    return li;
  }

  function groupsBlock(container, groups) {
    (groups || []).forEach(function (g) {
      var s = section();
      g.forEach(function (it) { s.list.appendChild(itemRow(it)); });
      container.appendChild(s);
    });
  }

  function emptyState(container, text) {
    container.appendChild(el("p", "state", text));
  }

  // ── Загрузка данных ────────────────────────────────────────────────────────
  function api(path) {
    var initData = tg && tg.initData;
    if (!initData) return Promise.reject(new Error("Откройте приложение из бота."));
    return fetch("api/" + path, { headers: { "Authorization": "tma " + initData } })
      .then(function (resp) {
        return resp.json().then(function (body) {
          if (!resp.ok) throw new Error(body.error || "Не удалось загрузить данные.");
          return body;
        });
      }, function () { throw new Error("Нет связи с сервером. Попробуйте ещё раз."); });
  }

  // ── Графики ────────────────────────────────────────────────────────────────
  function ratingChart(chart) {
    var W = 320, H = 150, L = 6, R = 6, T = 10, B = 22;
    var vals = chart.values;
    var lo = Math.min.apply(null, vals.concat([chart.reference])) - 10;
    var hi = Math.max.apply(null, vals.concat([chart.reference])) + 10;
    function x(i) { return L + (W - L - R) * (vals.length === 1 ? 0.5 : i / (vals.length - 1)); }
    function y(v) { return T + (H - T - B) * (1 - (v - lo) / (hi - lo)); }
    var s = svg("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img" });
    var ref = y(chart.reference);
    s.appendChild(svg("line", { "class": "chart-ref", x1: L, x2: W - R, y1: ref, y2: ref }));
    var pts = vals.map(function (v, i) { return x(i).toFixed(1) + "," + y(v).toFixed(1); }).join(" ");
    s.appendChild(svg("polyline", { "class": "chart-line", points: pts }));
    var last = vals.length - 1;
    s.appendChild(svg("circle", { "class": "chart-dot", cx: x(last), cy: y(vals[last]), r: 3 }));
    var first = svg("text", { "class": "chart-label", x: L, y: H - 6 });
    first.textContent = chart.labels[0];
    var end = svg("text", { "class": "chart-label", x: W - R, y: H - 6, "text-anchor": "end" });
    end.textContent = chart.labels[last];
    var refLabel = svg("text", { "class": "chart-label", x: W - R, y: ref - 4, "text-anchor": "end" });
    refLabel.textContent = String(chart.reference.toFixed(0));
    s.appendChild(first); s.appendChild(end); s.appendChild(refLabel);
    return s;
  }

  function heatmap(days) {
    var cell = 12, gap = 3, weeks = Math.ceil(days.length / 7);
    var W = weeks * (cell + gap), H = 7 * (cell + gap);
    var s = svg("svg", { "class": "heat", viewBox: "0 0 " + W + " " + H, role: "img" });
    days.forEach(function (d, i) {
      var col = Math.floor(i / 7), row = i % 7;
      var tier = d.count === null ? "out" : d.count === 0 ? 0 : d.count === 1 ? 1 : d.count <= 3 ? 2 : 3;
      var r = svg("rect", { "class": "heat-" + tier, x: col * (cell + gap), y: row * (cell + gap),
        width: cell, height: cell, rx: 3 });
      if (d.count !== null) {
        var t = svg("title"); t.textContent = d.date + ": " + d.count; r.appendChild(t);
      }
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
      var pts = axes.map(function (_, i) { return point(i, k).join(","); }).join(" ");
      s.appendChild(svg("polygon", { "class": "radar-grid", points: pts }));
    });
    axes.forEach(function (ax, i) {
      var p = point(i, 1);
      s.appendChild(svg("line", { "class": "radar-grid", x1: c, y1: c, x2: p[0], y2: p[1] }));
      var lp = point(i, 1.22);
      var t = svg("text", { "class": "radar-label", x: lp[0], y: lp[1] + 4, "text-anchor": "middle" });
      t.textContent = ax.name;
      s.appendChild(t);
    });
    var shape = axes.map(function (ax, i) { return point(i, Math.max(0.02, ax.value / 100)).join(","); }).join(" ");
    s.appendChild(svg("polygon", { "class": "radar-shape", points: shape }));
    return s;
  }

  // ── Экраны ─────────────────────────────────────────────────────────────────
  function boardRow(row, dark) {
    var li = el("li", "row row-nav" + (row.is_viewer ? " is-viewer" : ""));
    li.appendChild(el("div", "lead", dark ? String(row.rank) : row.initial));
    var body = el("div", "body");
    body.appendChild(el("div", "name", row.is_viewer ? row.name + ", ты" : row.name));
    body.appendChild(el("div", "sub", row.subtitle));
    li.appendChild(body);
    var value = el("div", "value");
    value.appendChild(el("div", "rating", row.rating));
    var place = el("div", "place");
    if (dark) {
      if (row.week_label) place.appendChild(el("span", row.week_change > 0 ? "up" : "", row.week_label));
    } else {
      place.appendChild(document.createTextNode("#" + row.rank));
      if (row.week_change !== 0) {
        place.appendChild(document.createTextNode(" · "));
        var sign = row.week_change > 0 ? "+" : "−";
        place.appendChild(el("span", row.week_change > 0 ? "up" : "", sign + Math.abs(row.week_change)));
      }
    }
    value.appendChild(place);
    li.appendChild(value);
    li.addEventListener("click", function () { go("player/" + row.player_id); });
    return li;
  }

  function renderLeaderboard(c, d) {
    var dark = isDark();
    c.appendChild(header(d.title, dark ? d.players_label : ""));
    if (d.empty) { emptyState(c, d.empty); return; }
    var board = section();
    board.list.classList.add("board");
    d.rows.forEach(function (row) { board.list.appendChild(boardRow(row, dark)); });
    c.appendChild(board);
    if (d.gap) c.appendChild(el("p", "footnote", d.gap));
    var links = section(dark ? "Разделы" : "");
    d.links.forEach(function (l) { links.list.appendChild(navRow(l.title, l.value, l.route)); });
    c.appendChild(links);
  }

  function renderPlayer(c, d) {
    var dark = isDark();
    var h = d.head;
    if (dark) {
      c.appendChild(header(h.name));
      var hero = el("div", "hero");
      hero.appendChild(el("div", "hero-label", "Рейтинг"));
      hero.appendChild(el("div", "hero-value", h.rating));
      hero.appendChild(el("div", "sub", [h.subtitle, h.week].filter(Boolean).join(" · ")));
      c.appendChild(hero);
    } else {
      c.appendChild(header(h.personal ? "Мой профиль" : h.name));
      var card = section();
      var li = el("li", "row row-hero");
      li.appendChild(el("div", "lead lead-lg", h.initial));
      var body = el("div", "body");
      body.appendChild(el("div", "name", h.name));
      body.appendChild(el("div", "sub", h.subtitle));
      if (h.week) body.appendChild(el("div", "sub", h.week));
      li.appendChild(body);
      li.appendChild(el("div", "rating rating-lg", h.rating));
      card.list.appendChild(li);
      c.appendChild(card);
    }
    if (d.empty) { emptyState(c, d.empty); return; }
    var metrics = section("Показатели");
    d.metrics.forEach(function (m) { metrics.list.appendChild(itemRow(m)); });
    c.appendChild(metrics);
    groupsBlock(c, d.groups);
    if (d.chart) {
      var ch = section("Динамика рейтинга");
      var li2 = el("li", "row row-chart");
      li2.appendChild(ratingChart(d.chart));
      ch.list.appendChild(li2);
      c.appendChild(ch);
    }
    var sec = section("Разделы");
    d.sections.forEach(function (s) { sec.list.appendChild(navRow(s.title, s.value, s.route)); });
    c.appendChild(sec);
  }

  function renderSimpleGroups(c, d) {
    c.appendChild(header(d.title, d.subtitle));
    groupsBlock(c, d.groups);
  }

  function renderAchievements(c, d, playerId) {
    c.appendChild(header(d.title, d.subtitle + " · " + d.count));
    var s = section("Категории");
    d.categories.forEach(function (cat) {
      s.list.appendChild(navRow(cat.title, cat.value, "player/" + playerId + "/achievements/" + cat.index));
    });
    c.appendChild(s);
  }

  function renderAchievementCategory(c, d) {
    c.appendChild(header(d.title, d.subtitle + " · " + d.count));
    var s = section();
    d.items.forEach(function (a) {
      var li = el("li", "row" + (a.earned ? " is-earned" : " is-locked"));
      li.appendChild(icon(a.earned ? "done" : "lock"));
      var body = el("div", "body");
      body.appendChild(el("div", "name", a.name));
      body.appendChild(el("div", "sub", a.desc));
      li.appendChild(body);
      s.list.appendChild(li);
    });
    c.appendChild(s);
  }

  function renderRadar(c, d) {
    c.appendChild(header(d.title, d.subtitle));
    if (d.empty) { emptyState(c, d.empty); return; }
    var s = section();
    var li = el("li", "row row-chart");
    li.appendChild(radarChart(d.axes));
    s.list.appendChild(li);
    c.appendChild(s);
    if (d.archetype) {
      var a = section("Архетип");
      var body = el("li", "row");
      var b = el("div", "body");
      b.appendChild(el("div", "name", d.archetype));
      if (d.description) b.appendChild(el("div", "sub", d.description));
      if (d.hint) b.appendChild(el("div", "sub", d.hint));
      body.appendChild(b);
      a.list.appendChild(body);
      c.appendChild(a);
    }
    if (d.narrative) c.appendChild(el("p", "footnote", d.narrative));
    var axes = section("Оси");
    d.axes.forEach(function (ax) {
      axes.list.appendChild(itemRow({ label: ax.name + " · " + ax.value + "%", value: ax.hint }));
    });
    c.appendChild(axes);
  }

  function renderActivity(c, d) {
    c.appendChild(header(d.title, d.subtitle + d.period + " · " + d.total));
    var s = section();
    var li = el("li", "row row-chart");
    li.appendChild(heatmap(d.days));
    s.list.appendChild(li);
    c.appendChild(s);
    var legend = el("div", "legend");
    d.legend.forEach(function (label, i) {
      var item = el("span", "legend-item");
      item.appendChild(el("i", "swatch heat-" + i));
      item.appendChild(document.createTextNode(label));
      legend.appendChild(item);
    });
    c.appendChild(legend);
    c.appendChild(el("p", "footnote", "Строки сверху вниз — понедельник … воскресенье, столбцы — недели слева направо."));
  }

  var RESULT_WORDS = { w: "Победа", l: "Поражение", d: "Ничья" };

  function matchRow(m, sameOpponent) {
    var li = el("li", "row row-nav");
    li.appendChild(el("i", "dot dot-" + m.result));
    var body = el("div", "body");
    // В личных встречах соперник один и тот же — вместо имени исход матча
    body.appendChild(el("div", "name", sameOpponent ? RESULT_WORDS[m.result] : m.opponent));
    body.appendChild(el("div", "sub", [m.date, m.score, m.boss ? "боссфайт" : ""].filter(Boolean).join(" · ")));
    li.appendChild(body);
    if (m.delta) li.appendChild(el("div", "meta" + (m.delta.charAt(0) === "+" ? " up" : ""), m.delta));
    li.appendChild(chevron());
    li.addEventListener("click", function () { go("player/" + m.opponent_id); });
    return li;
  }

  function renderHistory(c, d) {
    c.appendChild(header(d.title, d.subtitle));
    if (d.empty) { emptyState(c, d.empty); return; }
    var s = section();
    d.matches.forEach(function (m) { s.list.appendChild(matchRow(m)); });
    c.appendChild(s);
  }

  function renderH2H(c, d) {
    c.appendChild(header(d.title, d.subtitle));
    if (d.empty) { emptyState(c, d.empty); return; }
    var m = section("Итог");
    d.metrics.forEach(function (it) { m.list.appendChild(itemRow(it)); });
    c.appendChild(m);
    var s = section("Все встречи");
    d.matches.forEach(function (x) { s.list.appendChild(matchRow(x, true)); });
    c.appendChild(s);
  }

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
      var btn = el("button", "button", "Показать ещё");
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
    var s = section("Разделы");
    d.categories.forEach(function (cat) {
      s.list.appendChild(navRow(cat.title, String(cat.count), "records/" + cat.key));
    });
    c.appendChild(s);
  }

  function renderRecordCategory(c, d, key) {
    var cat = (d.categories || []).filter(function (x) { return x.key === key; })[0];
    c.appendChild(header(cat ? cat.title : d.title, cat ? "Рекорды клуба" : ""));
    if (!cat) { emptyState(c, "Пока здесь пусто."); return; }
    var s = section();
    cat.items.forEach(function (it) { s.list.appendChild(itemRow(it)); });
    c.appendChild(s);
  }

  function renderThrone(c, d) {
    c.appendChild(header(d.title));
    if (d.empty) { emptyState(c, d.empty); return; }
    if (d.current) {
      var cur = section("Сейчас на троне");
      cur.list.appendChild(navRow(d.current.name, d.current.duration, "player/" + d.current.player_id,
        "с " + d.current.since));
      c.appendChild(cur);
    }
    var facts = section();
    d.facts.forEach(function (f) { facts.list.appendChild(itemRow(f)); });
    c.appendChild(facts);
    if (d.reigns.length) {
      var s = section("Правления");
      d.reigns.forEach(function (r) {
        var li = el("li", "row");
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
  var routes = [
    [/^$/, "leaderboard", renderLeaderboard],
    [/^player\/(\d+)$/, "player/$1", renderPlayer],
    [/^player\/(\d+)\/stats\/(\w+)$/, "player/$1/stats/$2", renderSimpleGroups],
    [/^player\/(\d+)\/achievements$/, "player/$1/achievements", renderAchievements],
    [/^player\/(\d+)\/achievements\/(\d+)$/, "player/$1/achievements/$2", renderAchievementCategory],
    [/^player\/(\d+)\/radar$/, "player/$1/radar", renderRadar],
    [/^player\/(\d+)\/activity$/, "player/$1/activity", renderActivity],
    [/^player\/(\d+)\/history$/, "player/$1/history", renderHistory],
    [/^h2h\/(\d+)$/, "h2h/$1", renderH2H],
    [/^activity\/club$/, "activity/club", renderActivity],
    [/^records$/, "records", renderRecords],
    [/^records\/(\w+)$/, "records", renderRecordCategory],
    [/^matches$/, "matches", renderClubMatches],
    [/^throne$/, "throne", renderThrone]
  ];

  var lastRender = null;

  function currentRoute() { return location.hash.replace(/^#\/?/, ""); }

  function render() {
    var route = currentRoute();
    var match = null, entry = null;
    for (var i = 0; i < routes.length && !match; i++) {
      match = route.match(routes[i][0]);
      entry = routes[i];
    }
    if (!match) { go(""); return; }
    if (tg && tg.BackButton) { if (route) tg.BackButton.show(); else tg.BackButton.hide(); }

    var path = entry[1].replace(/\$(\d)/g, function (_, n) { return match[+n]; });
    var token = ++renderToken;
    app.textContent = "";
    app.appendChild(el("p", "state", "Загрузка…"));
    api(path).then(function (data) {
      if (token !== renderToken) return;
      lastRender = function () {
        app.textContent = "";
        entry[2](app, data, match[1], match[2]);
      };
      lastRender();
      window.scrollTo(0, 0);
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
    tg.onEvent("themeChanged", function () {
      applyTheme();
      if (lastRender) lastRender();
    });
    if (tg.BackButton) tg.BackButton.onClick(function () { history.back(); });
  }
  window.addEventListener("hashchange", render);
  render();
})();
