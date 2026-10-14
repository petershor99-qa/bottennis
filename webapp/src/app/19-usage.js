  // ── Использование, только администратор (v2.170.0) ─────────────────────────
  function usageBars(daily) {
    var W = 320, H = 96, B = 14, gap = 2;
    var max = Math.max.apply(null, daily.map(function (x) { return x.bot + x.app; }).concat([1]));
    var bw = (W - gap * (daily.length - 1)) / daily.length;
    var s = svg("svg", { "class": "chart usage-bars", viewBox: "0 0 " + W + " " + H, role: "img" });
    daily.forEach(function (x, i) {
      var left = i * (bw + gap);
      var hBot = (H - B) * x.bot / max, hApp = (H - B) * x.app / max;
      var t = svg("title");
      t.textContent = x.d + ": бот " + x.bot + ", приложение " + x.app;
      var g = svg("g");
      g.appendChild(t);
      g.appendChild(svg("rect", { "class": "bar-bot", x: left.toFixed(1), y: (H - B - hBot).toFixed(1), width: bw.toFixed(1), height: hBot.toFixed(1), rx: 1.5 }));
      g.appendChild(svg("rect", { "class": "bar-app", x: left.toFixed(1), y: (H - B - hBot - hApp).toFixed(1), width: bw.toFixed(1), height: hApp.toFixed(1), rx: 1.5 }));
      s.appendChild(g);
    });
    [0, daily.length - 1].forEach(function (i) {
      var lbl = svg("text", { "class": "chart-label", x: i === 0 ? 0 : W, y: H - 2, "text-anchor": i === 0 ? "start" : "end" });
      lbl.textContent = daily[i].d;
      s.appendChild(lbl);
    });
    return s;
  }

  function renderUsage(c, d) {
    c.appendChild(header(d.title, d.period));
    if (d.empty) { emptyState(c, d.empty); return; }
    var tiles = el("div", "tiles");
    d.tiles.forEach(function (t) {
      var tile = el("div", "tile");
      tile.appendChild(el("div", "tile-label", t.label));
      tile.appendChild(num("tile-value", t.value));
      if (t.sub) tile.appendChild(el("div", "tile-sub", t.sub));
      tiles.appendChild(tile);
    });
    c.appendChild(tiles);
    var card = el("div", "card");
    var head = el("div", "pair-top");
    head.appendChild(el("span", "eyebrow", "По дням"));
    card.appendChild(head);
    card.appendChild(usageBars(d.daily));
    var lg = el("div", "legend legend-tight");
    [["bar-bot", "бот"], ["bar-app", "приложение"]].forEach(function (x) {
      var item = el("span", "legend-item");
      item.appendChild(el("i", "swatch " + x[0]));
      item.appendChild(document.createTextNode(x[1]));
      lg.appendChild(item);
    });
    card.appendChild(lg);
    c.appendChild(card);
    [["Приложение", d.app], ["Бот", d.bot]].forEach(function (x) {
      if (!x[1].length) return;
      var sec = section(x[0]);
      x[1].forEach(function (it) { sec.list.appendChild(itemRow(it)); });
      c.appendChild(sec);
    });
    c.appendChild(el("p", "footnote", "Без разбивки по людям: сколько раз открыт экран и сколькими игроками."));
  }

