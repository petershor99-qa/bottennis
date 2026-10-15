  // ── Матчи, «ты против него» ────────────────────────────────────────────────
  var RESULT_WORDS = { w: "Победа", l: "Поражение", d: "Ничья" };

  function matchRow(m, sameOpponent) {
    var li = clickable(el("li", "row match-row result-" + m.result), m.id ? "match/" + m.id : "player/" + m.opponent_id);
    var body = el("div", "body");
    body.appendChild(el("div", "name", sameOpponent ? RESULT_WORDS[m.result] : m.opponent));
    body.appendChild(el("div", "sub", [m.date, m.score, m.boss ? "боссфайт" : ""].filter(Boolean).join(" · ")));
    li.appendChild(body);
    if (m.delta) li.appendChild(el("div", "delta " + (m.delta.charAt(0) === "+" ? "tone-win" : "tone-loss"), m.delta));
    return li;
  }

  // История дуэли (v2.177.0): кто вёл по ходу встреч (баланс побед), лучшие
  // серии, лучший матч пары и последние месяцы
  function balanceSvg(points) {
    var W = 320, H = 110, T = 10, B = 18;
    var vals = points.map(function (p) { return p.v; });
    var lo = Math.min.apply(null, vals.concat([0])), hi = Math.max.apply(null, vals.concat([0]));
    if (hi === lo) { hi += 1; lo -= 1; }
    function x(i) { return points.length < 2 ? W / 2 : 4 + (W - 8) * i / (points.length - 1); }
    function y(v) { return T + (H - T - B) * (1 - (v - lo) / (hi - lo)); }
    var s = svg("svg", { "class": "chart balance", viewBox: "0 0 " + W + " " + H, role: "img" });
    s.appendChild(svg("line", { x1: 0, x2: W, y1: y(0), y2: y(0), "class": "balance-zero" }));
    var dpath = points.map(function (p, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.v).toFixed(1); }).join(" ");
    s.appendChild(svg("path", { d: dpath, "class": "balance-line" }));
    var last = points[points.length - 1];
    s.appendChild(svg("circle", { cx: x(points.length - 1), cy: y(last.v), r: 4,
      "class": last.v > 0 ? "balance-dot is-up" : last.v < 0 ? "balance-dot is-down" : "balance-dot" }));
    [[0, "start"], [points.length - 1, "end"]].forEach(function (e) {
      var t = svg("text", { x: e[1] === "start" ? 2 : W - 2, y: H - 3, "text-anchor": e[1], "class": "chart-label" });
      t.textContent = points[e[0]].d;
      s.appendChild(t);
    });
    return s;
  }

  function duelCard(duel, them) {
    var wrap = el("section", "group");
    wrap.appendChild(el("h2", "group-title", "История дуэли"));
    var card = el("div", "card duel");
    var top = el("div", "pair-top");
    top.appendChild(el("span", "eyebrow", "Кто вёл: выше нуля — ты"));
    if (duel.points_share !== null) top.appendChild(el("span", "eyebrow-meta", "твоих очков " + duel.points_share + "%"));
    card.appendChild(top);
    card.appendChild(balanceSvg(duel.balance));
    var tiles = el("div", "tiles duel-tiles");
    [["Твоя лучшая серия", duel.best_streak_me, "win"], ["Лучшая серия соперника", duel.best_streak_them, "loss"]].forEach(function (t) {
      var tile = el("div", "tile tile-" + t[2]);
      tile.appendChild(el("div", "tile-label", t[0]));
      tile.appendChild(num("tile-value", String(t[1])));
      tiles.appendChild(tile);
    });
    card.appendChild(tiles);
    if (duel.months.length > 1) {
      var mo = el("div", "duel-months");
      duel.months.forEach(function (m) {
        var col = el("div", "duel-month");
        var bar = el("div", "duel-bar");
        var total = Math.max(1, m.me + m.them);
        var me = el("i", "duel-me"); me.style.height = (m.me / total * 100) + "%";
        var th = el("i", "duel-them"); th.style.height = (m.them / total * 100) + "%";
        bar.appendChild(th);
        bar.appendChild(me);
        col.appendChild(bar);
        col.appendChild(el("span", "duel-score", m.me + ":" + m.them));
        col.appendChild(el("span", "duel-label", m.m));
        mo.appendChild(col);
      });
      card.appendChild(mo);
    }
    wrap.appendChild(card);
    var best = el("ul", "feed-list");
    var bm = duel.best_match;
    var li = clickable(el("li", "feed-card feed-match"), "match/" + bm.id);
    var body = el("div", "body");
    body.appendChild(el("div", "eyebrow", "Лучший матч пары · " + bm.date));
    body.appendChild(versusLine(bm));
    var sc = el("div", "feed-score");
    sc.appendChild(setChips(bm.sets, bm.winner));
    body.appendChild(sc);
    li.appendChild(body);
    best.appendChild(li);
    wrap.appendChild(best);
    return wrap;
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
    c.appendChild(challengeButton(d.them.id));

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
    if (d.duel) c.appendChild(duelCard(d.duel, d.them.name));
    var m = section("Итог встреч");
    d.metrics.forEach(function (it) { m.list.appendChild(itemRow(it)); });
    c.appendChild(m);
    var s = section("Все встречи");
    d.matches.forEach(function (x) { s.list.appendChild(matchRow(x, true)); });
    c.appendChild(s);
  }

