  // ── Итоги (снимки сводок, v2.168.0) ────────────────────────────────────────
  function summaryPodium(rows) {
    var wrap = el("div", "podium podium-summary");
    [1, 0, 2].forEach(function (idx, k) {
      var p = rows[idx];
      if (!p) return;
      var col = el("div", "podium-col podium-" + p.place);
      col.appendChild(el("div", "podium-name", p.name));
      var block = el("div", "podium-block grow");
      block.style.animationDelay = (k * 120) + "ms";
      block.appendChild(el("div", "podium-rank", String(p.place)));
      block.appendChild(el("div", "podium-rating", p.record));
      col.appendChild(block);
      wrap.appendChild(col);
    });
    return wrap;
  }

  function renderSummary(c, d, kind) {
    if (kind === "day" && d.day) { renderDay(c, d.day, summaryTabs(d.tabs, kind)); return; }
    c.appendChild(header(d.title || "Итоги", d.date || ""));
    c.appendChild(summaryTabs(d.tabs, kind));
    if (d.empty) { emptyState(c, d.empty); yearExtras(c, d); return; }
    if (d.played.length) {
      var chips = el("div", "summary-played");
      d.played.forEach(function (line, i) {
        var chip = el("div", "summary-pill", line);
        chip.style.animationDelay = (i * 80) + "ms";
        chips.appendChild(chip);
      });
      c.appendChild(chips);
    }
    if (d.standings.length) {
      if (d.standings.length >= 2) c.appendChild(summaryPodium(d.standings));
      var s = section(d.standings_title || "Таблица");
      d.standings.forEach(function (r, i) {
        var li = el("li", "row row-pair stagger");
        li.style.animationDelay = (i * 50) + "ms";
        li.appendChild(el("div", "rank-num", String(r.place)));
        var b = el("div", "body");
        b.appendChild(el("div", "name", r.name));
        if (r.record) b.appendChild(el("div", "sub", r.record));
        li.appendChild(b);
        if (r.form.length) li.appendChild(formStrip(r.form.slice(-7), "strip-inline"));
        s.list.appendChild(li);
      });
      c.appendChild(s);
    }
    d.groups.forEach(function (g) {
      var sec = section();
      g.forEach(function (it) { sec.list.appendChild(itemRow(it)); });
      c.appendChild(sec);
    });
    yearExtras(c, d);
  }

