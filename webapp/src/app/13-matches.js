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

