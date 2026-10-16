  // ── API ────────────────────────────────────────────────────────────────────
  // Ответы держим минуту: переключение разделов профиля и возврат «назад» не
  // ходят на сервер заново. Ошибки не кэшируются. Данные меняются, только когда
  // вносят матч, — минутная давность не заметна.
  var CACHE_TTL_MS = 60 * 1000;
  var apiCache = {};

  function api(path) {
    var initData = initDataNow();
    if (!initData) return Promise.reject(new Error("Откройте приложение из бота."));
    var hit = apiCache[path];
    if (hit && Date.now() - hit.t < CACHE_TTL_MS) return hit.p;
    var p = timedFetch("api/" + path, { headers: { "Authorization": "tma " + initData } })
      .then(function (resp) {
        return resp.json().then(function (body) {
          if (!resp.ok) throw new Error(body.error || "Не удалось загрузить данные.");
          return body;
        });
      }, function (e) {
        throw e && /не ответил/.test(e.message) ? e : new Error("Нет связи с сервером. Попробуйте ещё раз.");
      });
    apiCache[path] = { t: Date.now(), p: p };
    p.catch(function () { delete apiCache[path]; });
    return p;
  }

  // Ошибки JS на устройстве (v2.177.0): после запуска — в журнал сервера, не
  // больше трёх за сессию. Только запись в лог, без уведомлений.
  var reportedErrors = 0;
  window.addEventListener("error", function (e) {
    if (reportedErrors >= 3 || !initDataNow() || !e || !e.message) return;
    reportedErrors += 1;
    var info = clientInfo();
    try {
      fetch("api/client_error", {
        method: "POST", keepalive: true,
        headers: { "Authorization": "tma " + initDataNow(), "Content-Type": "application/json" },
        body: JSON.stringify({ message: e.message, where: (e.filename || "").split("/").pop() + ":" + e.lineno,
          route: currentRoute(), platform: info.platform, version: info.version })
      }).catch(function () {});
    } catch (err) { /* отчёт не должен мешать */ }
  });

  function post(path, body) {
    var initData = initDataNow();
    if (!initData) return Promise.reject(new Error("Откройте приложение из бота."));
    return timedFetch("api/" + path, {
      method: "POST",
      headers: { "Authorization": "tma " + initData, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (resp) {
      return resp.json().then(function (b) {
        if (!resp.ok) throw new Error(b.error || "Не удалось сохранить.");
        return b;
      });
    });
  }

  // Счётчик экранов (v2.166.0): какой экран показан — в ту же статистику, что
  // и у бота. Первый экран приносит время загрузки (в лог сервера, не в базу).
  var firstTrack = true;
  // Имя экрана для счётчика: свой профиль — «me», чужой — «player», id отрезаются
  function screenName(route, viewer) {
    return route
      .replace(/^player\/(\d+)/, function (_, id) { return id === String(viewer) ? "me" : "player"; })
      .replace(/\/\d+/g, "")
      .replace(/\//g, "_") || "home";
  }

  function track(route) {
    var initData = initDataNow();
    if (!initData) return;
    var body = { screen: screenName(route, viewerId) };
    if (firstTrack && window.performance) {
      // v2.175.0: и клиент Telegram, и когда пришёл его скрипт — в лог сервера
      var info = clientInfo();
      body.boot_ms = Math.round(performance.now());
      body.platform = info.platform;
      body.version = info.version;
      body.tg_ms = telegramAt;
      // v2.194.0: замер сети загрузчика — где ушло время у медленного запуска
      if (window.__clubBoot && window.__clubBoot.timing) body.net = window.__clubBoot.timing();
      firstTrack = false;
      window.__clubBooted = true;
    }
    try {
      fetch("api/track", {
        method: "POST", keepalive: true,
        headers: { "Authorization": "tma " + initData, "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).catch(function () {});
    } catch (e) { /* счётчик не должен мешать приложению */ }
  }

  // Серые «скелеты» вместо слова «Загрузка…»
  function skeleton(kind) {
    var wrap = el("div", "sk");
    var shapes = kind === "profile" ? ["sk-hero", "sk-tabs", "sk-tiles"] :
      kind === "body" ? ["sk-tiles", "sk-card"] :
      // v2.182.0: по форме экрана — главная, рейтинг, матч
      kind === "home" ? ["sk-title", "sk-podium", "sk-me", "sk-button", "sk-card"] :
      kind === "rating" ? ["sk-title", "sk-tabs", "sk-row", "sk-row", "sk-row", "sk-row", "sk-row"] :
      kind === "match" ? ["sk-versus", "sk-card", "sk-row", "sk-row"] :
      ["sk-title", "sk-card", "sk-card"];
    shapes.forEach(function (c) { wrap.appendChild(el("div", "sk-block " + c)); });
    return wrap;
  }

