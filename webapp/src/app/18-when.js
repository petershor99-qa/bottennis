  // ── Когда играют (v2.170.0) ────────────────────────────────────────────────
  function renderWhen(c, d, who) {
    c.appendChild(header(d.title, [d.period, d.total].filter(Boolean).join(" · ")));
    var seg = el("div", "segmented");
    [["club", "Клуб"], ["me", "Я"]].forEach(function (t) {
      var b = el("button", "seg" + (t[0] === who ? " is-active" : ""), t[1]);
      b.type = "button";
      b.addEventListener("click", function () {
        if (t[0] === who) return;
        haptic("select");
        location.replace(t[0] === "me" ? "#/when/me" : "#/when");
      });
      seg.appendChild(b);
    });
    c.appendChild(seg);
    if (d.empty) { emptyState(c, d.empty); return; }

    var card = el("div", "card when");
    var grid = el("div", "when-grid");
    grid.style.setProperty("--cols", String(d.hours.length));
    grid.appendChild(el("span", "when-corner"));
    d.hours.forEach(function (h, i) { grid.appendChild(el("span", "when-hour", i % 2 === 0 ? String(h) : "")); });
    d.rows.forEach(function (row, ri) {
      grid.appendChild(el("span", "when-day", row.day));
      row.cells.forEach(function (cell, ci) {
        var b = el("button", "when-cell heat-" + cell.lvl);
        b.type = "button";
        b.style.animationDelay = (ri * 40 + ci * 12) + "ms";
        b.setAttribute("aria-label", row.full + ", " + cell.h + ":00 — " + cell.n);
        b.addEventListener("click", function () {
          var hh = (cell.h < 10 ? "0" : "") + cell.h;
          var next = (cell.h + 1 < 10 ? "0" : "") + (cell.h + 1);
          sheetOpen(null, row.full + ", " + hh + ":00–" + next + ":00", matchesWord(cell.n), "");
        });
        grid.appendChild(b);
      });
    });
    card.appendChild(grid);
    c.appendChild(card);
    var lg = el("div", "legend");
    [["0", "нет"], ["1", "реже"], ["3", "чаще всего"]].forEach(function (x) {
      var item = el("span", "legend-item");
      item.appendChild(el("i", "swatch heat-" + x[0]));
      item.appendChild(document.createTextNode(x[1]));
      lg.appendChild(item);
    });
    c.appendChild(lg);

    var facts = section("Главное");
    d.facts.forEach(function (it) { facts.list.appendChild(itemRow(it)); });
    c.appendChild(facts);
  }

  function matchesWord(n) {
    var m10 = n % 10, m100 = n % 100;
    var w = m100 >= 11 && m100 <= 14 ? "матчей" : m10 === 1 ? "матч" : m10 >= 2 && m10 <= 4 ? "матча" : "матчей";
    return n === 0 ? "Матчей не было" : n + " " + w + " за 90 дней";
  }

