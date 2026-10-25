  // ── Итоги дня (v2.176.0) ───────────────────────────────────────────────────
  // Считаются на лету за любой игровой день; стрелки листают дни с матчами.
  function compactDate(iso) { return iso ? iso.replace(/-/g, "") : ""; }

  function dayNav(d) {
    var nav = el("div", "day-nav");
    function arrow(target, cls, name, label) {
      var b = el("button", "day-arrow " + cls);
      b.type = "button";
      b.setAttribute("aria-label", label);
      b.appendChild(icon(name));
      if (!target) b.disabled = true;
      else b.addEventListener("click", function () { haptic("select"); location.replace("#/summary/day/" + compactDate(target)); });
      return b;
    }
    nav.appendChild(arrow(d.prev, "day-prev", "chevron-left", "Предыдущий игровой день"));
    nav.appendChild(el("div", "day-label", d.label));
    nav.appendChild(arrow(d.next, "day-next", "chevron-right", "Следующий игровой день"));
    return nav;
  }

  function dayHero(p, title, tone) {
    var card = clickable(el("div", "card day-hero day-hero-" + tone), "player/" + p.id);
    card.appendChild(avatar(p, "lg"));
    var b = el("div", "body");
    b.appendChild(el("div", "eyebrow", title));
    b.appendChild(el("div", "day-hero-name", p.name));
    b.appendChild(el("div", "sub", p.matches + " · " + p.record));
    if (p.form.length) b.appendChild(formStrip(p.form, "strip-inline"));
    card.appendChild(b);
    card.appendChild(num("day-hero-delta tone-" + (tone === "up" ? "win" : "loss"), p.delta));
    return card;
  }

  function hourBars(hours) {
    var W = 320, H = 82, B = 16, T = 12, gap = 3;
    var max = Math.max.apply(null, hours.map(function (x) { return x.n; }).concat([1]));
    var bw = (W - gap * (hours.length - 1)) / hours.length;
    var s = svg("svg", { "class": "chart hour-bars", viewBox: "0 0 " + W + " " + H, role: "img" });
    hours.forEach(function (x, i) {
      var left = i * (bw + gap), h = x.n ? Math.max(3, (H - B - T) * x.n / max) : 2;
      var t = svg("title");
      t.textContent = x.h + ":00 — " + x.n;
      var r = svg("rect", { x: left, y: H - B - h, width: bw, height: h, rx: 2, "class": x.n ? "hour-bar" : "hour-bar is-empty" });
      r.appendChild(t);
      s.appendChild(r);
      if (x.n) {
        // число матчей над столбиком (v2.211.0): без него одинаковые столбики ничего не говорили
        var count = svg("text", { x: left + bw / 2, y: H - B - h - 3, "text-anchor": "middle", "class": "chart-label hour-count" });
        count.textContent = String(x.n);
        s.appendChild(count);
      }
      if (hours.length <= 8 || i % 2 === 0) {
        var label = svg("text", { x: left + bw / 2, y: H - 3, "text-anchor": "middle", "class": "chart-label" });
        label.textContent = String(x.h);
        s.appendChild(label);
      }
    });
    return s;
  }

  function dayMatchCard(m, title, extra) {
    var li = clickable(el("li", "feed-card feed-match"), "match/" + m.id);
    var body = el("div", "body");
    if (title) body.appendChild(el("div", "eyebrow", title));
    body.appendChild(versusLine(m));
    var score = el("div", "feed-score");
    score.appendChild(setChips(m.sets, m.winner));
    if (m.delta) score.appendChild(el("span", "feed-delta tone-win", m.delta));
    body.appendChild(score);
    if (extra) body.appendChild(el("div", "sub feed-report", extra));
    if (m.reactions && m.reactions.length) body.appendChild(reactionCounts(m.reactions));
    li.appendChild(body);
    if (m.time) li.appendChild(el("div", "meta feed-time", m.time));
    return li;
  }

  function renderDay(c, d, tabs) {
    c.appendChild(header("Итоги", ""));
    c.appendChild(tabs);
    c.appendChild(dayNav(d));
    if (d.empty) { emptyState(c, d.empty); return; }
    var dayCols = twoCols(c, false);
    c = dayCols.main;

    var board = el("div", "tiles tiles-3 day-board");
    [["Матчей", d.board.matches], ["Партий", d.board.sets], ["Очков", d.board.points]].forEach(function (t) {
      var tile = el("div", "tile");
      tile.appendChild(el("div", "tile-label", t[0]));
      tile.appendChild(num("tile-value", String(t[1])));
      board.appendChild(tile);
    });
    c.appendChild(board);
    if (d.board.usual) c.appendChild(el("p", "day-usual", d.board.usual));

    if (d.hero || d.slump) {
      var heroes = el("div", "day-heroes");
      if (d.hero) heroes.appendChild(dayHero(d.hero, "Игрок дня", "up"));
      if (d.slump) heroes.appendChild(dayHero(d.slump, "Кто сдал", "down"));
      c.appendChild(heroes);
    }

    if (d.match_of_day || d.discussed) {
      var top = el("section", "group");
      top.appendChild(el("h2", "group-title", "Главное"));
      var tl = el("ul", "feed-list");
      if (d.match_of_day) tl.appendChild(dayMatchCard(d.match_of_day, "Матч дня", d.match_of_day.report));
      if (d.discussed && (!d.match_of_day || d.discussed.id !== d.match_of_day.id)) {
        tl.appendChild(dayMatchCard(d.discussed, "Матч, который обсуждали", ""));
      }
      top.appendChild(tl);
      c.appendChild(top);
    }

    c = dayCols.side;
    if (d.movement.length) {
      var mv = section("Таблица за день");
      d.movement.forEach(function (r, i) {
        var li = clickable(el("li", "row stagger" + (r.played ? "" : " is-dim")), "player/" + r.id);
        li.style.animationDelay = (i * 40) + "ms";
        li.appendChild(el("div", "rank-num", String(r.place)));
        li.appendChild(avatar(r, "sm"));
        var b = el("div", "body");
        b.appendChild(el("div", "name", r.name));
        li.appendChild(b);
        // справа — сколько рейтинга набрал или потерял за день (v2.211.0; были одни прочерки
        // «место не менялось»); сдвиг места — стрелкой рядом
        var arrow = r.new ? "новый " : r.change > 0 ? "▲" + r.change + " " : r.change < 0 ? "▼" + (-r.change) + " " : "";
        var ch = r.played ? el("span", "move " + deltaTone(r.delta), (arrow ? arrow : "") + r.delta) :
          el("span", "move move-same", arrow ? arrow.trim() : "—");
        li.appendChild(ch);
        mv.list.appendChild(li);
      });
      c.appendChild(mv);
    }

    if (d.players.length) {
      var ps = section("Кто как сыграл");
      d.players.forEach(function (p) {
        var li = clickable(el("li", "row"), "player/" + p.id);
        li.appendChild(avatar(p, "sm"));
        var b = el("div", "body");
        b.appendChild(el("div", "name", p.name));
        b.appendChild(el("div", "sub", p.matches + " · " + p.record));
        li.appendChild(b);
        li.appendChild(el("span", "value " + deltaTone(p.delta), p.delta));
        ps.list.appendChild(li);
      });
      c.appendChild(ps);
    }

    var when = el("section", "group");
    when.appendChild(el("h2", "group-title", "По часам"));
    when.appendChild(el("p", "footnote footnote-top", "Сколько матчей закончилось в каждый час."));
    var chart = el("div", "card");
    chart.appendChild(hourBars(d.hours));
    when.appendChild(chart);
    c.appendChild(when);

    // Список матчей дня — со своим заголовком (v2.212.0: раньше карточки шли сразу под столбиками часов)
    var matchesSec = el("section", "group");
    matchesSec.appendChild(el("h2", "group-title", "Матчи дня"));
    var all = el("ul", "feed-list");
    d.timeline.forEach(function (m) { all.appendChild(dayMatchCard(m, "", "")); });
    matchesSec.appendChild(all);
    c.appendChild(matchesSec);

    if (d.achievements.length || d.records.length) {
      var ach = el("section", "group");
      ach.appendChild(el("h2", "group-title", "Ачивки и рекорды"));
      var al = el("ul", "feed-list");
      d.records.forEach(function (r) {
        var li = r.player ? clickable(el("li", "feed-card"), "player/" + r.player.id) : el("li", "feed-card");
        li.appendChild(icon("trophy", "ico-lead ico-gold"));
        var b = el("div", "body");
        b.appendChild(el("div", "name", r.title));
        b.appendChild(el("div", "sub", [r.player ? r.player.name : "", r.value].filter(Boolean).join(" · ")));
        li.appendChild(b);
        al.appendChild(li);
      });
      d.achievements.forEach(function (a) {
        var li = clickable(el("li", "feed-card"), "player/" + a.player.id);
        var badge = el("div", "badge badge-sm " + (a.tier ? "badge-" + a.tier : "badge-earned"));
        badge.appendChild(useIcon(a.icon));
        li.appendChild(badge);
        var b = el("div", "body");
        b.appendChild(el("div", "name", "«" + a.name + "»"));
        b.appendChild(el("div", "sub", a.player.name + " · " + a.desc));
        li.appendChild(b);
        al.appendChild(li);
      });
      ach.appendChild(al);
      c.appendChild(ach);
    }
    var share = el("div", "share-row");
    share.appendChild(shareButton("day", function () { return dayCardBlocks(d); }));
    c.appendChild(share);
  }

  function summaryTabs(tabs, kind) {
    var seg = el("div", "segmented segmented-flat");
    tabs.forEach(function (t) {
      var b = el("button", "seg" + (t.kind === kind ? " is-active" : "") + (t.available ? "" : " is-dim"), t.title);
      b.type = "button";
      b.addEventListener("click", function () {
        if (t.kind === kind) return;
        haptic("select");
        location.replace("#/summary/" + t.kind);
      });
      seg.appendChild(b);
    });
    return seg;
  }

  // Итоги года (v2.170.0): «Твой год» и неформальные звания — только просмотр,
  // голосование остаётся в боте
  function yearExtras(c, d) {
    [d.mine, d.vote].forEach(function (block) {
      if (!block) return;
      var sec = section(block.title);
      block.items.forEach(function (it) { sec.list.appendChild(itemRow(it)); });
      c.appendChild(sec);
    });
  }

