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
    // Карточка игрока (v2.177.0): полоса «стола» сверху, характеристики из радара
    var hero = el("div", "card hero player-card" + (h.rank && h.rank <= 3 ? " frame frame-" + h.rank : ""));
    hero.appendChild(el("div", "table-band"));
    var top = el("div", "hero-top");
    top.appendChild(avatar({ id: +playerId, initial: h.initial }, "xl"));
    var who = el("div", "body");
    who.appendChild(el("div", "hero-name", h.name));
    who.appendChild(el("div", "sub", h.subtitle));
    if (h.photo_link) who.appendChild(photoLink(h.photo_link, playerId));
    top.appendChild(who);
    hero.appendChild(top);
    var line = el("div", "hero-rating");
    line.appendChild(ratingNumber("hero-value", h.rating, playerId));   // табло (v2.183.0)
    if (h.week) line.appendChild(el("span", "hero-week " + (h.week_up ? "tone-win" : "tone-loss"), h.week));
    hero.appendChild(line);
    if (d.card) hero.appendChild(cardStats(d.card, playerId));
    if (d.chart) hero.appendChild(ratingChart(d.chart));
    if (h.photo_link && !d.empty) hero.appendChild(shareButton("profile", function () { return profileCardBlocks(d); }));
    c.appendChild(hero);
    if (String(playerId) === String(viewerId)) celebrateTitle(h);
  }

  function cardStats(card, playerId) {
    var wrap = clickable(el("div", "card-stats"), "player/" + playerId + "/style");
    card.stats.forEach(function (s, i) {
      var cell = el("div", "card-stat stagger");
      cell.style.animationDelay = (i * 50) + "ms";
      cell.title = s.name + (s.tier_hint ? ": " + s.tier_hint : "");
      // Цвет — относительно клуба (v2.180.0): серый — мало данных, золото — лучший
      cell.appendChild(el("span", "card-stat-value scoreboard" + (s.tier ? " tier tier-" + s.tier : ""), String(s.value)));
      cell.appendChild(el("span", "card-stat-name", s.short));
      wrap.appendChild(cell);
    });
    if (card.archetype) wrap.appendChild(el("div", "card-archetype", card.archetype));
    return wrap;
  }

  // Своё фото ставится в боте (v2.174.0). «Сменить», если фото уже есть
  // (своё или из Telegram), иначе «Поставить». v2.185.0: ссылка на чат с ботом
  // открывала чат под приложением — казалось, что кнопка не работает; теперь
  // бот сам присылает просьбу о фото, а приложение закрывается (как вызов).
  function photoLink(url, playerId) {
    var b = el("button", "hero-photo-link", "Поставить фото");
    b.type = "button";
    loadPhoto(+playerId, document.createElement("div")).then(function (u) { if (u) b.textContent = "Сменить фото"; });
    b.addEventListener("click", function () {
      if (b.disabled) return;
      haptic("impact");
      b.disabled = true;
      post("photo/open", {}).then(function () {
        if (!closeApp()) sheetOpen(null, "Фото — в чате с ботом", "Открой чат с ботом и пришли туда фото.", "");
      }, function (err) {
        sheetOpen(null, "Не получилось", err.message, "");
      }).then(function () { b.disabled = false; });
    });
    return b;
  }

  // Каркас профиля (шапка с графиком, переключатель разделов) строится один раз
  // на игрока; при смене раздела меняется только содержимое под переключателем.
  var shell = null;

  function buildShell(c, d, playerId) {
    var cols = twoCols(c, true);
    profileHero(cols.main, d, playerId);
    // Чужой профиль: «Вызвать на матч» сразу под карточкой, на любом разделе (v2.179.0)
    if (String(playerId) !== String(viewerId)) cols.main.appendChild(challengeButton(playerId));
    var seg = null;
    if (!d.empty) { seg = segmented(playerId, "", d.counts); cols.side.appendChild(seg); }
    var body = el("div", "tab-body");
    cols.side.appendChild(body);
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
      else if (tab === "achievements") renderAchievementsTab(body, r[0], id === String(viewerId));
      else renderStyleTab(body, r[0], r[1]);
    }, function (err) {
      if (token !== renderToken) return;
      body.textContent = "";
      emptyState(body, err.message, true);
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

    c.appendChild(trendCard(playerId));

    // Прогноз на чужом профиле (v2.177.0): шанс сразу, без перехода в «Ты против …»
    if (d.forecast) {
      var fc = clickable(el("div", "card forecast"), "h2h/" + playerId);
      var ft = el("div", "pair-top");
      ft.appendChild(el("span", "eyebrow", "Твой шанс против него"));
      ft.appendChild(num("forecast-num", d.forecast.chance + "%"));
      fc.appendChild(ft);
      fc.appendChild(progressBar(d.forecast.chance / 100));
      var wl = el("div", "forecast-split");
      wl.appendChild(el("span", "tone-win", "Победа " + d.forecast.win));
      wl.appendChild(el("span", "tone-loss", "Поражение " + d.forecast.lose));
      fc.appendChild(wl);
      c.appendChild(fc);
    }

    if (d.versus) {
      var btn = el("button", "button");
      btn.type = "button";
      btn.appendChild(icon("swords", "ico-btn"));
      btn.appendChild(document.createTextNode(d.versus));
      btn.addEventListener("click", function () { go("h2h/" + playerId); });
      c.appendChild(btn);
    }

    if (String(playerId) === String(viewerId)) {
      var mb = el("button", "button button-quiet month-btn");
      mb.type = "button";
      mb.appendChild(icon("sparkles", "ico-btn"));
      mb.appendChild(document.createTextNode("Твой месяц"));
      mb.addEventListener("click", function () { go("month"); });
      c.appendChild(mb);
    }
    if (d.personal) personalBlocks(c, d.personal, playerId);

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

  // Личные рекорды, коронный счёт, главный соперник (v2.177.0)
  function personalBlocks(c, p, playerId) {
    if (p.rival) {
      var own = String(playerId) === String(viewerId);
      var r = p.rival;
      var card = clickable(el("div", "card rival"), own ? "h2h/" + r.id : "player/" + r.id);
      card.appendChild(avatar(r, "md"));
      var b = el("div", "body");
      b.appendChild(el("div", "eyebrow", "Главный соперник"));
      b.appendChild(el("div", "name", r.name));
      b.appendChild(el("div", "sub", r.meetings + " · последняя " + r.last));
      card.appendChild(b);
      card.appendChild(num("rival-score" + (r.tone ? " tone-" + r.tone : ""), r.score));
      c.appendChild(card);
    }
    if (p.bests.length) {
      var s = section("Личные рекорды");
      p.bests.forEach(function (it) {
        var li = el("li", "row row-pair");
        var lb = el("div", "label", it.label);
        li.appendChild(lb);
        li.appendChild(el("div", "value", it.value));
        if (it.date) li.appendChild(el("div", "sub sub-wide", "с " + it.date));
        s.list.appendChild(li);
      });
      c.appendChild(s);
    }
    if (p.signature) {
      var sg = p.signature;
      var tiles = el("div", "tiles signature");
      if (sg.score) {
        var t1 = el("div", "tile");
        t1.appendChild(el("div", "tile-label", "Коронный счёт"));
        t1.appendChild(el("div", "tile-value scoreboard", sg.score));
        t1.appendChild(el("div", "tile-sub", sg.times));
        tiles.appendChild(t1);
      }
      var t2 = el("div", "tile");
      t2.appendChild(el("div", "tile-label", "Партии на дьюсе"));
      t2.appendChild(num("tile-value", sg.deuce));
      t2.appendChild(el("div", "tile-sub", "из " + sg.sets));
      tiles.appendChild(t2);
      c.appendChild(tiles);
    }
  }

  // График рейтинга с периодами и сравнением (v2.168.0): ось — даты, поэтому
  // ряды двух игроков сравнимы. Данные — полный ряд /api/player/{id}/chart.
  var TREND_PERIODS = [[30, "30 дней"], [90, "90 дней"], [0, "Всё"]];

  function trendSvg(series, days) {
    var W = 320, H = 150, L = 4, R = 4, T = 10, B = 18;
    var cutoff = days ? Date.now() - days * 864e5 : 0;
    var lines = series.map(function (s) {
      return s.points.filter(function (p) { return Date.parse(p.d) >= cutoff; });
    });
    var all = [].concat.apply([], lines);
    if (all.length < 2) return el("p", "state", "За этот период мало матчей для графика.");
    var t0 = Math.min.apply(null, all.map(function (p) { return Date.parse(p.d); }));
    var t1 = Math.max.apply(null, all.map(function (p) { return Date.parse(p.d); })) || t0 + 1;
    var lo = Math.min.apply(null, all.map(function (p) { return p.v; })) - 8;
    var hi = Math.max.apply(null, all.map(function (p) { return p.v; })) + 8;
    function x(p) { return L + (W - L - R) * (t1 === t0 ? 0.5 : (Date.parse(p.d) - t0) / (t1 - t0)); }
    function y(v) { return T + (H - T - B) * (1 - (v - lo) / (hi - lo)); }
    var s = svg("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img" });
    lines.forEach(function (pts, i) {
      if (pts.length < 1) return;
      var line = svg("polyline", { "class": "chart-line" + (i ? " chart-line-them" : ""), pathLength: "1",
        points: pts.map(function (p) { return x(p).toFixed(1) + "," + y(p.v).toFixed(1); }).join(" ") });
      s.appendChild(line);
      var last = pts[pts.length - 1];
      s.appendChild(svg("circle", { "class": "chart-dot" + (i ? " chart-dot-them" : ""), cx: x(last), cy: y(last.v), r: 3.5 }));
    });
    var a = svg("text", { "class": "chart-label", x: L, y: H - 3 });
    a.textContent = new Date(t0).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
    var b = svg("text", { "class": "chart-label", x: W - R, y: H - 3, "text-anchor": "end" });
    b.textContent = new Date(t1).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
    s.appendChild(a);
    s.appendChild(b);
    return s;
  }

  function trendCard(playerId) {
    var card = el("div", "card trend");
    var head = el("div", "pair-top");
    head.appendChild(el("span", "eyebrow", "Динамика рейтинга"));
    card.appendChild(head);
    var chips = el("div", "chips chips-pick");
    var area = el("div", "trend-area");
    var keyRow = el("div", "legend trend-key");
    var cmpRow = el("div", "chips chips-pick trend-compare");
    card.appendChild(chips);
    card.appendChild(area);
    card.appendChild(keyRow);
    card.appendChild(cmpRow);
    var state = { days: 90, other: null, mine: null, theirs: null };

    function draw() {
      area.textContent = "";
      if (!state.mine) { area.appendChild(skeleton("body")); return; }
      var series = [state.mine].concat(state.theirs ? [state.theirs] : []);
      area.appendChild(trendSvg(series, state.days));
      keyRow.textContent = "";
      series.forEach(function (sr, i) {
        var k = el("span", "legend-item");
        k.appendChild(el("i", "swatch " + (i ? "swatch-them" : "swatch-me")));
        k.appendChild(document.createTextNode(sr.name));
        keyRow.appendChild(k);
      });
    }

    TREND_PERIODS.forEach(function (p) {
      var b = el("button", "chip chip-btn" + (p[0] === state.days ? " is-active" : ""), p[1]);
      b.type = "button";
      b.addEventListener("click", function () {
        state.days = p[0];
        [].forEach.call(chips.children, function (x) { x.classList.remove("is-active"); });
        b.classList.add("is-active");
        haptic("select");
        draw();
      });
      chips.appendChild(b);
    });

    api("player/" + playerId + "/chart").then(function (d) { state.mine = d; draw(); },
      function () { area.textContent = ""; });
    api("leaderboard").then(function (lb) {
      cmpRow.appendChild(el("span", "trend-compare-label", "Сравнить:"));
      lb.rows.forEach(function (row) {
        if (String(row.player_id) === String(playerId)) return;
        var b = el("button", "chip chip-btn", row.name);
        b.type = "button";
        b.addEventListener("click", function () {
          var on = state.other !== row.player_id;
          [].forEach.call(cmpRow.querySelectorAll(".chip-btn"), function (x) { x.classList.remove("is-active"); });
          haptic("select");
          if (!on) { state.other = null; state.theirs = null; draw(); return; }
          b.classList.add("is-active");
          state.other = row.player_id;
          api("player/" + row.player_id + "/chart").then(function (d) {
            if (state.other === row.player_id) { state.theirs = d; draw(); }
          });
        });
        cmpRow.appendChild(b);
      });
    }, function () {});
    draw();
    return card;
  }

  // Вкладка «Матчи» (v2.176.0): «Против каждого» и история с фильтром по
  // сопернику и периоду. Тап по строке таблицы — фильтр по этому сопернику.
  var PERIOD_FILTERS = [[0, "Всё время"], [30, "30 дней"], [7, "7 дней"]];

  function filterMatches(matches, opponentId, days, todayIso) {
    var since = "";
    if (days) {
      var t = new Date(todayIso + "T00:00:00Z");
      t.setUTCDate(t.getUTCDate() - (days - 1));
      since = t.toISOString().slice(0, 10);
    }
    return matches.filter(function (m) {
      return (!opponentId || m.opponent_id === opponentId) && (!since || (m.iso || "") >= since);
    });
  }

  function recordLine(list) {
    var w = 0, l = 0, dr = 0;
    list.forEach(function (m) { if (m.result === "w") w++; else if (m.result === "l") l++; else dr++; });
    return list.length + " " + pluralWord(list.length, "матч", "матча", "матчей") + " · " + w + "–" + l + (dr ? "–" + dr : "");
  }

  function pluralWord(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m100 >= 11 && m100 <= 14) return many;
    return m10 === 1 ? one : m10 >= 2 && m10 <= 4 ? few : many;
  }

  function mskTodayIso() {
    return new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10);
  }

  function renderMatchesTab(body, d) {
    if (d.empty) { emptyState(body, d.empty); return; }
    var state = { opponent: 0, days: 0 };
    var listWrap = el("div");

    if (d.versus && d.versus.length) {
      var vs = section("Против каждого");
      d.versus.forEach(function (r) {
        var li = el("li", "row tap versus-row-item");
        li.setAttribute("role", "button");
        li.tabIndex = 0;
        li.appendChild(avatar(r, "sm"));
        var b = el("div", "body");
        b.appendChild(el("div", "name", r.name));
        b.appendChild(el("div", "sub", r.score + " · последняя " + r.last));
        li.appendChild(b);
        li.appendChild(el("span", "value" + (r.tone ? " tone-" + r.tone : ""), r.rate));
        li.addEventListener("click", function () {
          haptic("select");
          state.opponent = state.opponent === r.id ? 0 : r.id;
          draw();
          listWrap.scrollIntoView({ behavior: "smooth", block: "start" });
        });
        vs.list.appendChild(li);
      });
      body.appendChild(vs);
    }

    var filters = el("div", "filters");
    var opp = el("div", "chips");
    var per = el("div", "chips");
    filters.appendChild(opp);
    filters.appendChild(per);
    body.appendChild(filters);
    body.appendChild(listWrap);

    function chip(text, active, onPick) {
      var b = el("button", "chip chip-btn" + (active ? " is-active" : ""), text);
      b.type = "button";
      b.addEventListener("click", function () { haptic("select"); onPick(); draw(); });
      return b;
    }

    function draw() {
      opp.textContent = "";
      opp.appendChild(chip("Все", !state.opponent, function () { state.opponent = 0; }));
      (d.versus || []).forEach(function (r) {
        opp.appendChild(chip(r.name, state.opponent === r.id, function () { state.opponent = r.id; }));
      });
      per.textContent = "";
      PERIOD_FILTERS.forEach(function (p) {
        per.appendChild(chip(p[1], state.days === p[0], function () { state.days = p[0]; }));
      });
      var shown = filterMatches(d.matches, state.opponent, state.days, mskTodayIso());
      listWrap.textContent = "";
      var s = section();
      var head = el("div", "group-head");
      head.appendChild(el("h2", "group-title", "История"));
      head.appendChild(el("span", "group-count", shown.length ? recordLine(shown) : ""));
      s.insertBefore(head, s.list);
      if (!shown.length) s.list.appendChild(el("li", "row sub", "Матчей с таким фильтром нет."));
      shown.forEach(function (m) { s.list.appendChild(matchRow(m, !!state.opponent)); });
      listWrap.appendChild(s);
    }
    draw();
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
    var meta = [a.earned ? "Получено" + (a.date ? " " + a.date : "") : "Ещё не получено", tierWord, a.rarity]
      .filter(Boolean).join(" · ");
    sheetOpen(badge, a.name, a.desc, meta);
  }

  // Новые ачивки светятся один раз (v2.169.0): какие уже видел — помнит память
  // устройства. Первый визит ничего не подсвечивает — иначе засветилось бы всё.
  var SEEN_KEY = "seen_achievements_v1";

  function glowNew(body, earnedIds) {
    storageGet(SEEN_KEY, function (raw) {
      if (raw === undefined) return;
      var seen = null;
      try { seen = JSON.parse(raw || "null"); } catch (e) { seen = null; }
      if (Array.isArray(seen)) {
        var fresh = [];
        [].forEach.call(body.querySelectorAll(".badge-cell[data-id]"), function (cell) {
          if (seen.indexOf(cell.getAttribute("data-id")) < 0) { cell.classList.add("is-new"); fresh.push(cell); }
        });
        // v2.182.0: новая ачивка «вылетает», над первой — конфетти
        if (fresh.length) {
          fresh[0].scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
          setTimeout(function () { confetti(fresh[0].querySelector(".badge") || fresh[0]); }, 450);
          try { if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* нет вибрации */ }
        }
      }
      storageSet(SEEN_KEY, JSON.stringify(earnedIds.slice().sort()));
    });
  }

  function renderAchievementsTab(body, d, own) {
    body.appendChild(el("p", "footnote footnote-top", "Получено " + d.count + ". Нажмите на значок, чтобы прочитать условие."));
    if (d.nearest && d.nearest.length) {
      var near = section("Ближе всего");
      d.nearest.forEach(function (n) {
        var li = el("li", "row");
        var badge = el("div", "badge badge-sm " + (n.tier ? "badge-" + n.tier : "badge-earned"));
        badge.appendChild(useIcon(n.icon));
        li.appendChild(badge);
        var b = el("div", "body");
        var top = el("div", "pair-top");
        top.appendChild(el("span", "name", n.name));
        top.appendChild(el("span", "value", n.value));
        b.appendChild(top);
        b.appendChild(el("div", "sub", n.desc));
        b.appendChild(progressBar(n.progress));
        li.appendChild(b);
        near.list.appendChild(li);
      });
      body.appendChild(near);
    }
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
        if (a.earned && a.id) cell.setAttribute("data-id", a.id);
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
    if (own) {
      var ids = [];
      d.categories.forEach(function (cat) {
        cat.items.forEach(function (a) { if (a.earned && a.id) ids.push(a.id); });
      });
      glowNew(body, ids);
    }
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
        var row = itemRow({ label: ax.name, value: ax.value + "%", note: ax.hint, progress: ax.value / 100 });
        // v2.180.0: цвет и пояснение «на фоне клуба»
        if (ax.tier) {
          var v = row.querySelector(".value");
          if (v) v.className += " tier tier-" + ax.tier;
          var body = row.querySelector(".body");
          if (body && ax.tier_hint) body.insertBefore(el("div", "tier-note tier-" + ax.tier, ax.tier_hint), body.children[1] || null);
        }
        axes.list.appendChild(row);
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

