  // ── Поводы заглянуть (v2.181.0): «Пока тебя не было», «Цели недели», иконка
  // клуба на экране телефона. Ни одного уведомления — всё только в приложении.

  // «Цели недели»: три личные цели, кольцо прогресса, «недель подряд»
  function goalRing(progress, target, done) {
    var R = 15, C = 2 * Math.PI * R;
    var s = svg("svg", { "class": "goal-ring" + (done ? " is-done" : ""), viewBox: "0 0 36 36", "aria-hidden": "true" });
    s.appendChild(svg("circle", { "class": "goal-ring-bg", cx: 18, cy: 18, r: R }));
    var arc = svg("circle", { "class": "goal-ring-arc", cx: 18, cy: 18, r: R,
      "stroke-dasharray": C.toFixed(1), "stroke-dashoffset": C.toFixed(1) });
    s.appendChild(arc);
    // кольцо «наливается» после появления на экране
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        arc.setAttribute("stroke-dashoffset", (C * (1 - Math.min(1, progress / target))).toFixed(1));
      });
    });
    if (done) {
      var check = svg("path", { "class": "goal-ring-check", d: "M12 18.5l4 4 8-9" });
      s.appendChild(check);
    }
    return s;
  }

  // Праздник закрытой цели (v2.200.0): один раз на цель за неделю — галочка
  // прочерчивается, от кольца разлетаются мячики; все три закрыты — конфетти
  // и счётчик недель подряд щёлкает на +1. Что уже показано — в памяти устройства.
  var GOALS_SEEN_KEY = "goals_seen_v1";

  // Чистая часть (проверяется в Node): что праздновать и что запомнить
  function goalsFresh(stored, week, goals, allDone) {
    var parts = String(stored || "").split("|");
    var same = parts[0] === week;
    var seen = same && parts[1] ? parts[1].split(",") : [];
    var seenAll = same && parts[2] === "all";
    var fresh = goals.filter(function (g) { return g.done && seen.indexOf(g.key) < 0; })
      .map(function (g) { return g.key; });
    var done = goals.filter(function (g) { return g.done; }).map(function (g) { return g.key; });
    return { fresh: fresh, all: !!allDone && !seenAll,
             store: week + "|" + done.join(",") + "|" + (allDone ? "all" : "") };
  }

  function celebrateGoals(wrap, g) {
    if (!g.week) return;
    storageGet(GOALS_SEEN_KEY, function (raw) {
      if (raw === undefined) return;                  // памяти нет — без праздника
      var r = goalsFresh(raw, g.week, g.goals, g.all_done);
      if (!r.fresh.length && !r.all) return;
      // главная могла сразу перерисоваться свежими данными (старт из снимка) —
      // празднуем только то, что осталось на экране
      setTimeout(function () {
        if (!wrap.isConnected) return;
        storageSet(GOALS_SEEN_KEY, r.store);
        r.fresh.forEach(function (key, i) {
          var row = wrap.querySelector('[data-goal="' + key + '"]');
          if (!row) return;
          row.classList.add("is-fresh");
          setTimeout(function () {
            hapticSuccess();
            ballBurst(row.querySelector(".goal-ring"));
          }, (reduceMotion ? 0 : 750) + i * 250);
        });
        if (!r.all) return;
        var foot = wrap.querySelector(".goals-foot");
        var count = wrap.querySelector(".goals-streak");
        setTimeout(function () {
          if (count) {
            count.textContent = String(Math.max(0, g.streak - 1));
            setTimeout(function () { count.textContent = String(g.streak); count.classList.add("is-flip"); },
              reduceMotion ? 0 : 350);
          }
          if (foot) { foot.classList.add("is-fresh"); confetti(foot); }
          hapticSuccess();
        }, reduceMotion ? 0 : 900 + r.fresh.length * 250);
      }, 400);
    });
  }

  function goalsBlock(g) {
    var wrap = el("section", "group goals");
    var head = el("div", "group-head");
    head.appendChild(el("h2", "group-title", g.title));
    head.appendChild(el("span", "group-count", g.left));
    wrap.appendChild(head);
    var list = el("ul", "list");
    g.goals.forEach(function (goal) {
      var li = goal.player_id ? clickable(el("li", "row goal-row"), "player/" + goal.player_id) : el("li", "row goal-row");
      if (goal.done) li.classList.add("is-done");
      li.setAttribute("data-goal", goal.key);
      li.appendChild(goalRing(goal.progress, goal.target, goal.done));
      var body = el("div", "body");
      body.appendChild(el("div", "name", goal.title));
      if (goal.note) body.appendChild(el("div", "sub", goal.note));
      li.appendChild(body);
      li.appendChild(el("div", "meta goal-count", goal.done ? "готово" : goal.progress + " / " + goal.target));
      list.appendChild(li);
    });
    wrap.appendChild(list);
    if (g.all_done || g.streak) {
      var foot = el("p", "footnote goals-foot" + (g.all_done ? " is-done" : ""));
      if (g.all_done) foot.appendChild(document.createTextNode("Неделя закрыта"));
      if (g.streak) {
        foot.appendChild(document.createTextNode((g.all_done ? " · " : "") + "Закрыто недель подряд: "));
        foot.appendChild(el("span", "goals-streak", String(g.streak)));
      }
      wrap.appendChild(foot);
    }
    celebrateGoals(wrap, g);
    return wrap;
  }

  // «Пока тебя не было»: что изменилось с прошлого захода. Время захода —
  // в памяти устройства (CloudStorage — общая для телефона и компьютера).
  // Карточка — не чаще, чем раз в SINCE_MIN_GAP_MS, и один раз за запуск.
  var VISIT_KEY = "last_visit_v1";
  var SINCE_MIN_GAP_MS = 2 * 3600 * 1000;
  var sinceChecked = false;
  // v2.192.0: при старте из снимка главной (v2.180.0) ответ мог прийти раньше,
  // чем главная перерисуется свежими данными, — карточка исчезала вместе со
  // снимком. Теперь данные запоминаются, и карточка ставится на каждую
  // отрисовку главной, пока её не закрыли и пока игрок не ушёл с главной.
  var sinceState = null;

  // Картинка заставки (v2.188.0) — та же, что в index.html, с тем же адресом
  // (версия из ссылки preload), поэтому уже в кэше браузера
  function splashUrl() {
    var pre = document.querySelector('link[rel="preload"][as="image"]');
    return pre ? pre.getAttribute("href") : "static/splash.webp";
  }

  function sinceCard(c, d) {
    var card = el("div", "card since");
    // v2.192.0: «С возвращением» с картинкой заставки — её почти никто не видел,
    // повторный запуск слишком быстрый
    var hero = el("div", "since-hero");
    var txt = el("div", "since-hero-text");
    txt.appendChild(el("div", "since-hello", "С возвращением!"));
    var top = el("div", "since-meta");
    top.appendChild(el("span", "eyebrow", d.title));
    top.appendChild(el("span", "eyebrow-meta", d.since));
    txt.appendChild(top);
    hero.appendChild(txt);
    var pic = el("img", "since-pic");
    pic.alt = "";
    pic.decoding = "async";
    pic.addEventListener("error", function () { pic.remove(); });
    pic.src = splashUrl();
    hero.appendChild(pic);
    card.appendChild(hero);
    var close = el("button", "hint-close");
    close.type = "button";
    close.setAttribute("aria-label", "Скрыть");
    close.appendChild(useIcon("x"));
    close.addEventListener("click", function () { haptic("select"); if (sinceState) sinceState.closed = true; card.remove(); });
    card.appendChild(close);
    var list = el("ul", "list list-plain");
    d.items.forEach(function (it) { list.appendChild(itemRow(it)); });
    card.appendChild(list);
    var anchor = c.querySelector(".podium") || c.children[1] || null;
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(card, anchor);
    else c.appendChild(card);
  }

  function checkSince(c) {
    if (sinceState && !sinceState.closed && !c.querySelector(".since")) sinceCard(c, sinceState.d);
    if (sinceChecked) return;
    sinceChecked = true;
    window.addEventListener("hashchange", function leave() {
      if (location.hash.replace(/^#\/?/, "") === "") return;
      if (sinceState) sinceState.closed = true;
      window.removeEventListener("hashchange", leave);
    });
    var now = Date.now();
    storageGet(VISIT_KEY, function (raw) {
      if (raw === undefined) return;                  // памяти нет — без карточки
      storageSet(VISIT_KEY, String(now));
      var last = parseInt(raw, 10);
      if (!last || now - last < SINCE_MIN_GAP_MS) return;
      api("since?t=" + Math.floor(last / 1000)).then(function (d) {
        if (!d || !d.items || !d.items.length) return;
        sinceState = { d: d, closed: false };
        if (c.isConnected && !c.querySelector(".since")) sinceCard(c, d);
      }, function () { /* не вышло — просто без карточки */ });
    });
  }

  // Иконка клуба на экране телефона (Telegram 8.0+): предложить один раз, где
  // клиент это умеет и иконки ещё нет
  var HOME_ICON_KEY = "home_icon_hint_v1";

  function homeIconOffer(c) {
    if (!tg || !tg.checkHomeScreenStatus || !tg.addToHomeScreen) return;
    try { if (!tg.isVersionAtLeast || !tg.isVersionAtLeast("8.0")) return; } catch (e) { return; }
    storageGet(HOME_ICON_KEY, function (seen) {
      if (seen !== null) return;
      try {
        tg.checkHomeScreenStatus(function (status) {
          if (status !== "missed" || !c.isConnected) return;
          var card = el("div", "card hint home-icon");
          card.appendChild(useIcon("device-mobile", "ico-lead"));
          var b = el("div", "body");
          b.appendChild(el("div", "name", "Иконка клуба на экране телефона"));
          b.appendChild(el("div", "sub", "Приложение будет открываться в один тап, без поиска бота."));
          var add = el("button", "button button-quiet home-icon-add", "Добавить");
          add.type = "button";
          add.addEventListener("click", function () {
            haptic("impact");
            try { tg.addToHomeScreen(); } catch (e) { /* клиент передумал */ }
          });
          b.appendChild(add);
          card.appendChild(b);
          var close = el("button", "hint-close");
          close.type = "button";
          close.setAttribute("aria-label", "Закрыть");
          close.appendChild(useIcon("x"));
          close.addEventListener("click", function () { haptic("select"); storageSet(HOME_ICON_KEY, "1"); card.remove(); });
          card.appendChild(close);
          try {
            tg.onEvent("homeScreenAdded", function () { storageSet(HOME_ICON_KEY, "1"); card.remove(); });
          } catch (e) { /* старый клиент */ }
          var tiles = c.querySelector(".club-tiles");
          var anchor = tiles ? tiles.parentNode : null;
          if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(card, anchor.nextSibling);
          else c.appendChild(card);
        });
      } catch (e) { /* не поддерживается */ }
    });
  }

