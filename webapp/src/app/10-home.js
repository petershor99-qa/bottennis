  // ── Клуб (главная) ─────────────────────────────────────────────────────────
  function podium(items) {
    var wrap = el("div", "podium");
    [1, 0, 2].forEach(function (idx) {
      var p = items[idx];
      if (!p) return;
      var col = clickable(el("div", "podium-col podium-" + p.rank), "player/" + p.id);
      var crownSlot = el("div", "crown-slot");
      if (p.champion) crownSlot.appendChild(icon("crown", "crown"));
      col.appendChild(crownSlot);
      col.appendChild(avatar(p, p.rank === 1 ? "xl" : "lg"));
      col.appendChild(el("div", "podium-name", p.name));
      var block = el("div", "podium-block");
      block.appendChild(el("div", "podium-rank", String(p.rank)));
      block.appendChild(num("podium-rating", p.rating));
      col.appendChild(block);
      wrap.appendChild(col);
    });
    return wrap;
  }

  function setChips(sets, winner) {
    var wrap = el("div", "chips");
    sets.forEach(function (sc) {
      var parts = sc.split(":");
      var aWon = parseInt(parts[0], 10) > parseInt(parts[1], 10);
      var won = winner && ((aWon && winner === "a") || (!aWon && winner === "b"));
      wrap.appendChild(el("span", won ? "chip chip-win" : "chip", sc));
    });
    return wrap;
  }

  function versusLine(m) {
    var line = el("div", "vs-line");
    line.appendChild(avatar(m.a, "sm"));
    line.appendChild(el("span", "vs-name" + (m.winner === "a" ? " win" : ""), m.a.name));
    line.appendChild(el("span", "vs-sep", m.winner ? "—" : "ничья"));
    line.appendChild(el("span", "vs-name" + (m.winner === "b" ? " win" : ""), m.b.name));
    line.appendChild(avatar(m.b, "sm"));
    return line;
  }

  // Лента клуба (v2.168.0): матчи (у ярких — репортаж), ачивки, рекорды, трон
  // Лента клуба (v2.171.0): каждое событие — отдельная карточка, заголовок дня
  // прилипает к верху при прокрутке, «Показать ещё» догружает порции по 15.
  function feedItem(it, i) {
    var li;
    var body = el("div", "body");
    if (it.type === "match") {
      li = clickable(el("li", "feed-card feed-match"), "match/" + it.id);
      body.appendChild(versusLine(it));
      var score = el("div", "feed-score");
      score.appendChild(setChips(it.sets, it.winner));
      if (it.delta) score.appendChild(el("span", "feed-delta tone-win", it.delta));
      body.appendChild(score);
      if (it.report) body.appendChild(el("div", "sub feed-report", it.report));
      if (it.reactions && it.reactions.length) body.appendChild(reactionCounts(it.reactions));
    } else if (it.type === "achievement") {
      li = clickable(el("li", "feed-card"), "player/" + it.player.id);
      var badge = el("div", "badge badge-sm " + (it.tier ? "badge-" + it.tier : "badge-earned"));
      badge.appendChild(useIcon(it.icon));
      li.appendChild(badge);
      body.appendChild(el("div", "name", "«" + it.name + "»"));
      body.appendChild(el("div", "sub", it.player.name + " · " + it.desc));
      if (it.target && it.reactions) body.appendChild(feedReactionBar(it.target, it.reactions));
    } else if (it.type === "record") {
      li = it.player ? clickable(el("li", "feed-card"), "player/" + it.player.id) : el("li", "feed-card");
      li.appendChild(icon("trophy", "ico-lead ico-gold"));
      body.appendChild(el("div", "name", it.title));
      body.appendChild(el("div", "sub", [it.player ? it.player.name : "", it.value].filter(Boolean).join(" · ")));
      if (it.target && it.reactions) body.appendChild(feedReactionBar(it.target, it.reactions));
    } else {
      li = clickable(el("li", "feed-card"), "player/" + it.player.id);
      li.appendChild(icon("crown", "ico-lead ico-gold"));
      body.appendChild(el("div", "name", it.player.name + " — на троне"));
      body.appendChild(el("div", "sub", "Новый чемпион клуба"));
    }
    li.style.animationDelay = Math.min(i, 12) * 40 + "ms";
    li.appendChild(body);
    li.appendChild(el("div", "meta feed-time", it.time));
    return li;
  }

  function feedBlock(items, more) {
    var wrap = el("section", "group");
    wrap.appendChild(el("h2", "group-title", "Лента клуба"));
    var list = el("ul", "feed-list");
    wrap.appendChild(list);
    var state = { lastDay: null, offset: 0 };
    function add(batch) {
      batch.forEach(function (it, i) {
        if (it.day !== state.lastDay) {
          state.lastDay = it.day;
          list.appendChild(el("li", "feed-day", it.day));
        }
        list.appendChild(feedItem(it, i));
      });
      state.offset += batch.length;
    }
    add(items);
    if (more) {
      var btn = el("button", "more", "Показать ещё");
      btn.type = "button";
      btn.addEventListener("click", function () {
        haptic("select");
        btn.disabled = true;
        api("feed?offset=" + state.offset).then(function (page) {
          add(page.items);
          if (page.more) btn.disabled = false;
          else btn.remove();
        }, function () { btn.disabled = false; });
      });
      wrap.appendChild(btn);
    }
    return wrap;
  }

  // Меню клуба плиткой (v2.171.0): выше ленты, чтобы до него не листать
  function clubTiles(links) {
    var wrap = el("section", "group");
    wrap.appendChild(el("h2", "group-title", "Клуб"));
    var grid = el("div", "club-tiles");
    links.forEach(function (l) {
      var t = clickable(el("div", "club-tile"), l.route);
      t.appendChild(icon(l.icon || "chevron", "ico-lead"));
      t.appendChild(el("div", "club-tile-title", l.title));
      if (l.value) t.appendChild(el("div", "club-tile-value", l.value));
      grid.appendChild(t);
    });
    wrap.appendChild(grid);
    return wrap;
  }

  // Реакции на матчи (v2.169.0, эксперимент): видны только в приложении,
  // бот о них никому не пишет
  function reactionCounts(list) {
    var wrap = el("div", "reaction-counts");
    list.forEach(function (r) {
      var n = el("span", "reaction-count");
      n.appendChild(useIcon(r.icon));
      n.appendChild(document.createTextNode(String(r.count)));
      wrap.appendChild(n);
    });
    return wrap;
  }

  // Кто поставил (v2.173.0): строка на каждый вид с реакциями — значок и имена
  function reactionWho(items) {
    var wrap = el("div", "reaction-who");
    items.forEach(function (r) {
      if (!r.who || !r.who.length) return;
      var line = el("div", "reaction-who-line");
      line.appendChild(useIcon(r.icon));
      line.appendChild(el("span", "", r.who.join(", ")));
      wrap.appendChild(line);
    });
    return wrap;
  }

  // Одна реакция игрока (v2.174.0): та же снимается, другая заменяет прежнюю —
  // экран меняется сразу, ответ сервера потом уточняет
  function toggledReactions(items, kind) {
    return items.map(function (x) {
      var on = x.kind === kind ? !x.mine : false;
      var count = x.count + (on ? 1 : 0) - (x.mine ? 1 : 0);
      return { kind: x.kind, icon: x.icon, title: x.title, mine: on, count: count, who: x.who };
    });
  }

  // Реакции на ачивку или рекорд прямо в ленте (v2.176.0): маленькие кнопки,
  // нажатие не открывает карточку
  function feedReactionBar(target, list) {
    var bar = el("div", "reactions reactions-mini");
    function draw(items) {
      bar.textContent = "";
      items.forEach(function (r) {
        var b = el("button", "reaction" + (r.mine ? " is-mine" : ""));
        b.type = "button";
        b.setAttribute("aria-label", r.title);
        b.setAttribute("aria-pressed", r.mine ? "true" : "false");
        b.appendChild(useIcon(r.icon));
        if (r.count) b.appendChild(el("span", "reaction-n", String(r.count)));
        b.addEventListener("click", function (e) {
          e.stopPropagation();
          haptic("select");
          draw(toggledReactions(items, r.kind));
          post("feed/react", { target: target, kind: r.kind }).then(function (resp) {
            draw(resp.reactions);
            delete apiCache.home;
          }, function () { draw(items); });
        });
        bar.appendChild(b);
      });
    }
    draw(list);
    bar.addEventListener("keydown", function (e) { e.stopPropagation(); });
    return bar;
  }

  function reactionBar(matchId, list) {
    var box = el("div", "reactions-box");
    var bar = el("div", "reactions");
    var who = el("div");
    box.appendChild(bar);
    box.appendChild(who);
    function draw(items) {
      who.textContent = "";
      who.appendChild(reactionWho(items));
      bar.textContent = "";
      items.forEach(function (r) {
        var b = el("button", "reaction" + (r.mine ? " is-mine" : ""));
        b.type = "button";
        b.setAttribute("aria-label", r.title);
        b.setAttribute("aria-pressed", r.mine ? "true" : "false");
        b.appendChild(useIcon(r.icon));
        if (r.count) b.appendChild(el("span", "reaction-n", String(r.count)));
        b.addEventListener("click", function () {
          haptic("select");
          var next = toggledReactions(items, r.kind);
          draw(next);
          post("match/" + matchId + "/react", { kind: r.kind }).then(function (resp) {
            draw(resp.reactions);
            delete apiCache["match/" + matchId];
            delete apiCache.home;
          }, function () { draw(items); });
        });
        bar.appendChild(b);
      });
    }
    draw(list);
    return box;
  }

  // Подсказка при первом запуске (v2.169.0): одна карточка, закрывается крестиком
  var HINT_KEY = "hint_home_v1";

  function firstRunHint(c) {
    storageGet(HINT_KEY, function (seen) {
      if (seen !== null || !c.isConnected) return;
      var card = el("div", "card hint");
      card.appendChild(useIcon("info-circle", "ico-lead"));
      var b = el("div", "body");
      b.appendChild(el("div", "name", "Это приложение клуба"));
      b.appendChild(el("div", "sub", "Лента, рейтинг, итоги и профили — здесь. Вызвать на матч и записать счёт — в боте, как раньше. Матчи, игроки и ачивки открываются по нажатию."));
      card.appendChild(b);
      var close = el("button", "hint-close");
      close.type = "button";
      close.setAttribute("aria-label", "Закрыть подсказку");
      close.appendChild(useIcon("x"));
      close.addEventListener("click", function () { haptic("select"); storageSet(HINT_KEY, "1"); card.remove(); });
      card.appendChild(close);
      c.insertBefore(card, c.children[1] || null);
    });
  }

  function renderHome(c, d) {
    c.appendChild(header("Клуб сегодня", d.today.label));
    if (d.empty) { emptyState(c, d.empty); return; }
    firstRunHint(c);
    var homeCols = twoCols(c, false);
    var outer = c;
    c = homeCols.main;
    c.appendChild(podium(d.podium));

    var me = clickable(el("div", "card me-card"), "player/" + d.me.id);
    me.appendChild(avatar({ id: d.me.id, initial: d.me.initial }, "md"));
    var mb = el("div", "body");
    mb.appendChild(el("div", "name", "Ты · " + d.me.place));
    if (d.me.week) mb.appendChild(el("div", "sub " + (d.me.week_up ? "tone-win" : "tone-loss"), d.me.week));
    if (d.me.today) mb.appendChild(el("div", "sub", d.me.today));
    me.appendChild(mb);
    me.appendChild(num("me-rating", d.me.rating));
    c.appendChild(me);

    if (d.links.length) c.appendChild(clubTiles(d.links));

    if (d.featured) {
      var f = d.featured.id ? clickable(el("div", "card feature"), "match/" + d.featured.id) : el("div", "card feature");
      var top = el("div", "pair-top");
      top.appendChild(el("span", "eyebrow", d.featured.title));
      top.appendChild(el("span", "eyebrow-meta", [d.featured.date, d.featured.boss ? "боссфайт" : ""].filter(Boolean).join(" · ")));
      f.appendChild(top);
      f.appendChild(versusLine(d.featured));
      f.appendChild(setChips(d.featured.sets, d.featured.winner));
      if (d.featured.reason) f.appendChild(el("p", "feature-reason", d.featured.reason));
      if (d.featured.reactions && d.featured.reactions.length) f.appendChild(reactionCounts(d.featured.reactions));
      c.appendChild(f);
    }

    if (d.feed && d.feed.length) homeCols.side.appendChild(feedBlock(d.feed, d.feed_more));
    if (!homeCols.side.firstChild) outer.classList.remove("is-wide");
  }

