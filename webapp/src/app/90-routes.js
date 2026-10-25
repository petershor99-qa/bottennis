  // ── Маршруты ───────────────────────────────────────────────────────────────
  // [шаблон адреса, вкладка снизу, загрузка данных, отрисовка]
  function one(path) { return function () { return api(path); }; }

  var PLAYER_ROUTE = /^player\/(\d+)(?:\/(matches|achievements|style))?$/;

  var routes = [
    [/^$/, "home", one("home"), renderHome, "home"],
    [/^rating$/, "rating", one("leaderboard"), renderLeaderboard, "rating"],
    [/^h2h\/(\d+)$/, null, function (m) { return api("h2h/" + m[1]); }, function (c, d) { renderVersus(c, d); }, "h2h"],
    // v2.193.0: любая пара — «A против B» глазами третьего
    [/^pair\/(\d+)\/(\d+)$/, null, function (m) { return api("pair/" + m[1] + "/" + m[2]); }, function (c, d) { renderVersus(c, d); }, "h2h"],
    [/^records$/, "home", one("records"), renderRecords, "records"],
    [/^matches$/, "home", one("matches"), renderClubMatches, "matches"],
    [/^activity\/club$/, "home", one("activity/club"), renderClubActivity, "activity"],
    [/^throne$/, "home", one("throne"), renderThrone, "throne"],
    // Итоги дня (v2.176.0) — на лету за любой день: summary/day/ГГГГММДД
    [/^summary\/(day|week|month|quarter|year)(?:\/(\d{8}))?$/, "home", function (m) {
      var date = m[2] ? m[2].slice(0, 4) + "-" + m[2].slice(4, 6) + "-" + m[2].slice(6) : "";
      if (m[1] !== "day") return api("summary/" + m[1] + (date ? "?date=" + date : ""));
      return Promise.all([api("summary/day"), api("day" + (date ? "?date=" + date : ""))])
        .then(function (r) { return { tabs: r[0].tabs, day: r[1] }; });
    }, function (c, d, m) { renderSummary(c, d, m[1]); }, "summary"],
    [/^match\/(\d+)$/, null, function (m) { return api("match/" + m[1]); }, function (c, d) { renderMatch(c, d); }, "match"],
    [/^when(?:\/(me))?$/, "home", function (m) { return api("when?who=" + (m[1] || "club")); },
      function (c, d, m) { renderWhen(c, d, m[1] || "club"); }, "when"],
    [/^usage$/, "home", one("usage"), renderUsage, "usage"],
    // v2.212.0: «Рассылки» — переключатели автосводок (ссылка с «Я»)
    [/^notifications$/, "home", one("notifications"), renderNotifications, "notifications"],
    [/^pairs$/, "home", one("pairs"), renderPairs, "pairs"],
    [/^race$/, "home", one("race"), renderRace, "race"],
    // v2.177.0: летопись, счета клуба, «Твой месяц»
    [/^chronicle$/, "home", one("chronicle"), renderChronicle, "chronicle"],
    [/^scores$/, "home", one("scores"), renderScores, "scores"],
    [/^month(?:\/(\d{6}))?$/, "me", function (m) { return api("month" + (m[1] ? "?ym=" + m[1] : "")); },
      function (c, d) { renderMonth(c, d); }, "month"]
  ];

  var TABS = [["home", "", "Клуб", "home"], ["rating", "rating", "Рейтинг", "rating"], ["me", "me", "Я", "user"]];

  function drawTabbar(active) {
    tabbar.textContent = "";
    TABS.forEach(function (t) {
      var b = el("button", "tab tab-" + t[0] + (t[0] === active ? " is-active" : "") + (t[0] === "me" && meDot ? " has-dot" : ""));
      b.type = "button";
      b.appendChild(icon(t[3]));
      b.appendChild(el("span", "tab-label", t[2]));
      b.addEventListener("click", function () {
        haptic("select");
        var target = t[1] === "me" ? "player/" + viewerId : t[1];
        if (currentRoute() === target) {
          // v2.190.0: уже наверху — обновить экран (значок вкладки крутится, пока идёт запрос)
          if (window.scrollY > 4) { window.scrollTo({ top: 0, behavior: "smooth" }); return; }
          b.classList.add("is-refreshing");
          refresh().then(function () { b.classList.remove("is-refreshing"); });
          return;
        }
        location.hash = "#/" + target;
      });
      tabbar.appendChild(b);
    });
  }

  // Чистая часть (проверяется в Node): адрес профиля вместо «против себя» или null
  function selfVersusTarget(route, viewer) {
    var h = /^h2h\/(\d+)$/.exec(route);
    if (h && viewer !== null && viewer !== undefined && h[1] === String(viewer)) return "player/" + h[1];
    var p = /^pair\/(\d+)\/(\d+)$/.exec(route);
    if (p && p[1] === p[2]) return "player/" + p[1];
    return null;
  }

  function currentRoute() { return location.hash.replace(/^#\/?/, ""); }

  // Позиция прокрутки — по записи истории (v2.201.0), а не по адресу. Переход по
  // ссылке создаёт новую запись — экран сверху, даже если этот адрес уже был в
  // истории (матч → профиль → тот же матч открывался с середины). «Назад»
  // (кнопка Telegram, свайп) возвращает на запись со своим id — на то же место.
  var scrollPos = {};
  var renderedRoute = null;
  var renderedNav = null;
  var currentNav = { id: null, restore: false };
  var navCounter = 0;
  try { if ("scrollRestoration" in history) history.scrollRestoration = "manual"; } catch (e) { /* старый браузер */ }

  function navEntry() {
    var st = history.state;
    if (st && st.navId) return { id: st.navId, restore: true };
    var id = Date.now().toString(36) + "-" + (++navCounter);
    try { history.replaceState({ navId: id }, "", location.href); } catch (e) { /* без истории — всегда сверху */ }
    return { id: id, restore: false };
  }

  function enterScreen(route, quiet) {
    app.setAttribute("data-enter", quiet === true ? "none" : navDirection(route));
  }

  function finishRender(route, keepScroll) {
    renderedRoute = route;
    renderedNav = currentNav.id;
    track(route);
    if (keepScroll) return;
    var id = currentNav.id, target = currentNav.restore ? (scrollPos[id] || 0) : 0;
    window.scrollTo(0, target);
    // v2.201.1: мобильный Chrome (Telegram на Android) после смены содержимого мог
    // вернуть прежнюю прокрутку — экран открывался с середины. Повтор после кадра.
    requestAnimationFrame(function () {
      if (renderedNav === id && Math.abs(window.scrollY - target) > 2) window.scrollTo(0, target);
    });
  }

  function render(quiet) {
    var route = currentRoute();
    app.classList.toggle("quiet", quiet === true);
    var nav = navEntry();
    if (renderedNav !== null && renderedNav !== nav.id) scrollPos[renderedNav] = window.scrollY;
    currentNav = nav;
    // новый экран — наверх сразу, до каркаса загрузки (не после: браузер успевал «запомнить» середину)
    if (!nav.restore) window.scrollTo(0, 0);
    if (route === "me") { location.replace("#/player/" + viewerId); return; }
    // v2.204.0: «Ты против себя» и пара из одного игрока — на профиль, а не экран ошибки
    var selfPair = selfVersusTarget(route, viewerId);
    if (selfPair) { location.replace("#/" + selfPair); return; }
    // «Твой месяц» во весь экран (v2.183.0); остальные экраны — если так выбрано (v2.186.0)
    if (!/^month/.test(route) && !wantFullscreen()) leaveFullscreen();

    var token = ++renderToken;
    var pm = route.match(PLAYER_ROUTE);

    if (pm) {
      var playerId = pm[1], tab = pm[2] || "";
      drawTabbar(playerId === String(viewerId) ? "me" : null);
      var isOwn = route === "player/" + viewerId;
      if (tg && tg.BackButton) { if (isOwn) tg.BackButton.hide(); else tg.BackButton.show(); }
      if (shell && shell.playerId === playerId && app.contains(shell.body)) {
        // тот же игрок — переключаем только раздел, шапку не трогаем
        navDirection(route);   // история переходов знает и разделы профиля (v2.182.0)
        setSegment(tab);
        loadTab(tab, token);
        renderedRoute = route;
        renderedNav = currentNav.id;
        track(route);
        return;
      }
      shell = null;
      app.textContent = "";
      app.classList.remove("is-wide");
      enterScreen(route, quiet);
      app.appendChild(skeleton("profile"));
      api("player/" + playerId).then(function (d) {
        if (token !== renderToken) return;
        app.textContent = "";
        buildShell(app, d, playerId);
        setSegment(tab);
        loadTab(tab, token);
        finishRender(route, false);
      }, function (err) {
        if (token !== renderToken) return;
        app.textContent = "";
        emptyState(app, err.message, true, err);
      });
      return;
    }

    var match = null, entry = null;
    for (var i = 0; i < routes.length && !match; i++) { match = route.match(routes[i][0]); entry = routes[i]; }
    if (!match) { location.replace("#/"); return; }

    shell = null;
    var tabName = entry[1];
    drawTabbar(tabName);
    var isRoot = route === "" || route === "rating";
    if (tg && tg.BackButton) { if (isRoot) tg.BackButton.hide(); else tg.BackButton.show(); }

    app.textContent = "";
    app.classList.remove("is-wide");
    enterScreen(route, quiet);
    app.appendChild(skeletonFor(route));
    entry[2](match).then(function (data) {
      if (token !== renderToken) return;
      app.textContent = "";
      entry[3](app, data, match);
      if (route === "") saveHomeSnapshot(data);
      finishRender(route, false);
    }, function (err) {
      if (token !== renderToken) return;
      app.textContent = "";
      emptyState(app, err.message, true, err);
    });
  }

  // Автообновление (v2.169.0): вернулись в приложение спустя минуту — данные
  // запрашиваются заново, экран обновляется на месте: без скелета, анимаций и
  // со своей прокруткой. Нет связи — остаётся прежний экран.
  var REFRESH_AFTER_MS = 60 * 1000;
  var hiddenAt = null;

  function routeJobs(route) {
    var pm = route.match(PLAYER_ROUTE);
    if (pm) {
      var id = pm[1], tab = pm[2] || "", jobs = [api("player/" + id)];
      if (tab === "matches") jobs.push(api("player/" + id + "/history"));
      else if (tab === "achievements") jobs.push(api("player/" + id + "/achievements"));
      else if (tab === "style") jobs.push(api("player/" + id + "/radar"), api("player/" + id + "/activity"));
      return jobs;
    }
    for (var i = 0; i < routes.length; i++) {
      var m = route.match(routes[i][0]);
      if (m) return [routes[i][2](m)];
    }
    return [];
  }

  function refresh() {
    var route = currentRoute(), y = window.scrollY;
    apiCache = {};
    delete photoCache[viewerId];  // фото могли сменить в боте, пока приложение было свёрнуто
    return Promise.all(routeJobs(route)).then(function () {
      if (currentRoute() !== route || document.querySelector(".sheet-wrap")) return;
      shell = null;
      if (renderedNav !== null) scrollPos[renderedNav] = y;
      render(true);
      requestAnimationFrame(function () { window.scrollTo(0, y); });
    }, function () { /* нет связи — оставляем как было */ });
  }

  function onHidden() { if (hiddenAt === null) hiddenAt = Date.now(); }

  function onShown() {
    if (hiddenAt === null) return;
    var away = Date.now() - hiddenAt;
    hiddenAt = null;
    if (away >= REFRESH_AFTER_MS && viewerId !== null) refresh();
  }

  document.addEventListener("visibilitychange", function () { if (document.hidden) onHidden(); else onShown(); });

  // Скрипт Telegram пришёл (сразу или позже страницы) — подключить его
  function attachTelegram() {
    var w = window.Telegram && window.Telegram.WebApp;
    if (!w || w === attachTelegram.done) return;
    attachTelegram.done = w;
    tg = w;
    if (window.performance) telegramAt = Math.round(performance.now());
    applyTheme();
    try {
      tg.ready();
      tg.expand();
      // Свайп вниз при прокрутке не закрывает приложение (Telegram 7.7+, v2.171.0)
      if (tg.isVersionAtLeast && tg.isVersionAtLeast("7.7") && tg.disableVerticalSwipes) tg.disableVerticalSwipes();
      tg.onEvent("themeChanged", function () { applyTheme(); shell = null; render(true); });
      tg.onEvent("fullscreenChanged", syncFullscreenClass);
      // v2.203.1: отступ под камерой — и когда Telegram открыл приложение сразу во весь
      // экран (события fullscreenChanged тогда нет) или поменял безопасную зону
      tg.onEvent("safeAreaChanged", syncFullscreenClass);
      tg.onEvent("contentSafeAreaChanged", syncFullscreenClass);
      setupFullscreen();
      if (tg.BackButton) {
        tg.BackButton.onClick(appBack);
        if (renderedRoute !== null && renderedRoute !== "" && renderedRoute !== "rating" &&
            renderedRoute !== "player/" + viewerId) tg.BackButton.show();
      }
      // Свернули/развернули приложение (Telegram 8.0+) — то же, что смена видимости
      tg.onEvent("deactivated", onHidden);
      tg.onEvent("activated", onShown);
    } catch (e) { /* старый клиент — работаем без этих возможностей */ }
  }

  applyTheme();
  attachTelegram();
  window.__clubOnTelegram = attachTelegram;
  // Адрес запуска с параметрами Telegram — сразу на главную (или экран из ?r=),
  // чтобы маршруты не путались с ними; параметры уже сохранены выше
  if (location.hash && location.hash.indexOf("#/") !== 0 && launch.tgWebAppData) {
    try { history.replaceState(null, "", location.pathname + location.search + "#/"); } catch (e) { /* старый браузер */ }
  }
  window.addEventListener("hashchange", function () {
    var target = relaunchRoute(location.hash, location.search);
    if (target !== null) {
      // повторный запуск: свежие параметры Telegram и экран из ?r= (было — «Клуб»)
      launch = parseLaunchParams(location.hash);
      try { window.sessionStorage.setItem("__telegram__initParams", JSON.stringify(launch)); } catch (e) { /* нет памяти */ }
      try { history.replaceState(null, "", location.pathname + location.search + "#/" + target); } catch (e) { /* старый браузер */ }
    }
    render();
  });
  // Переход из бота сразу на нужный экран (v2.167.0): ?r=player/5, ?r=rating&from=3.
  try {
    var params = new URLSearchParams(location.search);
    var startRoute = params.get("r");
    if (params.has("from")) pendingRise = parseInt(params.get("from"), 10) || null;
    if (startRoute !== null && /^[a-z0-9_\/]{0,60}$/.test(startRoute) && (location.hash === "#/" || location.hash.indexOf("#/") !== 0)) {
      history.replaceState(null, "", location.pathname + location.search + "#/" + startRoute);
    }
  } catch (e) { /* старый браузер — откроется главная */ }

  // Пока игрок смотрит первый экран, подгружаем «Рейтинг» и свой профиль —
  // переход по нижнему меню открывается сразу (v2.172.0). Кэш ответов — минута.
  function prefetchTabs() {
    if (viewerId === null) return;
    setTimeout(function () {
      ["leaderboard", "player/" + viewerId, "player/" + viewerId + "/chart"].forEach(function (path) {
        api(path).catch(function () { /* не вышло — экран загрузит сам */ });
      });
    }, 1200);
  }

  // Мгновенный старт (v2.180.0): последняя главная хранится на устройстве
  // (localStorage, привязка к игроку, не старше SNAP_MAX_AGE_MS). При запуске на
  // главную она показывается сразу, со строкой «Обновляется…», а свежие данные
  // заменяют её на месте — без скелета и анимаций. Нет связи — остаётся снимок
  // с пометкой, от какого он времени.
  var SNAP_KEY = "snap_home_v1";
  var SNAP_MAX_AGE_MS = 3 * 24 * 3600 * 1000;

  function launchUserId() {
    try { return JSON.parse(new URLSearchParams(initDataNow()).get("user") || "null").id || null; }
    catch (e) { return null; }
  }

  function saveHomeSnapshot(data) {
    var uid = launchUserId();
    if (!uid || !data || data.empty) return;
    try { window.localStorage.setItem(SNAP_KEY, JSON.stringify({ t: Date.now(), u: uid, v: viewerId, d: data })); }
    catch (e) { /* нет памяти — старт как обычно */ }
  }

  function readHomeSnapshot() {
    var uid = launchUserId();
    if (!uid) return null;
    try {
      var snap = JSON.parse(window.localStorage.getItem(SNAP_KEY) || "null");
      if (snap && snap.u === uid && snap.d && Date.now() - snap.t < SNAP_MAX_AGE_MS) return snap;
    } catch (e) { /* испорченный снимок — как будто его нет */ }
    return null;
  }

  function snapshotNote(text) {
    var old = document.getElementById("snap-note");
    if (old) old.parentNode.removeChild(old);
    if (!text) return;
    var note = el("div", "snap-note", text);
    note.id = "snap-note";
    app.insertBefore(note, app.firstChild);
  }

  // Старт без лишнего ожидания (v2.172.0): данные первого экрана запрашиваются
  // сразу, параллельно с /api/me, — render() возьмёт их из кэша ответов
  var firstJobs = [];
  try { firstJobs = routeJobs(currentRoute()); } catch (e) { /* первый экран загрузит сам */ }
  var meReady = api("me").then(function (d) { viewerId = d.id; }, function () { viewerId = null; });
  var snap = currentRoute() === "" ? readHomeSnapshot() : null;
  if (snap) {
    viewerId = snap.v;
    app.textContent = "";
    app.classList.add("quiet");
    drawTabbar("home");
    try { renderHome(app, snap.d); snapshotNote("Обновляется…"); }
    catch (e) { snap = null; app.textContent = ""; }
  }
  if (snap) {
    Promise.all([meReady].concat(firstJobs)).then(function () {
      snapshotNote("");
      // игрок мог уже пролистать снимок — место сохраняется (по записи истории, v2.201.1)
      currentNav = navEntry();
      renderedNav = currentNav.id;
      scrollPos[currentNav.id] = window.scrollY;
      render(true);
      prefetchTabs();
    }, function () {
      var t = new Date(snap.t);
      snapshotNote("Нет связи — данные на " + ("0" + t.getHours()).slice(-2) + ":" + ("0" + t.getMinutes()).slice(-2));
    });
  } else {
    meReady.then(function () { render(); prefetchTabs(); });
  }
})();
