  // ── Матчи, «ты против него» ────────────────────────────────────────────────
  var RESULT_WORDS = { w: "Победа", l: "Поражение", d: "Ничья" };

  function matchRow(m, sameOpponent) {
    var li = clickable(el("li", "row match-row result-" + m.result), m.id ? "match/" + m.id : "player/" + m.opponent_id);
    var body = el("div", "body");
    body.appendChild(el("div", "name", sameOpponent ? RESULT_WORDS[m.result] : m.opponent));
    body.appendChild(el("div", "sub", [m.date, m.score, m.boss ? "боссфайт" : ""].filter(Boolean).join(" · ")));
    li.appendChild(body);
    if (m.delta) li.appendChild(el("div", "delta " + deltaTone(m.delta), m.delta));
    return li;
  }

  // История дуэли (v2.177.0): кто вёл по ходу встреч (баланс побед), лучшие
  // серии, лучший матч пары и последние месяцы
  // Не больше max точек: каждая k-я встреча плюс последняя (v2.204.0) — при сотнях
  // встреч шаги ±1 сливались в «пилу», форма «кто вёл» терялась
  function thinPoints(points, max) {
    if (points.length <= max) return points;
    var step = (points.length - 1) / (max - 1), out = [];
    for (var i = 0; i < max; i++) out.push(points[Math.round(i * step)]);
    return out;
  }

  function balanceSvg(points) {
    points = thinPoints(points, 60);
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

  function duelCard(duel, them, meName) {
    var wrap = el("section", "group");
    wrap.appendChild(el("h2", "group-title", "История дуэли"));
    var card = el("div", "card duel");
    var top = el("div", "pair-top");
    top.appendChild(el("span", "eyebrow", "Кто вёл: выше нуля — " + (meName || "ты")));
    if (duel.points_share !== null) {
      top.appendChild(el("span", "eyebrow-meta", meName ? meName + ": " + duel.points_share + "% очков" : "твоих очков " + duel.points_share + "%"));
    }
    card.appendChild(top);
    card.appendChild(balanceSvg(duel.balance));
    var tiles = el("div", "tiles duel-tiles");
    [[meName ? "Лучшая серия: " + meName : "Твоя лучшая серия", duel.best_streak_me, "win"],
     [meName ? "Лучшая серия: " + them : "Лучшая серия соперника", duel.best_streak_them, "loss"]].forEach(function (t) {
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
    var meName = d.pair ? d.me.name : "Ты";   // v2.193.0: пара глазами третьего
    var me = d.pair ? clickable(el("div", "versus-side"), "player/" + d.me.id) : el("div", "versus-side");
    me.appendChild(avatar(d.me, "lg"));
    me.appendChild(el("div", "versus-name", meName));
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
    ft.appendChild(el("span", "eyebrow", d.pair ? "Шанс победы — " + d.me.name : "Шанс победы сейчас"));
    ft.appendChild(num("forecast-num", d.forecast.chance + "%"));
    fc.appendChild(ft);
    fc.appendChild(progressBar(d.forecast.chance / 100));
    var wl = el("div", "forecast-split");
    wl.appendChild(el("span", "tone-win", "Победа " + d.forecast.win));
    wl.appendChild(el("span", "tone-loss", "Поражение " + d.forecast.lose));
    fc.appendChild(wl);
    c.appendChild(fc);
    if (!d.pair) c.appendChild(challengeButton(d.them.id));

    if (d.axes.length) {
      var ax = section("Стиль: " + (d.pair ? d.me.name : "ты") + " и " + d.them.name);
      var key = el("li", "row axis-key");
      var k1 = el("span", "legend-item");
      k1.appendChild(el("i", "swatch swatch-me"));
      k1.appendChild(document.createTextNode(meName));
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
    if (d.duel) c.appendChild(duelCard(d.duel, d.them.name, d.pair ? d.me.name : null));
    var m = section("Итог встреч");
    d.metrics.forEach(function (it) { m.list.appendChild(itemRow(it)); });
    c.appendChild(m);
    var s = section("Все встречи");
    appendInChunks(s.list, d.matches, function (x) {
      var row = matchRow(x, true);
      if (d.pair) row.querySelector(".name").textContent = x.result === "d" ? "Ничья"
        : "Победа: " + (x.result === "w" ? d.me.name : d.them.name);
      return row;
    });
    c.appendChild(s);
  }

