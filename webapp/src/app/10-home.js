  // ── Клуб (главная) ─────────────────────────────────────────────────────────
  // Подиум — советская тумба (v2.200.0, картинка владельца webapp/static/podium.webp,
  // адрес с версией подставляет сервер). Колонки сетки совпадают со ступенями
  // картинки, высота «ступеньки» под игроком — до заднего края верхней грани.
  var PODIUM_ART = "static/podium.webp";

  // v2.201.0: серия от трёх побед — огонь вокруг аватара (рейтинг, подиум)
  var HOT_STREAK = 3;

  function hotAvatar(p, size, id) {
    var a = avatar(id ? { id: id, initial: p.initial } : p, size);
    if (p.streak >= HOT_STREAK) {
      a.classList.add("is-hot");
      a.title = "Серия побед: " + p.streak;
    }
    return a;
  }

  // v2.201.0: место на подиуме сменилось с прошлого захода — аватар переезжает
  // со старой ступени на новую, новичок подиума поднимается снизу
  var PODIUM_SEEN_KEY = "podium_seen_v1";

  function podiumMoves(wrap, items) {
    storageGet(PODIUM_SEEN_KEY, function (raw) {
      if (raw === undefined) return;
      var now = {};
      items.forEach(function (p, i) { if (p && i < 3) now[p.id] = i + 1; });
      var prev = null;
      try { prev = JSON.parse(raw || "null"); } catch (e) { prev = null; }
      setTimeout(function () {
        if (!wrap.isConnected) return;
        storageSet(PODIUM_SEEN_KEY, JSON.stringify(now));
        if (!prev || reduceMotion || !wrap.animate) return;
        Object.keys(now).forEach(function (id) {
          var from = prev[id], to = now[id];
          if (from === to) return;
          var av = wrap.querySelector(".podium-" + to + " .avatar");
          if (!av || !av.animate) return;
          var frames;
          if (from) {
            var anchor = wrap.querySelector(".podium-" + from + " .avatar") || wrap.querySelector(".podium-" + from + " .podium-step");
            if (!anchor) return;
            var a = av.getBoundingClientRect(), b = anchor.getBoundingClientRect();
            var dx = (b.left + b.width / 2) - (a.left + a.width / 2), dy = (b.top + b.height / 2) - (a.top + a.height / 2);
            frames = [{ transform: "translate(" + dx + "px," + dy + "px)" },
              { transform: "translate(" + dx / 2 + "px," + (dy / 2 - 30) + "px)", offset: 0.5 }, { transform: "none" }];
          } else {
            frames = [{ transform: "translateY(60px) scale(.5)", opacity: 0 }, { transform: "none", opacity: 1 }];
          }
          av.animate(frames, { duration: 900, delay: 700, easing: "cubic-bezier(.3, .9, .3, 1)", fill: "backwards" });
        });
      }, 400);
    });
  }

  // v2.201.0: рейтинг изменился с прошлого захода — число досчитывается от старого
  // значения, над ним всплывает разница
  var ME_RATING_KEY = "me_rating_seen_v1";

  function meRating(card, rating) {
    var node = el("div", "me-rating");
    var done = false;
    function plain() { if (!done) { done = true; countUp(node, rating); } }
    var fallback = setTimeout(plain, 800);
    storageGet(ME_RATING_KEY, function (raw) {
      clearTimeout(fallback);
      var before = parseFloat(raw), after = parseFloat(rating);
      var changed = raw && !isNaN(before) && !isNaN(after) && Math.abs(after - before) >= 0.05 &&
        !app.classList.contains("quiet");
      if (!changed || done) plain();
      else {
        done = true;
        countBetween(node, before, after, 1);
        var diff = after - before;
        var bubble = el("span", "me-bubble " + (diff > 0 ? "tone-win" : "tone-loss"),
          (diff > 0 ? "+" : "−") + Math.abs(diff).toFixed(1));
        card.appendChild(bubble);
        setTimeout(function () { bubble.remove(); }, 2600);
      }
      if (raw === undefined) return;
      setTimeout(function () { if (card.isConnected) storageSet(ME_RATING_KEY, rating); }, 400);
    });
    return node;
  }

  // v2.202.0: первые дни месяца — карточка «Твой октябрь готов» (закрыл — больше не показывается)
  var MONTH_READY_KEY = "month_ready_seen_v1";

  function monthReady(c, anchor, m) {
    storageGet(MONTH_READY_KEY, function (seen) {
      if (seen === m.ym || !anchor.isConnected) return;
      var card = clickable(el("div", "card hint month-ready"), "month/" + m.ym);
      card.appendChild(icon("calendar-event", "ico-lead"));
      var b = el("div", "body");
      b.appendChild(el("div", "name", m.title));
      b.appendChild(el("div", "sub", m.sub));
      card.appendChild(b);
      var close = el("button", "hint-close");
      close.type = "button";
      close.setAttribute("aria-label", "Скрыть");
      close.appendChild(useIcon("x"));
      close.addEventListener("click", function (e) {
        e.stopPropagation();
        haptic("select");
        storageSet(MONTH_READY_KEY, m.ym);
        card.remove();
      });
      card.appendChild(close);
      card.addEventListener("click", function () { storageSet(MONTH_READY_KEY, m.ym); });
      anchor.parentNode.insertBefore(card, anchor);
    });
  }

  function podium(items) {
    var wrap = el("div", "podium podium-stand");
    var art = el("img", "podium-art");
    art.alt = "";
    art.setAttribute("aria-hidden", "true");
    art.decoding = "async";
    art.src = PODIUM_ART;
    wrap.appendChild(art);
    [1, 0, 2].forEach(function (idx) {
      var p = items[idx];
      var place = idx + 1;
      var col = p ? clickable(el("div", "podium-col podium-" + place), "player/" + p.id) : el("div", "podium-col podium-" + place);
      if (p) {
        var crownSlot = el("div", "crown-slot");
        if (p.champion) crownSlot.appendChild(icon("crown", "crown"));
        col.appendChild(crownSlot);
        col.appendChild(hotAvatar(p, place === 1 ? "xl" : "lg"));
        col.appendChild(el("div", "podium-name", p.name));
        col.appendChild(num("podium-rating", p.rating));
      }
      col.appendChild(el("div", "podium-step"));
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

  // Игроки матча в ленте — по строке на каждого (v2.187.0): в одну строку длинные
  // имена переносились, и аватар второго игрока уезжал один на следующую строку
  function feedPlayers(m) {
    var wrap = el("div", "feed-players");
    [["a", m.a], ["b", m.b]].forEach(function (x) {
      var row = el("div", "feed-player");
      row.appendChild(avatar(x[1], "sm"));
      row.appendChild(el("span", "vs-name" + (m.winner === x[0] ? " win" : ""), x[1].name));
      wrap.appendChild(row);
    });
    return wrap;
  }

  // Лента клуба (v2.168.0): матчи (у ярких — репортаж), ачивки, рекорды, трон
  // Лента клуба (v2.171.0): каждое событие — отдельная карточка, заголовок дня
  // прилипает к верху при прокрутке, «Показать ещё» догружает порции по 15.
  // v2.185.0: метка характера матча (как штамп на экране матча) и пометка,
  // что этот матч уже показан выше карточкой «Матч дня»
  function feedTags(it, featured) {
    var tags = [];
    var mood = matchMood({ winner: it.winner, boss: it.boss, sets: chipSets(it.sets) });
    if (featured && featured.id === it.id) tags.push(["feed-tag", featured.title]);
    // v2.201.0: «Отыгрался» в ленте — почти у каждого 2:1, поэтому только на экране матча
    if (mood && mood !== "rally") tags.push(["feed-tag mood-tag mood-" + mood, MOOD_LABELS[mood]]);
    if (!tags.length) return null;
    var wrap = el("div", "feed-tags");
    tags.forEach(function (t) { wrap.appendChild(el("span", t[0], t[1])); });
    return wrap;
  }

  function feedItem(it, i, featured) {
    var li;
    var body = el("div", "body");
    if (it.type === "match") {
      li = clickable(el("li", "feed-card feed-match"), "match/" + it.id);
      var tags = feedTags(it, featured);
      if (tags) body.appendChild(tags);
      body.appendChild(feedPlayers(it));
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

  function feedBlock(items, more, featured) {
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
        list.appendChild(feedItem(it, i, featured));
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
  // v2.192.0: плитки — по тому, что клуб чаще открывает (порядок с сервера,
  // bot/webapp/menu.py); редкие — под «Ещё», раскрытие запоминается на устройстве
  var CLUB_MORE_KEY = "club_more_v1";

  function clubTiles(links) {
    var wrap = el("section", "group club-group");
    wrap.appendChild(el("h2", "group-title", "Клуб"));
    var grid = el("div", "club-tiles");
    var extra = 0;
    links.forEach(function (l) {
      var t = clickable(el("div", "club-tile" + (l.more ? " club-tile-more" : "")), l.route);
      t.appendChild(icon(l.icon || "chevron", "ico-lead"));
      t.appendChild(el("div", "club-tile-title", l.title));
      if (l.value) t.appendChild(el("div", "club-tile-value", l.value));
      if (l.more) extra += 1;
      grid.appendChild(t);
    });
    wrap.appendChild(grid);
    if (extra) {
      var open = false;
      try { open = window.localStorage.getItem(CLUB_MORE_KEY) === "1"; } catch (e) { /* закрыто */ }
      var btn = el("button", "more club-more");
      btn.type = "button";
      var setOpen = function (v) {
        open = v;
        wrap.classList.toggle("is-open", v);
        btn.textContent = v ? "Свернуть" : "Ещё " + extra;
        btn.setAttribute("aria-expanded", v ? "true" : "false");
      };
      btn.addEventListener("click", function () {
        haptic("select");
        setOpen(!open);
        try { window.localStorage.setItem(CLUB_MORE_KEY, open ? "1" : "0"); } catch (e) { /* без памяти */ }
      });
      setOpen(open);
      wrap.appendChild(btn);
    }
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

  // «Пружинка» реакции (v2.180.0): кнопка сжимается и отпружинивает, над
  // поставленной реакцией всплывает её значок
  function reactionPop(bar, r, next) {
    var nb = bar.querySelector('[data-kind="' + r.kind + '"]');
    if (!nb) return;
    nb.classList.add("pop");
    var now = next.filter(function (x) { return x.kind === r.kind; })[0];
    if (!now || !now.mine) return;
    var fly = el("span", "reaction-fly");
    fly.appendChild(useIcon(r.icon));
    nb.appendChild(fly);
    setTimeout(function () { if (fly.parentNode) fly.parentNode.removeChild(fly); }, 800);
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
        b.setAttribute("data-kind", r.kind);
        b.appendChild(useIcon(r.icon));
        if (r.count) b.appendChild(el("span", "reaction-n", String(r.count)));
        b.addEventListener("click", function () {
          haptic("select");
          var next = toggledReactions(items, r.kind);
          draw(next);
          reactionPop(bar, r, next);
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

  // «Вызвать на матч» (v2.179.0): вызов делается в боте, как и раньше. Кнопка
  // просит бота прислать экран вызова в чат (с выбранным соперником —
  // подтверждение) и закрывает приложение — игрок сразу у нужного экрана.
  function closeApp() {
    try { if (tg && tg.close) { tg.close(); return true; } } catch (e) { /* ниже — напрямую */ }
    try {
      if (window.TelegramWebviewProxy) { window.TelegramWebviewProxy.postEvent("web_app_close", JSON.stringify("")); return true; }
      if (window.parent !== window) { window.parent.postMessage(JSON.stringify({ eventType: "web_app_close", eventData: "" }), "*"); return true; }
    } catch (e) { /* не в Telegram */ }
    return false;
  }

  function challengeButton(opponentId) {
    var b = el("button", "button challenge-btn");
    b.type = "button";
    b.appendChild(icon("swords", "ico-btn"));
    b.appendChild(document.createTextNode("Вызвать на матч"));
    b.addEventListener("click", function () {
      haptic("impact");
      b.disabled = true;
      post("challenge/open", opponentId ? { opponent_id: Number(opponentId) } : {}).then(function () {
        if (!closeApp()) sheetOpen(null, "Вызов — в чате с ботом", "Открой чат с ботом: там уже ждёт экран вызова.", "");
      }, function (err) {
        sheetOpen(null, "Не получилось", err.message, "");
      }).then(function () { b.disabled = false; });
    });
    return b;
  }

  function renderHome(c, d) {
    c.appendChild(header("Клуб сегодня", d.today.label));
    if (d.empty) { emptyState(c, d.empty); return; }
    firstRunHint(c);
    checkSince(c);
    var homeCols = twoCols(c, false);
    var outer = c;
    c = homeCols.main;
    var podiumNode = podium(d.podium);
    c.appendChild(podiumNode);
    podiumMoves(podiumNode, d.podium);
    checkMeDot(d.me);
    if (d.month_ready) monthReady(c, podiumNode, d.month_ready);

    var me = clickable(el("div", "card me-card"), "player/" + d.me.id);
    me.appendChild(avatar({ id: d.me.id, initial: d.me.initial }, "md"));
    var mb = el("div", "body");
    mb.appendChild(el("div", "name", "Ты · " + d.me.place));
    if (d.me.week) mb.appendChild(el("div", "sub " + (d.me.week_up ? "tone-win" : "tone-loss"), d.me.week));
    if (d.me.today) mb.appendChild(el("div", "sub", d.me.today));
    me.appendChild(mb);
    me.appendChild(meRating(me, d.me.rating));
    c.appendChild(me);
    c.appendChild(challengeButton(null));
    if (d.goals) c.appendChild(goalsBlock(d.goals));

    if (d.links.length) c.appendChild(clubTiles(d.links));
    homeIconOffer(c);

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

    if (d.feed && d.feed.length) homeCols.side.appendChild(feedBlock(d.feed, d.feed_more, d.featured));
    if (!homeCols.side.firstChild) outer.classList.remove("is-wide");
  }

