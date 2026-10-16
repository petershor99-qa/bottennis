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

  // v2.202.0: неделя, месяц, квартал, год — на лету за любой период: листание,
  // табло со сравнением, тумба с аватарами, «Твоя неделя», герои карточками,
  // матч периода карточкой, «Гонка рейтинга», «Поделиться картинкой»
  function periodNav(d, kind) {
    var nav = el("div", "day-nav");
    function arrow(target, name, label) {
      var b = el("button", "day-arrow");
      b.type = "button";
      b.setAttribute("aria-label", label);
      b.appendChild(icon(name));
      if (!target) b.disabled = true;
      else b.addEventListener("click", function () { haptic("select"); location.replace("#/summary/" + kind + "/" + compactDate(target)); });
      return b;
    }
    nav.appendChild(arrow(d.prev, "chevron-left", "Предыдущий период"));
    nav.appendChild(el("div", "day-label", d.label));
    nav.appendChild(arrow(d.next, "chevron-right", "Следующий период"));
    return nav;
  }

  function faces(players) {
    var row = el("div", "hero-faces");
    var pics = el("div", "rec-faces");
    players.forEach(function (p) { pics.appendChild(avatar(p, "sm")); });
    row.appendChild(pics);
    row.appendChild(el("span", "rec-name", players.map(function (p) { return p.name; }).join(players.length === 2 ? " vs " : ", ")));
    return row;
  }

  function heroTile(h, i) {
    var route = h.players.length === 1 ? "player/" + h.players[0].id :
      h.key === "derby" && h.players.length === 2 ? "pair/" + h.players[0].id + "/" + h.players[1].id : null;
    var t = route ? clickable(el("div", "rec-tile hero-tile stagger"), route) : el("div", "rec-tile hero-tile stagger");
    t.style.animationDelay = (i * 60) + "ms";
    t.appendChild(icon(h.icon, "rec-ico hero-ico-" + h.key));
    t.appendChild(el("div", "rec-tile-title", h.title));
    if (h.value) {
      // число крупно, слова после него мелко: «3» + «победы подряд»
      var v = el("div", "rec-tile-value");
      var parts = splitNumber(h.value);
      v.appendChild(num("scoreboard", parts ? parts[0] + parts[1] + parts[2] : h.value));
      if (parts && parts[3].trim()) v.appendChild(el("span", "rec-unit", parts[3].trim()));
      t.appendChild(v);
    }
    if (h.players.length) t.appendChild(faces(h.players));
    if (h.note) t.appendChild(el("div", "sub", h.note));
    return t;
  }

  function youCard(you) {
    var card = el("div", "card you-card");
    card.appendChild(el("div", "eyebrow", you.title));
    you.lines.forEach(function (line) { card.appendChild(el("div", "you-line", line)); });
    if (you.story) {
      var b = el("button", "button button-quiet you-story", "Смотреть сторис");
      b.type = "button";
      b.addEventListener("click", function () { haptic("impact"); go("month/" + you.story); });
      card.appendChild(b);
    }
    return card;
  }

  function renderPeriod(c, d, kind) {
    c.appendChild(header(d.title, ""));
    c.appendChild(summaryTabs(d.tabs, kind));
    c.appendChild(periodNav(d, kind));
    if (d.empty) { emptyState(c, d.empty); yearExtras(c, d); return; }
    var cols = twoCols(c, false);
    var main = cols.main;
    if (d.you) main.appendChild(youCard(d.you));
    var board = el("div", "tiles tiles-3 day-board");
    d.board.forEach(function (t) {
      var tile = el("div", "tile");
      tile.appendChild(el("div", "tile-label", t.label));
      tile.appendChild(num("tile-value", t.value));
      if (t.delta) tile.appendChild(el("div", "tile-sub " + deltaTone(t.delta), t.delta));
      board.appendChild(tile);
    });
    main.appendChild(board);
    if (d.compare_note) main.appendChild(el("p", "day-usual", d.compare_note));
    if (d.standings.length >= 2) {
      main.appendChild(podium(d.standings.slice(0, 3).map(function (s) {
        return { id: s.id, name: s.name, initial: s.initial, rating: s.record, streak: 0, champion: false };
      })));
    }
    if (d.match) {
      var top = el("ul", "feed-list");
      top.appendChild(dayMatchCard(d.match, d.match.title, d.match.report));
      main.appendChild(top);
    }
    var side = cols.side;
    if (d.heroes.length) {
      var hs = el("section", "group");
      hs.appendChild(el("h2", "group-title", d.heroes_title));
      var grid = el("div", "rec-tiles");
      d.heroes.forEach(function (h, i) { grid.appendChild(heroTile(h, i)); });
      hs.appendChild(grid);
      side.appendChild(hs);
    }
    var s = section(d.standings_title);
    d.standings.forEach(function (r, i) {
      var li = clickable(el("li", "row stagger"), "player/" + r.id);
      li.style.animationDelay = (i * 50) + "ms";
      li.appendChild(el("div", "rank-num", String(r.place)));
      li.appendChild(avatar(r, "sm"));
      var b = el("div", "body");
      b.appendChild(el("div", "name", r.name));
      b.appendChild(el("div", "sub", r.record));
      li.appendChild(b);
      if (r.form.length) li.appendChild(formStrip(r.form, "strip-inline"));
      s.list.appendChild(li);
    });
    side.appendChild(s);
    var race = section();
    race.list.appendChild(clickable(itemRow({ label: "Гонка рейтинга", value: "›" }), "race"));
    side.appendChild(race);
    var share = el("div", "share-row");
    share.appendChild(shareButton("period", function () { return periodCardBlocks(d); }));
    side.appendChild(share);
    yearExtras(side, d);
  }

  function renderSummary(c, d, kind) {
    if (kind === "day" && d.day) { renderDay(c, d.day, summaryTabs(d.tabs, kind)); return; }
    if (d.board || d.label) { renderPeriod(c, d, kind); return; }
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

