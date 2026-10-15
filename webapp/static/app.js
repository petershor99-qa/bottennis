// Собрано scripts/build_app_js.py из webapp/src/app/*.js — правьте части там.
// Mini App клуба. v2.160.0 — «Рейтинг клуба»; v2.161.0 — экраны «только
// чтение»; v2.162.0 — свой интерфейс приложения: вкладки «Клуб / Рейтинг / Я»,
// главная с подиумом и матчем дня, профиль-табло с разделами на одном экране,
// ачивки сеткой с уровнями, «ты против него», фото игроков, анимации и вибрация.
// Тексты приходят с сервера (bot/webapp/api.py). Данные вставляются через
// textContent — имена не интерпретируются как HTML.
(function () {
  "use strict";

  // В Node (тесты tests/js, v2.174.0) — только чистые функции, без DOM и Telegram
  if (typeof window === "undefined") {
    module.exports = {
      matchesWord: matchesWord, screenName: screenName, splitNumber: splitNumber, badgeClass: badgeClass,
      parseLaunchParams: parseLaunchParams, isColorDark: isColorDark,
      filterMatches: filterMatches, recordLine: recordLine, compactDate: compactDate,
      toggledReactions: toggledReactions, matchMood: matchMood, tapGuard: tapGuard, chipSets: chipSets, isPhoneClient: isPhoneClient, replayTiming: replayTiming, ballHeight: ballHeight,
      nextNav: nextNav, sparkPoints: sparkPoints, odometerDigits: odometerDigits, raceOrder: raceOrder
    };
    return;
  }

  // Скрипт Telegram может прийти позже страницы или не прийти вовсе (медленный
  // или закрытый telegram.org) — с v2.175.0 приложение не ждёт его: подпись для
  // входа и тему берёт из адреса запуска (#tgWebAppData=…), а кнопку «Назад»,
  // вибрацию и тему Telegram подключает, когда скрипт придёт (attachTelegram).
  var tg = window.Telegram && window.Telegram.WebApp;
  var launch = parseLaunchParams(location.hash);
  if (launch.tgWebAppData) {
    // Скрипт Telegram, пришедший после смены адреса, возьмёт параметры отсюда
    try { window.sessionStorage.setItem("__telegram__initParams", JSON.stringify(launch)); } catch (e) { /* нет памяти */ }
  } else {
    try { launch = JSON.parse(window.sessionStorage.getItem("__telegram__initParams")) || {}; } catch (e) { launch = {}; }
  }
  var telegramAt = null;  // через сколько мс после старта пришёл скрипт Telegram
  var root = document.documentElement;
  var app = document.getElementById("app");
  var tabbar = document.getElementById("tabbar");
  var DARK_BG = "#0A0F1F";
  var SVG = "http://www.w3.org/2000/svg";
  var renderToken = 0;
  var viewerId = null;

  // ── Тема, вибрация ─────────────────────────────────────────────────────────
  // Параметры запуска из адреса: «#tgWebAppData=…&tgWebAppVersion=…» -> объект.
  // Разбор как у telegram-web-app.js; адрес-маршрут «#/rating» — пустой объект.
  function parseLaunchParams(hash) {
    var out = {};
    hash = String(hash || "").replace(/^#/, "");
    if (!hash || hash.charAt(0) === "/" || hash.indexOf("=") < 0) return out;
    var q = hash.indexOf("?");
    if (q >= 0) hash = hash.slice(q + 1);
    hash.split("&").forEach(function (pair) {
      var i = pair.indexOf("=");
      if (i <= 0) return;
      out[decodeParam(pair.slice(0, i))] = decodeParam(pair.slice(i + 1));
    });
    return out;
  }

  function decodeParam(s) {
    try { return decodeURIComponent(s.replace(/\+/g, "%20")); } catch (e) { return s; }
  }

  // Тёмный ли цвет фона «#RRGGBB» — та же формула, что у Telegram
  function isColorDark(hex) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || ""));
    if (!m) return false;
    var r = parseInt(m[1], 16), g = parseInt(m[2], 16), b = parseInt(m[3], 16);
    return Math.sqrt(0.299 * r * r + 0.587 * g * g + 0.114 * b * b) < 120;
  }

  function launchDark() {
    try { return isColorDark(JSON.parse(launch.tgWebAppThemeParams || "{}").bg_color); }
    catch (e) { return false; }
  }

  function initDataNow() { return (tg && tg.initData) || launch.tgWebAppData || ""; }

  function clientInfo() {
    return {
      platform: (tg && tg.platform) || launch.tgWebAppPlatform || "",
      version: (tg && tg.version) || launch.tgWebAppVersion || ""
    };
  }

  // Запрос с ограничением по времени: без него зависший ответ оставлял
  // «Загрузку…» навсегда (v2.175.0)
  var FETCH_TIMEOUT_MS = 15000;
  function timedFetch(url, opts) {
    var ctrl = window.AbortController ? new AbortController() : null;
    if (ctrl) opts.signal = ctrl.signal;
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () {
        if (ctrl) ctrl.abort();
        reject(new Error("Сервер не ответил. Попробуйте ещё раз."));
      }, FETCH_TIMEOUT_MS);
      fetch(url, opts).then(function (r) { clearTimeout(timer); resolve(r); },
        function (e) { clearTimeout(timer); reject(e); });
    });
  }

  function applyTheme() {
    var dark = tg ? tg.colorScheme === "dark" : launchDark();
    root.setAttribute("data-theme", dark ? "dark" : "light");
    if (!tg) return;
    try {
      var bg = dark ? DARK_BG : (tg.themeParams.secondary_bg_color || "#EFEFF4");
      tg.setHeaderColor(bg);
      tg.setBackgroundColor(bg);
      if (tg.setBottomBarColor) tg.setBottomBarColor(bg);
    } catch (e) { /* старые клиенты Telegram */ }
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "select") tg.HapticFeedback.selectionChanged();
      else tg.HapticFeedback.impactOccurred("light");
    } catch (e) { /* нет вибрации — не страшно */ }
  }

  // ── Разметка ───────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svg(tag, attrs) {
    var node = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  // Все значки — из набора static/icons.svg (Tabler Icons, MIT): и ачивки, и
  // интерфейс (v2.169.0, раньше интерфейс рисовался своими контурами).
  // Короткие имена интерфейса -> имена в наборе.
  var ICONS = {
    home: "home", rating: "chart-bar", user: "user", trophy: "trophy", crown: "crown",
    calendar: "calendar", list: "list", flag: "flag", flame: "flame", target: "target",
    bars: "chart-histogram", users: "users", lock: "lock", swords: "swords", chevron: "chevron-right"
  };

  function icon(name, cls) { return useIcon(ICONS[name] || name, cls); }

  function useIcon(name, cls) {
    var s = svg("svg", { "class": "ico" + (cls ? " " + cls : ""), viewBox: "0 0 24 24", "aria-hidden": "true" });
    var u = svg("use", { href: "static/icons.svg#ti-" + name });
    s.appendChild(u);
    return s;
  }

  // Нажатия в двух местах сразу (v2.185.0): пока на экране два пальца и ещё
  // чуть-чуть после, нажатия не считаются; переход — не чаще раза в 0,4 с, иначе
  // два пальца по двум плиткам давали два перехода подряд, и «Назад» вёл на
  // первый; повторное нажатие того же элемента быстрее 0,35 с — двойной тап,
  // не второе действие (кроме сторис: там частые нажатия — это листание).
  // (пороги — внутри функции: в Node, где она проверяется, переменные модуля не заданы)
  var NAV_LOCK_MS = 400;
  var multiTouchAt = 0, navLockUntil = 0, lastTap = { node: null, at: 0 };

  function tapGuard(now, target, state) {
    if (now - state.multiTouchAt < 400) return false;
    if (target && target === state.lastTap.node && now - state.lastTap.at < 350) return false;
    state.lastTap = { node: target, at: now };
    return true;
  }

  if (typeof document !== "undefined") {
    var noteTouches = function (e) { if (e.touches && e.touches.length > 1) multiTouchAt = Date.now(); };
    document.addEventListener("touchstart", noteTouches, { passive: true, capture: true });
    document.addEventListener("touchmove", noteTouches, { passive: true, capture: true });
    document.addEventListener("click", function (e) {
      var node = e.target && e.target.closest ? e.target.closest("button, .tap, a") : null;
      if (!node || node.closest(".story")) return;
      var state = { multiTouchAt: multiTouchAt, lastTap: lastTap };
      var ok = tapGuard(Date.now(), node, state);
      lastTap = state.lastTap;
      if (!ok) { e.stopPropagation(); e.preventDefault(); }
    }, true);
  }

  function go(route) {
    var now = Date.now();
    if (now < navLockUntil) return;
    navLockUntil = now + NAV_LOCK_MS;
    haptic("impact");
    location.hash = "#/" + route;
  }

  function clickable(node, route) {
    node.classList.add("tap");
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.addEventListener("click", function () { go(route); });
    node.addEventListener("keydown", function (e) { if (e.key === "Enter") go(route); });
    return node;
  }

  function header(title, sub) {
    var h = el("header", "screen-head");
    h.appendChild(el("h1", "screen-title", title));
    if (sub) h.appendChild(el("p", "screen-sub", sub));
    return h;
  }

  // Две колонки на широком окне (v2.176.0, Telegram на компьютере): main —
  // слева, side — справа; на телефоне они идут друг под другом в том же порядке
  function twoCols(c, stickyMain) {
    var cols = el("div", "cols");
    var main = el("div", "col col-main" + (stickyMain ? " col-sticky" : ""));
    var side = el("div", "col col-side");
    cols.appendChild(main);
    cols.appendChild(side);
    c.appendChild(cols);
    if (c === app) app.classList.add("is-wide");
    return { main: main, side: side };
  }

  function section(title) {
    var wrap = el("section", "group");
    if (title) wrap.appendChild(el("h2", "group-title", title));
    var list = el("ul", "list");
    wrap.appendChild(list);
    wrap.list = list;
    return wrap;
  }

  function progressBar(fraction) {
    var bar = el("div", "progress");
    var fill = el("i");
    fill.style.width = Math.round(Math.max(0, Math.min(1, fraction)) * 100) + "%";
    bar.appendChild(fill);
    return bar;
  }

  function formStrip(marks, cls) {
    var wrap = el("div", "strip " + (cls || ""));
    marks.forEach(function (m) { wrap.appendChild(el("i", "cell cell-" + m)); });
    return wrap;
  }

  // Элемент из строк бота: короткая подпись — в две колонки, длинная — стопкой
  function itemRow(item) {
    var li = el("li", "row");
    if (item.form) {
      li.classList.add("row-pair");
      li.appendChild(el("div", "label", item.label));
      li.appendChild(formStrip(item.form, "strip-inline"));
      return li;
    }
    var shortPair = item.label && item.value !== undefined && item.label.length <= 24 && item.value.length <= 28;
    if (shortPair && item.progress === undefined) {
      li.classList.add("row-pair");
      li.appendChild(el("div", "label", item.label));
      li.appendChild(el("div", "value", item.value));
      if (item.note) li.appendChild(el("div", "sub sub-wide", item.note));
      return li;
    }
    var body = el("div", "body");
    if (item.label) {
      var top = el("div", "pair-top");
      top.appendChild(el("span", "name", item.label));
      if (item.progress !== undefined) top.appendChild(el("span", "value", item.value));
      body.appendChild(top);
      if (item.progress === undefined) body.appendChild(el("div", "sub", item.value));
    } else {
      body.appendChild(el("div", "text", item.text));
    }
    if (item.note) body.appendChild(el("div", "sub", item.note));
    if (item.progress !== undefined) body.appendChild(progressBar(item.progress));
    li.appendChild(body);
    return li;
  }

  // Пустой экран — со значком; ошибка — со значком и кнопкой «Повторить» (v2.169.0)
  // Пустой экран (v2.177.0): свой рисунок — ракетка и мяч над сеткой — вместо
  // стандартного значка; у ошибки — прежний значок
  function tableArt() {
    var s = svg("svg", { "class": "table-art", viewBox: "0 0 120 72", "aria-hidden": "true" });
    s.appendChild(svg("path", { d: "M8 58 H112", "class": "art-table" }));
    s.appendChild(svg("path", { d: "M60 40 V58", "class": "art-net" }));
    s.appendChild(svg("path", { d: "M54 40 H66", "class": "art-net" }));
    s.appendChild(svg("circle", { cx: 34, cy: 30, r: 13, "class": "art-racket" }));
    s.appendChild(svg("path", { d: "M42 39 L50 50", "class": "art-handle" }));
    s.appendChild(svg("circle", { cx: 84, cy: 22, r: 5, "class": "art-ball" }));
    s.appendChild(svg("path", { d: "M84 30 Q86 44 92 54", "class": "art-trail" }));
    return s;
  }

  function emptyState(container, text, isError) {
    var box = el("div", "state" + (isError ? " state-error" : ""));
    box.appendChild(isError ? useIcon("info-circle", "state-ico") : tableArt());
    box.appendChild(el("p", "state-text", text));
    if (isError) {
      var again = el("button", "more", "Повторить");
      again.type = "button";
      again.addEventListener("click", function () { haptic("impact"); apiCache = {}; shell = null; render(); });
      box.appendChild(again);
    }
    container.appendChild(box);
  }

  // ── Память устройства (v2.169.0) ───────────────────────────────────────────
  // CloudStorage Telegram (общая для всех устройств игрока), без него —
  // localStorage. undefined — памяти нет: подсказка и подсветка тогда просто
  // не показываются; null — ключа ещё нет.
  function cloud() {
    try { return tg && tg.CloudStorage && tg.isVersionAtLeast && tg.isVersionAtLeast("6.9") ? tg.CloudStorage : null; }
    catch (e) { return null; }
  }

  function storageGet(key, cb) {
    var cs = cloud();
    if (cs) {
      try {
        cs.getItem(key, function (err, value) { cb(err ? undefined : (value || null)); });
        return;
      } catch (e) { /* ниже — localStorage */ }
    }
    var value;
    try { value = window.localStorage.getItem(key); } catch (e) { value = undefined; }
    cb(value);
  }

  function storageSet(key, value) {
    var cs = cloud();
    try {
      if (cs) cs.setItem(key, value);
      else window.localStorage.setItem(key, value);
    } catch (e) { /* без памяти — без подсказок */ }
  }

  // ── Фото игроков: буква сразу, фото подменяет её, когда загрузится ─────────
  var photoCache = {};

  function avatar(person, size) {
    var a = el("div", "avatar avatar-" + (size || "md"), person.initial);
    a.style.setProperty("--hue", String((person.id * 47) % 360));
    loadPhoto(person.id, a);
    return a;
  }

  function loadPhoto(id, node) {
    function apply(url) {
      if (!url) return;
      node.style.backgroundImage = "url(" + url + ")";
      node.classList.add("has-photo");
      node.textContent = "";
    }
    var cached = photoCache[id];
    if (cached !== undefined) {
      if (cached && typeof cached.then === "function") return cached.then(function (u) { apply(u); return u; });
      apply(cached);
      return Promise.resolve(cached);
    }
    var initData = initDataNow();
    if (!initData) return Promise.resolve(null);
    var pending = fetch("api/photo/" + id, { headers: { "Authorization": "tma " + initData } })
      .then(function (r) { return r.ok ? r.blob() : null; })
      .then(function (b) { var url = b ? URL.createObjectURL(b) : null; photoCache[id] = url; return url; },
            function () { photoCache[id] = null; return null; });
    photoCache[id] = pending;
    return pending.then(function (u) { apply(u); return u; });
  }

  // ── Анимации ───────────────────────────────────────────────────────────────
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // «1080.1», «60%», «+12.4» -> [до числа, знак минуса, число, после] или null
  function splitNumber(text) {
    var m = /^([^\d−-]*)([−-]?)(\d+(?:\.\d+)?)(.*)$/.exec(text);
    return m ? [m[1], m[2], m[3], m[4]] : null;
  }

  function countUp(node, text) {
    // досчитывается число, остальное как есть
    var parts = splitNumber(text);
    if (!parts || reduceMotion || app.classList.contains("quiet")) { node.textContent = text; return; }
    var m = [text].concat(parts);
    var target = parseFloat(m[3]), decimals = (m[3].split(".")[1] || "").length;
    var start = null, duration = 650;
    function frame(ts) {
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / duration);
      var eased = 1 - Math.pow(1 - p, 3);
      node.textContent = m[1] + m[2] + (target * eased).toFixed(decimals) + m[4];
      if (p < 1) requestAnimationFrame(frame);
    }
    node.textContent = m[1] + m[2] + (0).toFixed(decimals) + m[4];
    requestAnimationFrame(frame);
  }

  function num(cls, text) { var n = el("div", cls); countUp(n, text); return n; }

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

  // ── Графики ────────────────────────────────────────────────────────────────
  function ratingChart(chart) {
    var W = 320, H = 110, L = 2, R = 2, T = 8, B = 16;
    var vals = chart.values;
    var lo = Math.min.apply(null, vals.concat([chart.reference])) - 8;
    var hi = Math.max.apply(null, vals.concat([chart.reference])) + 8;
    function x(i) { return L + (W - L - R) * (vals.length === 1 ? 0.5 : i / (vals.length - 1)); }
    function y(v) { return T + (H - T - B) * (1 - (v - lo) / (hi - lo)); }
    var wrap = el("div", "chart-wrap");
    var s = svg("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img" });
    var ref = y(chart.reference);
    s.appendChild(svg("line", { "class": "chart-ref", x1: L, x2: W - R, y1: ref, y2: ref }));
    var pts = vals.map(function (v, i) { return x(i).toFixed(1) + "," + y(v).toFixed(1); });
    var bottom = (H - B).toFixed(1);
    s.appendChild(svg("path", { "class": "chart-area",
      d: "M" + x(0).toFixed(1) + "," + bottom + " L" + pts.join(" L") + " L" + x(vals.length - 1).toFixed(1) + "," + bottom + " Z" }));
    s.appendChild(svg("polyline", { "class": "chart-line", points: pts.join(" "), pathLength: "1" }));
    var last = vals.length - 1;
    var dot = svg("circle", { "class": "chart-dot", cx: x(last), cy: y(vals[last]), r: 3.5 });
    var marker = svg("line", { "class": "chart-marker", x1: 0, x2: 0, y1: T, y2: H - B });
    s.appendChild(marker);
    s.appendChild(dot);
    var first = svg("text", { "class": "chart-label", x: L, y: H - 3 });
    first.textContent = chart.labels[0];
    var end = svg("text", { "class": "chart-label", x: W - R, y: H - 3, "text-anchor": "end" });
    end.textContent = chart.labels[last];
    s.appendChild(first);
    s.appendChild(end);
    wrap.appendChild(s);

    var meta = el("div", "chart-meta");
    var rest = "от " + chart.min.toFixed(1) + " до " + chart.max.toFixed(1) + " · нажмите на график";
    meta.textContent = rest;
    wrap.appendChild(meta);

    function pick(ev) {
      var box = s.getBoundingClientRect();
      if (!box.width) return;
      var px = (ev.clientX - box.left) / box.width * W;
      var i = Math.max(0, Math.min(vals.length - 1, Math.round((px - L) / (W - L - R) * (vals.length - 1))));
      marker.setAttribute("x1", x(i)); marker.setAttribute("x2", x(i));
      marker.classList.add("is-on");
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(vals[i]));
      meta.textContent = chart.labels[i] + " · " + vals[i].toFixed(1);
    }
    function release() {
      marker.classList.remove("is-on");
      dot.setAttribute("cx", x(last)); dot.setAttribute("cy", y(vals[last]));
      meta.textContent = rest;
    }
    s.addEventListener("pointerdown", pick);
    s.addEventListener("pointermove", function (ev) { if (ev.buttons || ev.pointerType === "touch") pick(ev); });
    s.addEventListener("pointerup", release);
    s.addEventListener("pointerleave", release);
    s.addEventListener("pointercancel", release);
    return wrap;
  }

  function heatmap(days) {
    var cell = 12, gap = 3, weeks = Math.ceil(days.length / 7);
    var s = svg("svg", { "class": "heat", viewBox: "0 0 " + weeks * (cell + gap) + " " + 7 * (cell + gap), role: "img" });
    days.forEach(function (d, i) {
      var tier = d.count === null ? "out" : d.count === 0 ? 0 : d.count === 1 ? 1 : d.count <= 3 ? 2 : 3;
      var r = svg("rect", { "class": "heat-" + tier, x: Math.floor(i / 7) * (cell + gap), y: (i % 7) * (cell + gap),
        width: cell, height: cell, rx: 3 });
      if (d.count !== null) { var t = svg("title"); t.textContent = d.date + ": " + d.count; r.appendChild(t); }
      s.appendChild(r);
    });
    return s;
  }

  function radarChart(axes) {
    var size = 300, c = size / 2, rad = 100, n = axes.length;
    var s = svg("svg", { "class": "radar", viewBox: "0 0 " + size + " " + size, role: "img" });
    function point(i, k) {
      var a = -Math.PI / 2 + 2 * Math.PI * i / n;
      return [c + Math.cos(a) * rad * k, c + Math.sin(a) * rad * k];
    }
    [0.25, 0.5, 0.75, 1].forEach(function (k) {
      s.appendChild(svg("polygon", { "class": "radar-grid",
        points: axes.map(function (_, i) { return point(i, k).join(","); }).join(" ") }));
    });
    axes.forEach(function (ax, i) {
      var p = point(i, 1);
      s.appendChild(svg("line", { "class": "radar-grid", x1: c, y1: c, x2: p[0], y2: p[1] }));
      var lp = point(i, 1.22);
      var t = svg("text", { "class": "radar-label", x: lp[0], y: lp[1] + 4, "text-anchor": "middle" });
      t.textContent = ax.name;
      s.appendChild(t);
    });
    s.appendChild(svg("polygon", { "class": "radar-shape",
      points: axes.map(function (ax, i) { return point(i, Math.max(0.02, ax.value / 100)).join(","); }).join(" ") }));
    return s;
  }

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
    if (mood) tags.push(["feed-tag mood-tag mood-" + mood, MOOD_LABELS[mood]]);
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
  function clubTiles(links) {
    var wrap = el("section", "group club-group");
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

  // ── Рейтинг ────────────────────────────────────────────────────────────────
  function boardRow(row) {
    var li = clickable(el("li", "row" + (row.is_viewer ? " is-viewer" : "")), "player/" + row.player_id);
    li.appendChild(el("div", "rank-num", String(row.rank)));
    li.appendChild(avatar({ id: row.player_id, initial: row.initial }, "md"));
    var body = el("div", "body");
    var nameLine = el("div", "name");
    nameLine.appendChild(document.createTextNode(row.is_viewer ? row.name + ", ты" : row.name));
    if (row.subtitle.indexOf("Чемпион") === 0) nameLine.appendChild(icon("crown", "crown-inline"));
    body.appendChild(nameLine);
    body.appendChild(el("div", "sub", row.subtitle));
    li.appendChild(body);
    var value = el("div", "value");
    var spark = sparkline(row.spark);
    if (spark) li.insertBefore(spark, null);
    value.appendChild(el("div", "rating", row.rating));
    if (row.week_change !== 0) {
      value.appendChild(el("div", "place " + (row.week_change > 0 ? "tone-win" : "tone-loss"),
        (row.week_change > 0 ? "▲ " : "▼ ") + Math.abs(row.week_change)));
    }
    li.appendChild(value);
    return li;
  }

  // Подъём в рейтинге (v2.167.0): бот после матча открывает рейтинг со ссылкой
  // ?r=rating&from=<место до матча>. Если место выросло — своя строка «въезжает»
  // снизу на новое место, над таблицей короткая плашка. Один раз за запуск.
  var pendingRise = null;

  function animateRise(c, li, from, to) {
    var note = el("div", "rise-note");
    note.appendChild(icon("rating", "ico-lead"));
    note.appendChild(document.createTextNode("Ты поднялся с #" + from + " на #" + to));
    c.insertBefore(note, c.children[1] || null);
    if (reduceMotion) { li.classList.add("rise-flash"); return; }
    requestAnimationFrame(function () {
      li.style.setProperty("--rise", ((from - to) * li.offsetHeight) + "px");
      li.classList.add("rise");
    });
    try { if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* нет вибрации */ }
  }

  function periodRow(row) {
    var li = clickable(el("li", "row" + (row.is_viewer ? " is-viewer" : "")), "player/" + row.player_id);
    li.appendChild(el("div", "rank-num", String(row.rank)));
    li.appendChild(avatar(row, "md"));
    var body = el("div", "body");
    body.appendChild(el("div", "name", row.is_viewer ? row.name + ", ты" : row.name));
    body.appendChild(el("div", "sub", row.subtitle));
    li.appendChild(body);
    li.appendChild(el("div", "rating " + (row.up ? "tone-win" : "tone-loss"), row.gain));
    return li;
  }

  var PERIODS = [["", "Всё время"], ["week", "7 дней"], ["month", "30 дней"], ["quarter", "90 дней"]];

  function periodSwitch(onPick) {
    var seg = el("div", "segmented segmented-flat");
    PERIODS.forEach(function (p, i) {
      var b = el("button", "seg" + (i === 0 ? " is-active" : ""), p[1]);
      b.type = "button";
      b.addEventListener("click", function () {
        if (b.classList.contains("is-active")) return;
        haptic("select");
        [].forEach.call(seg.children, function (x) { x.classList.remove("is-active"); });
        b.classList.add("is-active");
        onPick(p[0]);
      });
      seg.appendChild(b);
    });
    return seg;
  }

  function renderLeaderboard(c, d) {
    c.appendChild(header(d.title, d.players_label));
    if (d.empty) { emptyState(c, d.empty); return; }
    var area = el("div");
    c.appendChild(periodSwitch(function (period) {
      area.textContent = "";
      if (!period) { area.appendChild(allTimeBoard); if (gapNote) area.appendChild(gapNote); return; }
      area.appendChild(skeleton("body"));
      api("leaderboard?period=" + period).then(function (p) {
        area.textContent = "";
        area.appendChild(el("p", "footnote footnote-top", "Кто больше всех набрал " + p.label + ". Вечный рейтинг не меняется."));
        if (p.empty) { emptyState(area, p.empty); return; }
        var s = section();
        s.list.classList.add("board");
        p.rows.forEach(function (row) { s.list.appendChild(periodRow(row)); });
        area.appendChild(s);
      }, function (err) { area.textContent = ""; emptyState(area, err.message, true); });
    }));
    c.appendChild(area);
    var board = section();
    var allTimeBoard = board;
    var gapNote = null;
    board.list.classList.add("board");
    var mine = null, myRow = null;
    d.rows.forEach(function (row) {
      var li = boardRow(row);
      if (row.is_viewer) { mine = li; myRow = row; }
      board.list.appendChild(li);
    });
    area.appendChild(board);
    if (d.gap) { gapNote = el("p", "footnote", d.gap); area.appendChild(gapNote); }
    var ids = d.rows.map(function (row) { return row.player_id; });
    if (pendingRise && mine && pendingRise > myRow.rank) {
      animateRise(c, mine, pendingRise, myRow.rank);
      try { window.localStorage.setItem(ORDER_KEY, JSON.stringify(ids)); } catch (e) { /* без памяти */ }
    } else if (!app.classList.contains("quiet")) {
      requestAnimationFrame(function () { boardFlip(board.list, ids); });
    }
    pendingRise = null;
  }

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

  // ── Матчи, «ты против него» ────────────────────────────────────────────────
  var RESULT_WORDS = { w: "Победа", l: "Поражение", d: "Ничья" };

  function matchRow(m, sameOpponent) {
    var li = clickable(el("li", "row match-row result-" + m.result), m.id ? "match/" + m.id : "player/" + m.opponent_id);
    var body = el("div", "body");
    body.appendChild(el("div", "name", sameOpponent ? RESULT_WORDS[m.result] : m.opponent));
    body.appendChild(el("div", "sub", [m.date, m.score, m.boss ? "боссфайт" : ""].filter(Boolean).join(" · ")));
    li.appendChild(body);
    if (m.delta) li.appendChild(el("div", "delta " + (m.delta.charAt(0) === "+" ? "tone-win" : "tone-loss"), m.delta));
    return li;
  }

  // История дуэли (v2.177.0): кто вёл по ходу встреч (баланс побед), лучшие
  // серии, лучший матч пары и последние месяцы
  function balanceSvg(points) {
    var W = 320, H = 110, T = 10, B = 18;
    var vals = points.map(function (p) { return p.v; });
    var lo = Math.min.apply(null, vals.concat([0])), hi = Math.max.apply(null, vals.concat([0]));
    if (hi === lo) { hi += 1; lo -= 1; }
    function x(i) { return points.length < 2 ? W / 2 : 4 + (W - 8) * i / (points.length - 1); }
    function y(v) { return T + (H - T - B) * (1 - (v - lo) / (hi - lo)); }
    var s = svg("svg", { "class": "chart balance", viewBox: "0 0 " + W + " " + H, role: "img" });
    s.appendChild(svg("line", { x1: 0, x2: W, y1: y(0), y2: y(0), "class": "balance-zero" }));
    var dpath = points.map(function (p, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.v).toFixed(1); }).join(" ");
    s.appendChild(svg("path", { d: dpath, "class": "balance-line" }));
    var last = points[points.length - 1];
    s.appendChild(svg("circle", { cx: x(points.length - 1), cy: y(last.v), r: 4,
      "class": last.v > 0 ? "balance-dot is-up" : last.v < 0 ? "balance-dot is-down" : "balance-dot" }));
    [[0, "start"], [points.length - 1, "end"]].forEach(function (e) {
      var t = svg("text", { x: e[1] === "start" ? 2 : W - 2, y: H - 3, "text-anchor": e[1], "class": "chart-label" });
      t.textContent = points[e[0]].d;
      s.appendChild(t);
    });
    return s;
  }

  function duelCard(duel, them) {
    var wrap = el("section", "group");
    wrap.appendChild(el("h2", "group-title", "История дуэли"));
    var card = el("div", "card duel");
    var top = el("div", "pair-top");
    top.appendChild(el("span", "eyebrow", "Кто вёл: выше нуля — ты"));
    if (duel.points_share !== null) top.appendChild(el("span", "eyebrow-meta", "твоих очков " + duel.points_share + "%"));
    card.appendChild(top);
    card.appendChild(balanceSvg(duel.balance));
    var tiles = el("div", "tiles duel-tiles");
    [["Твоя лучшая серия", duel.best_streak_me, "win"], ["Лучшая серия соперника", duel.best_streak_them, "loss"]].forEach(function (t) {
      var tile = el("div", "tile tile-" + t[2]);
      tile.appendChild(el("div", "tile-label", t[0]));
      tile.appendChild(num("tile-value", String(t[1])));
      tiles.appendChild(tile);
    });
    card.appendChild(tiles);
    if (duel.months.length > 1) {
      var mo = el("div", "duel-months");
      duel.months.forEach(function (m) {
        var col = el("div", "duel-month");
        var bar = el("div", "duel-bar");
        var total = Math.max(1, m.me + m.them);
        var me = el("i", "duel-me"); me.style.height = (m.me / total * 100) + "%";
        var th = el("i", "duel-them"); th.style.height = (m.them / total * 100) + "%";
        bar.appendChild(th);
        bar.appendChild(me);
        col.appendChild(bar);
        col.appendChild(el("span", "duel-score", m.me + ":" + m.them));
        col.appendChild(el("span", "duel-label", m.m));
        mo.appendChild(col);
      });
      card.appendChild(mo);
    }
    wrap.appendChild(card);
    var best = el("ul", "feed-list");
    var bm = duel.best_match;
    var li = clickable(el("li", "feed-card feed-match"), "match/" + bm.id);
    var body = el("div", "body");
    body.appendChild(el("div", "eyebrow", "Лучший матч пары · " + bm.date));
    body.appendChild(versusLine(bm));
    var sc = el("div", "feed-score");
    sc.appendChild(setChips(bm.sets, bm.winner));
    body.appendChild(sc);
    li.appendChild(body);
    best.appendChild(li);
    wrap.appendChild(best);
    return wrap;
  }

  function renderVersus(c, d) {
    var card = el("div", "card versus");
    var row = el("div", "versus-row");
    var me = el("div", "versus-side");
    me.appendChild(avatar(d.me, "lg"));
    me.appendChild(el("div", "versus-name", "Ты"));
    var them = clickable(el("div", "versus-side"), "player/" + d.them.id);
    them.appendChild(avatar(d.them, "lg"));
    them.appendChild(el("div", "versus-name", d.them.name));
    var score = el("div", "versus-score");
    if (d.score) {
      score.appendChild(num("versus-num" + (d.score.me > d.score.them ? " tone-win" : ""), String(d.score.me)));
      score.appendChild(el("span", "versus-colon", ":"));
      score.appendChild(num("versus-num" + (d.score.them > d.score.me ? " tone-loss" : ""), String(d.score.them)));
    } else {
      score.appendChild(el("span", "versus-colon", "—"));
    }
    row.appendChild(me);
    row.appendChild(score);
    row.appendChild(them);
    card.appendChild(row);
    if (d.score && d.score.draws) card.appendChild(el("div", "sub center", "ничьих: " + d.score.draws));
    c.appendChild(card);

    var fc = el("div", "card forecast");
    var ft = el("div", "pair-top");
    ft.appendChild(el("span", "eyebrow", "Шанс победы сейчас"));
    ft.appendChild(num("forecast-num", d.forecast.chance + "%"));
    fc.appendChild(ft);
    fc.appendChild(progressBar(d.forecast.chance / 100));
    var wl = el("div", "forecast-split");
    wl.appendChild(el("span", "tone-win", "Победа " + d.forecast.win));
    wl.appendChild(el("span", "tone-loss", "Поражение " + d.forecast.lose));
    fc.appendChild(wl);
    c.appendChild(fc);
    c.appendChild(challengeButton(d.them.id));

    if (d.axes.length) {
      var ax = section("Стиль: ты и " + d.them.name);
      var key = el("li", "row axis-key");
      var k1 = el("span", "legend-item");
      k1.appendChild(el("i", "swatch swatch-me"));
      k1.appendChild(document.createTextNode("Ты"));
      var k2 = el("span", "legend-item");
      k2.appendChild(el("i", "swatch swatch-them"));
      k2.appendChild(document.createTextNode(d.them.name));
      key.appendChild(k1);
      key.appendChild(k2);
      ax.list.appendChild(key);
      d.axes.forEach(function (a) {
        var li = el("li", "row axis-row");
        li.appendChild(el("div", "axis-name", a.name));
        var bars = el("div", "axis-bars");
        var left = el("div", "axis-half axis-me");
        var lf = el("i"); lf.style.width = a.me + "%"; left.appendChild(lf);
        var right = el("div", "axis-half axis-them");
        var rf = el("i"); rf.style.width = a.them + "%"; right.appendChild(rf);
        bars.appendChild(el("span", "axis-num", String(a.me)));
        bars.appendChild(left);
        bars.appendChild(right);
        bars.appendChild(el("span", "axis-num", String(a.them)));
        li.appendChild(bars);
        ax.list.appendChild(li);
      });
      c.appendChild(ax);
    }

    if (d.empty) { emptyState(c, d.empty); return; }
    if (d.duel) c.appendChild(duelCard(d.duel, d.them.name));
    var m = section("Итог встреч");
    d.metrics.forEach(function (it) { m.list.appendChild(itemRow(it)); });
    c.appendChild(m);
    var s = section("Все встречи");
    d.matches.forEach(function (x) { s.list.appendChild(matchRow(x, true)); });
    c.appendChild(s);
  }

  // ── Разделы клуба ──────────────────────────────────────────────────────────
  function clubMatchRow(m) {
    var li = m.id ? clickable(el("li", "row"), "match/" + m.id) : el("li", "row");
    var body = el("div", "body");
    var names = el("div", "name name-pair");
    names.appendChild(el("span", m.winner === "a" ? "win" : "", m.a));
    names.appendChild(document.createTextNode(" — "));
    names.appendChild(el("span", m.winner === "b" ? "win" : "", m.b));
    body.appendChild(names);
    body.appendChild(el("div", "sub", [m.date, m.score, m.winner ? "" : "ничья"].filter(Boolean).join(" · ")));
    li.appendChild(body);
    return li;
  }

  function renderClubMatches(c, d) {
    c.appendChild(header(d.title, d.subtitle));
    if (d.empty) { emptyState(c, d.empty); return; }
    var s = section();
    d.matches.forEach(function (m) { s.list.appendChild(clubMatchRow(m)); });
    c.appendChild(s);
    var next = d.next_offset;
    if (next !== null) {
      var btn = el("button", "button button-quiet", "Показать ещё");
      btn.type = "button";
      btn.addEventListener("click", function () {
        btn.disabled = true;
        api("matches?offset=" + next).then(function (more) {
          more.matches.forEach(function (m) { s.list.appendChild(clubMatchRow(m)); });
          next = more.next_offset;
          btn.disabled = false;
          if (next === null) btn.remove();
        }, function () { btn.disabled = false; });
      });
      c.appendChild(btn);
    }
  }

  function renderRecords(c, d) {
    c.appendChild(header(d.title));
    if (d.empty) { emptyState(c, d.empty); return; }
    d.categories.forEach(function (cat) {
      var s = section(cat.title);
      cat.items.forEach(function (it) { s.list.appendChild(itemRow(it)); });
      c.appendChild(s);
    });
  }

  function renderClubActivity(c, d) {
    c.appendChild(header(d.title, d.period + " · " + d.total));
    var s = section();
    var li = el("li", "row row-chart");
    li.appendChild(heatmap(d.days));
    s.list.appendChild(li);
    c.appendChild(s);
    c.appendChild(legend(d));
  }

  function renderThrone(c, d) {
    c.appendChild(header(d.title));
    if (d.empty) { emptyState(c, d.empty); return; }
    if (d.current) {
      var card = clickable(el("div", "card throne-card"), "player/" + d.current.player_id);
      card.appendChild(icon("crown", "crown crown-big"));
      card.appendChild(avatar({ id: d.current.player_id, initial: d.current.name.charAt(0).toUpperCase() }, "xl"));
      card.appendChild(el("div", "hero-name", d.current.name));
      card.appendChild(el("div", "sub", "на троне " + d.current.duration + " · с " + d.current.since));
      c.appendChild(card);
    }
    var facts = section();
    d.facts.forEach(function (f) { facts.list.appendChild(itemRow(f)); });
    c.appendChild(facts);
    if (d.reigns.length) {
      var s = section("Прошлые правления");
      d.reigns.forEach(function (r) {
        var li = clickable(el("li", "row"), "player/" + r.player_id);
        li.appendChild(icon("crown", "ico-lead ico-gold"));
        var body = el("div", "body");
        body.appendChild(el("div", "name", r.name));
        body.appendChild(el("div", "sub", r.period + " · " + r.duration));
        if (r.note) body.appendChild(el("div", "sub", r.note));
        li.appendChild(body);
        s.list.appendChild(li);
      });
      c.appendChild(s);
    }
  }

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

  // ── «Поделиться» картинкой (v2.176.0) ──────────────────────────────────────
  // Карточка рисуется на canvas, бот присылает её игроку в чат — оттуда её можно
  // переслать куда угодно (работает на любом клиенте Telegram).
  var CARD = { W: 1080, pad: 72, bg1: "#0A0F1F", bg2: "#16204A", text: "#F2F4FA", hint: "#8A93AD",
    accent: "#5B9BFF", win: "#51CF66", loss: "#FF6B6B", panel: "rgba(255,255,255,0.06)" };
  var CARD_FONT = "Raleway, -apple-system, 'Segoe UI', Roboto, sans-serif";

  function cardCanvas(blocks) {
    var h = CARD.pad * 2 + blocks.reduce(function (s, b) { return s + b.h; }, 0) + 70;
    var cv = document.createElement("canvas");
    cv.width = CARD.W;
    cv.height = h;
    var g = cv.getContext("2d");
    var grad = g.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, CARD.bg1);
    grad.addColorStop(1, CARD.bg2);
    g.fillStyle = grad;
    g.fillRect(0, 0, CARD.W, h);
    var y = CARD.pad;
    blocks.forEach(function (b) { b.draw(g, y); y += b.h; });
    cardText(g, "Клуб настольного тенниса", CARD.pad, h - CARD.pad + 10, 30, 600, CARD.hint);
    return cv;
  }

  function cardText(g, text, x, y, size, weight, color, align) {
    g.font = (weight || 400) + " " + size + "px " + CARD_FONT;
    g.fillStyle = color || CARD.text;
    g.textAlign = align || "left";
    g.textBaseline = "alphabetic";
    g.fillText(fitText(g, String(text), CARD.W - CARD.pad * 2), x, y);
  }

  function fitText(g, text, max) {
    if (g.measureText(text).width <= max) return text;
    while (text.length > 1 && g.measureText(text + "…").width > max) text = text.slice(0, -1);
    return text + "…";
  }

  function cardPanel(g, x, y, w, h) {
    g.fillStyle = CARD.panel;
    if (g.roundRect) { g.beginPath(); g.roundRect(x, y, w, h, 28); g.fill(); }
    else g.fillRect(x, y, w, h);
  }

  function signedColor(text) { return String(text).charAt(0) === "+" ? CARD.win : CARD.loss; }

  function titleBlock(title, sub) {
    return { h: 150, draw: function (g, y) {
      cardText(g, title, CARD.pad, y + 64, 64, 800);
      if (sub) cardText(g, sub, CARD.pad, y + 116, 36, 500, CARD.hint);
    } };
  }

  function statsBlock(stats) {
    return { h: 230, draw: function (g, y) {
      var gap = 24, w = (CARD.W - CARD.pad * 2 - gap * (stats.length - 1)) / stats.length;
      stats.forEach(function (s, i) {
        var x = CARD.pad + i * (w + gap);
        cardPanel(g, x, y, w, 196);
        cardText(g, s[0], x + 32, y + 60, 30, 600, CARD.hint);
        cardText(g, s[1], x + 32, y + 150, s[1].length > 6 ? 56 : 72, 800, s[2] || CARD.text);
      });
    } };
  }

  function lineBlock(text, color, size) {
    return { h: (size || 34) + 30, draw: function (g, y) { cardText(g, text, CARD.pad, y + (size || 34), size || 34, 500, color || CARD.hint); } };
  }

  function personBlock(label, name, sub, value, color) {
    return { h: 210, draw: function (g, y) {
      cardPanel(g, CARD.pad, y, CARD.W - CARD.pad * 2, 180);
      cardText(g, label, CARD.pad + 36, y + 54, 28, 700, CARD.hint);
      cardText(g, name, CARD.pad + 36, y + 112, 52, 800);
      if (sub) cardText(g, sub, CARD.pad + 36, y + 156, 30, 500, CARD.hint);
      if (value) cardText(g, value, CARD.W - CARD.pad - 36, y + 120, 64, 800, color || signedColor(value), "right");
    } };
  }

  function rowsBlock(title, rows) {
    return { h: 70 + rows.length * 74, draw: function (g, y) {
      cardText(g, title, CARD.pad, y + 40, 30, 700, CARD.hint);
      rows.forEach(function (r, i) {
        var ry = y + 112 + i * 74;
        cardText(g, r[0], CARD.pad, ry, 40, 600);
        if (r[1]) cardText(g, r[1], CARD.W - CARD.pad, ry, 40, 800, r[2] || CARD.text, "right");
      });
    } };
  }

  function formBlock(title, marks) {
    return { h: 150, draw: function (g, y) {
      cardText(g, title, CARD.pad, y + 40, 30, 700, CARD.hint);
      var size = 64, gap = 14;
      marks.forEach(function (m, i) {
        g.fillStyle = m === "w" ? CARD.win : m === "l" ? CARD.loss : CARD.hint;
        var x = CARD.pad + i * (size + gap);
        if (g.roundRect) { g.beginPath(); g.roundRect(x, y + 66, size, size, 14); g.fill(); }
        else g.fillRect(x, y + 66, size, size);
      });
    } };
  }

  function dayCardBlocks(d) {
    var blocks = [titleBlock("Итоги дня", d.label),
      statsBlock([["Матчей", String(d.board.matches)], ["Партий", String(d.board.sets)], ["Очков", String(d.board.points)]])];
    if (d.board.usual) blocks.push(lineBlock(d.board.usual));
    if (d.hero) blocks.push(personBlock("Игрок дня", d.hero.name, d.hero.matches + " · " + d.hero.record, d.hero.delta));
    if (d.slump) blocks.push(personBlock("Кто сдал", d.slump.name, d.slump.matches + " · " + d.slump.record, d.slump.delta));
    if (d.match_of_day) {
      var m = d.match_of_day;
      blocks.push(personBlock("Матч дня", m.a.name + " — " + m.b.name, m.sets.join("  ·  "), "", ""));
    }
    if (d.players.length) {
      blocks.push(rowsBlock("Кто как сыграл", d.players.slice(0, 7).map(function (p) {
        return [p.name + "  " + p.record, p.delta, signedColor(p.delta)];
      })));
    }
    return blocks;
  }

  function profileCardBlocks(d) {
    var h = d.head;
    var blocks = [titleBlock(h.name, h.subtitle),
      statsBlock([["Рейтинг", h.rating, CARD.accent]].concat(h.week ? [["За неделю", h.week.split(" ")[0], signedColor(h.week)]] : []))];
    var tiles = (d.tiles || []).slice(0, 6).map(function (t) { return [t.label, t.value]; });
    if (tiles.length) blocks.push(rowsBlock("Коротко", tiles));
    if (d.last10 && d.last10.length) blocks.push(formBlock("Последние матчи", d.last10));
    return blocks;
  }

  function shareButton(kind, buildBlocks) {
    var b = el("button", "button button-quiet share-btn");
    b.type = "button";
    b.appendChild(icon("share"));
    b.appendChild(el("span", "", "Поделиться картинкой"));
    b.addEventListener("click", function () {
      haptic("impact");
      b.disabled = true;
      var image;
      try { image = cardCanvas(buildBlocks()).toDataURL("image/png"); }
      catch (e) { b.disabled = false; sheetOpen(null, "Не получилось", "Не удалось нарисовать картинку.", ""); return; }
      post("share", { kind: kind, image: image }).then(function () {
        sheetOpen(null, "Картинка в чате с ботом", "Открой чат с ботом и перешли её, куда хочешь.", "");
      }, function (err) {
        sheetOpen(null, "Не получилось", err.message, "");
      }).then(function () { b.disabled = false; });
    });
    return b;
  }

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
    var W = 320, H = 70, B = 16, gap = 3;
    var max = Math.max.apply(null, hours.map(function (x) { return x.n; }).concat([1]));
    var bw = (W - gap * (hours.length - 1)) / hours.length;
    var s = svg("svg", { "class": "chart hour-bars", viewBox: "0 0 " + W + " " + H, role: "img" });
    hours.forEach(function (x, i) {
      var left = i * (bw + gap), h = x.n ? Math.max(3, (H - B) * x.n / max) : 2;
      var t = svg("title");
      t.textContent = x.h + ":00 — " + x.n;
      var r = svg("rect", { x: left, y: H - B - h, width: bw, height: h, rx: 2, "class": x.n ? "hour-bar" : "hour-bar is-empty" });
      r.appendChild(t);
      s.appendChild(r);
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
        var ch = r.new ? el("span", "move move-new", "новый") :
          r.change > 0 ? el("span", "move tone-win", "▲" + r.change) :
          r.change < 0 ? el("span", "move tone-loss", "▼" + (-r.change)) : el("span", "move move-same", "—");
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
        li.appendChild(el("span", "value " + (p.delta.charAt(0) === "+" ? "tone-win" : "tone-loss"), p.delta));
        ps.list.appendChild(li);
      });
      c.appendChild(ps);
    }

    var when = el("section", "group");
    when.appendChild(el("h2", "group-title", "По часам"));
    var chart = el("div", "card");
    chart.appendChild(hourBars(d.hours));
    when.appendChild(chart);
    var all = el("ul", "feed-list");
    d.timeline.forEach(function (m) { all.appendChild(dayMatchCard(m, "", "")); });
    when.appendChild(all);
    c.appendChild(when);

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

  // ── Использование, только администратор (v2.170.0) ─────────────────────────
  function usageBars(daily) {
    var W = 320, H = 96, B = 14, gap = 2;
    var max = Math.max.apply(null, daily.map(function (x) { return x.bot + x.app; }).concat([1]));
    var bw = (W - gap * (daily.length - 1)) / daily.length;
    var s = svg("svg", { "class": "chart usage-bars", viewBox: "0 0 " + W + " " + H, role: "img" });
    daily.forEach(function (x, i) {
      var left = i * (bw + gap);
      var hBot = (H - B) * x.bot / max, hApp = (H - B) * x.app / max;
      var t = svg("title");
      t.textContent = x.d + ": бот " + x.bot + ", приложение " + x.app;
      var g = svg("g");
      g.appendChild(t);
      g.appendChild(svg("rect", { "class": "bar-bot", x: left.toFixed(1), y: (H - B - hBot).toFixed(1), width: bw.toFixed(1), height: hBot.toFixed(1), rx: 1.5 }));
      g.appendChild(svg("rect", { "class": "bar-app", x: left.toFixed(1), y: (H - B - hBot - hApp).toFixed(1), width: bw.toFixed(1), height: hApp.toFixed(1), rx: 1.5 }));
      s.appendChild(g);
    });
    [0, daily.length - 1].forEach(function (i) {
      var lbl = svg("text", { "class": "chart-label", x: i === 0 ? 0 : W, y: H - 2, "text-anchor": i === 0 ? "start" : "end" });
      lbl.textContent = daily[i].d;
      s.appendChild(lbl);
    });
    return s;
  }

  function renderUsage(c, d) {
    c.appendChild(header(d.title, d.period));
    if (d.empty) { emptyState(c, d.empty); return; }
    var tiles = el("div", "tiles");
    d.tiles.forEach(function (t) {
      var tile = el("div", "tile");
      tile.appendChild(el("div", "tile-label", t.label));
      tile.appendChild(num("tile-value", t.value));
      if (t.sub) tile.appendChild(el("div", "tile-sub", t.sub));
      tiles.appendChild(tile);
    });
    c.appendChild(tiles);
    var card = el("div", "card");
    var head = el("div", "pair-top");
    head.appendChild(el("span", "eyebrow", "По дням"));
    card.appendChild(head);
    card.appendChild(usageBars(d.daily));
    var lg = el("div", "legend legend-tight");
    [["bar-bot", "бот"], ["bar-app", "приложение"]].forEach(function (x) {
      var item = el("span", "legend-item");
      item.appendChild(el("i", "swatch " + x[0]));
      item.appendChild(document.createTextNode(x[1]));
      lg.appendChild(item);
    });
    card.appendChild(lg);
    c.appendChild(card);
    if (d.boot && d.boot.length) {
      var boot = section("Запуск приложения");
      d.boot.forEach(function (it) { boot.list.appendChild(itemRow(it)); });
      c.appendChild(boot);
    }
    [["Приложение", d.app], ["Бот", d.bot]].forEach(function (x) {
      if (!x[1].length) return;
      var sec = section(x[0]);
      x[1].forEach(function (it) { sec.list.appendChild(itemRow(it)); });
      c.appendChild(sec);
    });
    c.appendChild(el("p", "footnote", "Без разбивки по людям: сколько раз открыт экран и сколькими игроками."));
  }

  // ── Экран матча (v2.168.0) ─────────────────────────────────────────────────
  // Характер матча для анимации (v2.177.0): только у ярких матчей, по данным
  // партий — камбэк, разгром, дьюсы, боссфайт. Обычный матч — без эффектов.
  function matchMood(d) {
    if (!d.winner || !d.sets.length) return "";
    var w = d.winner, l = w === "a" ? "b" : "a";
    var lostFirst = d.sets[0][l] > d.sets[0][w];
    var allWon = d.sets.every(function (s) { return s[w] > s[l]; });
    var margin = d.sets.reduce(function (sum, s) { return sum + (s[w] - s[l]); }, 0) / d.sets.length;
    var deuces = d.sets.filter(function (s) { return Math.max(s.a, s.b) > 11; }).length;
    if (d.boss) return "boss";
    if (lostFirst && d.sets.length >= 3) return "comeback";
    if (allWon && margin >= 6) return "blowout";
    if (deuces >= 2) return "deuce";
    return "";
  }

  var MOOD_LABELS = { boss: "Боссфайт", comeback: "Камбэк", blowout: "Разгром", deuce: "Дьюсы" };

  // Счёт партий из ленты («5:11») — в вид экрана матча ({a, b}) для matchMood
  function chipSets(sets) {
    return (sets || []).map(function (sc) {
      var p = String(sc).split(":");
      return { a: parseInt(p[0], 10) || 0, b: parseInt(p[1], 10) || 0 };
    });
  }

  // Партии по очереди (v2.186.0): при первом открытии матча на этом устройстве
  // партии «играются» одна за другой — полосы растут, очки набираются, счёт по
  // партиям растёт. Вся запись — около 2 с (replayTiming); нажатие — сразу итог.
  // Повторно, при «уменьшить движение» и при тихом обновлении — сразу итог.
  // (числа — внутри replayTiming: в Node, где она проверяется, переменные модуля не заданы)
  var REPLAY_KEY = "match_replay_seen_v1";

  // v2.187.0: быстрее — было до 0,8 с на партию и до 4 с на матч, «долго»
  function replayTiming(n) {
    var total = 2000, setMs = 350, gap = 60;
    var per = Math.min(setMs, Math.floor(total / Math.max(1, n)) - gap);
    return { per: Math.max(200, per), gap: gap, start: 100 };
  }

  function replaySeen(id) {
    var list = [];
    try { list = JSON.parse(window.localStorage.getItem(REPLAY_KEY) || "[]"); } catch (e) { return true; }
    if (!Array.isArray(list)) list = [];
    if (list.indexOf(id) >= 0) return true;
    list.push(id);
    try { window.localStorage.setItem(REPLAY_KEY, JSON.stringify(list.slice(-200))); } catch (e) { return true; }
    return false;
  }

  function replaySets(card, rows, aNum, bNum, d) {
    var t = replayTiming(d.sets.length);
    var won = { a: 0, b: 0 }, timers = [], finished = false;
    card.classList.add("replaying");
    aNum.textContent = "0";
    bNum.textContent = "0";
    rows.forEach(function (r) {
      r.li.classList.add("is-waiting");
      r.parts.forEach(function (p) { p.bar.style.animation = "none"; p.bar.style.width = "0"; p.pts.textContent = "0"; });
    });
    function settle(i) {
      rows[i].li.classList.remove("is-playing");
      var s = d.sets[i];
      won[s.a > s.b ? "a" : "b"] += 1;
      aNum.textContent = String(won.a);
      bNum.textContent = String(won.b);
    }
    function finish() {
      if (finished) return;
      finished = true;
      timers.forEach(clearTimeout);
      document.removeEventListener("click", skip, true);
      rows.forEach(function (r) {
        r.li.classList.remove("is-waiting", "is-playing");
        r.parts.forEach(function (p) { p.bar.style.transition = "none"; p.bar.style.width = p.width; p.pts.textContent = p.final; });
      });
      var aSets = d.sets.filter(function (x) { return x.a > x.b; }).length;
      aNum.textContent = String(aSets);
      bNum.textContent = String(d.sets.length - aSets);
      card.classList.remove("replaying");
    }
    function skip() { finish(); }
    document.addEventListener("click", skip, true);
    rows.forEach(function (r, i) {
      timers.push(setTimeout(function () {
        if (finished || !card.isConnected) { finish(); return; }
        r.li.classList.remove("is-waiting");
        r.li.classList.add("is-playing");   // кто взял партию — видно в её конце
        var start = null;
        r.parts.forEach(function (p) {
          p.bar.style.transition = "width " + t.per + "ms linear, background-color .3s";
          p.bar.style.width = p.width;
        });
        (function frame(ts) {
          if (finished) return;
          if (start === null) start = ts;
          var k = Math.min(1, (ts - start) / t.per);
          r.parts.forEach(function (p) { p.pts.textContent = String(Math.round(p.value * k)); });
          if (k < 1) requestAnimationFrame(frame);
          else { settle(i); haptic("select"); if (i === rows.length - 1) finish(); }
        })(performance.now());
      }, t.start + i * (t.per + t.gap)));
    });
  }

  function matchCardBlocks(d) {
    var aSets = d.sets.filter(function (x) { return x.a > x.b; }).length;
    var blocks = [titleBlock(d.a.name + " — " + d.b.name, d.date),
      statsBlock([[d.a.name, String(aSets), d.winner === "a" ? CARD.win : CARD.text],
        [d.b.name, String(d.sets.length - aSets), d.winner === "b" ? CARD.win : CARD.text]])];
    blocks.push(rowsBlock("Партии", d.sets.map(function (s, i) {
      return [(i + 1) + "-я партия", s.a + ":" + s.b, s.a > s.b ? CARD.text : CARD.hint];
    })));
    if (d.a.delta) blocks.push(lineBlock("Рейтинг: " + d.a.name + " " + d.a.delta + ", " + d.b.name + " " + d.b.delta));
    return blocks;
  }

  function renderMatch(c, d) {
    var mood = matchMood(d);
    // штамп — над победителем (v2.185.0; раньше всегда справа)
    var card = el("div", "card versus" + (mood ? " mood-" + mood + " stamp-" + d.winner : ""));
    if (mood) card.appendChild(el("div", "mood-stamp", MOOD_LABELS[mood]));
    var row = el("div", "versus-row");
    function side(p, mine) {
      var s = clickable(el("div", "versus-side" + (d.winner === mine ? " is-winner" : "")), "player/" + p.id);
      s.appendChild(avatar(p, "lg"));
      s.appendChild(el("div", "versus-name", p.name));
      if (p.delta) s.appendChild(el("div", "sub " + (p.delta.charAt(0) === "+" ? "tone-win" : "tone-loss"), p.delta));
      return s;
    }
    var aSets = d.sets.filter(function (x) { return x.a > x.b; }).length;
    var bSets = d.sets.length - aSets;
    var replay = d.sets.length > 1 && d.id && !reduceMotion && !app.classList.contains("quiet") && !replaySeen(d.id);
    var score = el("div", "versus-score");
    var aNum = replay ? el("div", "versus-num" + (d.winner === "a" ? " tone-win" : ""), "0")
      : num("versus-num" + (d.winner === "a" ? " tone-win" : ""), String(aSets));
    var bNum = replay ? el("div", "versus-num" + (d.winner === "b" ? " tone-win" : ""), "0")
      : num("versus-num" + (d.winner === "b" ? " tone-win" : ""), String(bSets));
    score.appendChild(aNum);
    score.appendChild(el("span", "versus-colon", ":"));
    score.appendChild(bNum);
    row.appendChild(side(d.a, "a"));
    row.appendChild(score);
    row.appendChild(side(d.b, "b"));
    card.appendChild(row);
    card.appendChild(el("div", "sub center", [d.date, d.boss ? "боссфайт" : "", d.winner ? "" : "ничья"].filter(Boolean).join(" · ")));
    c.appendChild(card);

    var sets = section("Партии");
    var rows = [];
    d.sets.forEach(function (st, i) {
      var li = el("li", "row set-row" + (replay ? "" : " stagger"));
      li.style.animationDelay = (i * 80) + "ms";
      var parts = [];
      li.appendChild(el("div", "set-n", String(i + 1)));
      var bars = el("div", "set-bars");
      var max = Math.max(st.a, st.b, 11);
      [["a", st.a, st.a > st.b], ["b", st.b, st.b > st.a]].forEach(function (x) {
        var line = el("div", "set-line");
        var bar = el("i", "set-bar set-bar-" + x[0] + (x[2] ? " is-won" : ""));
        bar.style.width = Math.round(x[1] / max * 100) + "%";
        line.appendChild(bar);
        var pts = el("span", "set-pts" + (x[2] ? " is-won" : ""), String(x[1]));
        line.appendChild(pts);
        bars.appendChild(line);
        parts.push({ bar: bar, pts: pts, width: bar.style.width, value: x[1], final: String(x[1]) });
      });
      li.appendChild(bars);
      sets.list.appendChild(li);
      rows.push({ li: li, parts: parts });
    });
    c.appendChild(sets);
    if (replay) replaySets(card, rows, aNum, bNum, d);

    if (d.reason || d.report) {
      var story = el("div", "card");
      if (d.reason) story.appendChild(el("div", "eyebrow", d.reason));
      if (d.report) story.appendChild(el("p", "feature-reason", d.report));
      c.appendChild(story);
    }
    if (d.reactions) c.appendChild(reactionBar(d.id, d.reactions));
    var h = section("Личные встречи");
    h.list.appendChild(itemRow({
      label: d.a.name + " — " + d.b.name,
      value: d.h2h.a + " : " + d.h2h.b + (d.h2h.draws ? ", ничьих " + d.h2h.draws : ""),
      note: d.h2h.total
    }));
    c.appendChild(h);
    var share = el("div", "share-row");
    share.appendChild(shareButton("match", function () { return matchCardBlocks(d); }));
    c.appendChild(share);
  }


  // ── Летопись и счета клуба (v2.177.0) ──────────────────────────────────────
  function renderChronicle(c, d) {
    c.appendChild(header("Летопись клуба", d.subtitle || ""));
    if (d.empty) { emptyState(c, d.empty); return; }
    d.months.forEach(function (mo) {
      var wrap = el("section", "group");
      wrap.appendChild(el("h2", "group-title", mo.title));
      var list = el("ul", "chronicle");
      mo.items.forEach(function (it, i) {
        var target = it.match ? "match/" + it.match.id : it.player ? "player/" + it.player.id : null;
        var li = target ? clickable(el("li", "chronicle-item"), target) : el("li", "chronicle-item");
        li.style.animationDelay = Math.min(i, 10) * 30 + "ms";
        var dot = el("div", "chronicle-dot chronicle-" + it.kind);
        dot.appendChild(icon(it.icon));
        li.appendChild(dot);
        var b = el("div", "body");
        b.appendChild(el("div", "name", it.title));
        var sub = it.sub || (it.match ? it.match.a.name + " — " + it.match.b.name + " · " + it.match.sets.join(" ") : "");
        b.appendChild(el("div", "sub", [it.date, sub].filter(Boolean).join(" · ")));
        li.appendChild(b);
        list.appendChild(li);
      });
      wrap.appendChild(list);
      c.appendChild(wrap);
    });
  }

  function renderScores(c, d) {
    c.appendChild(header("Счета клуба", d.subtitle || ""));
    if (d.empty) { emptyState(c, d.empty); return; }
    var top = el("section", "group");
    top.appendChild(el("h2", "group-title", "Самые частые счета партий"));
    var card = el("div", "card scores-top");
    var max = Math.max.apply(null, d.top.map(function (t) { return t.count; }));
    d.top.forEach(function (t, i) {
      var row = el("div", "score-row stagger");
      row.style.animationDelay = (i * 60) + "ms";
      row.appendChild(el("span", "score-big scoreboard", t.score));
      var bar = el("div", "score-bar");
      var fill = el("i");
      fill.style.width = Math.round(t.count / max * 100) + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("span", "score-count", t.count + " · " + t.share + "%"));
      card.appendChild(row);
    });
    top.appendChild(card);
    c.appendChild(top);
    var tiles = el("div", "tiles");
    d.facts.forEach(function (f) {
      var tile = el("div", "tile");
      tile.appendChild(el("div", "tile-label", f.label));
      tile.appendChild(el("div", "tile-value scoreboard", f.value));
      tile.appendChild(el("div", "tile-sub", f.sub));
      tiles.appendChild(tile);
    });
    c.appendChild(tiles);
    if (d.match_scores.length) {
      var ms = section("Счета матчей по партиям");
      d.match_scores.forEach(function (m) {
        var li = el("li", "row row-pair");
        li.appendChild(el("div", "label scoreboard", m.score));
        li.appendChild(el("div", "value", String(m.count)));
        ms.list.appendChild(li);
      });
      c.appendChild(ms);
    }
  }

  // ── «Твой месяц» (v2.177.0) ────────────────────────────────────────────────
  // История месяца карточками, как сторис: нажатие справа — дальше, слева —
  // назад, полоски сверху показывают, где ты. Открывается только самим игроком.
  function storyCard(card) {
    var box = el("div", "story-card story-" + card.kind);
    box.appendChild(el("div", "story-title", card.title));
    if (card.player) {
      var who = el("div", "story-who");
      who.appendChild(avatar(card.player, "lg"));
      who.appendChild(el("div", "story-name", card.player.name));
      box.appendChild(who);
    }
    if (card.match) box.appendChild(versusLine(card.match));
    if (card.big) {
      var big = el("div", "story-big scoreboard" + (card.up === true ? " tone-win" : card.up === false ? " tone-loss" : ""));
      big.textContent = card.big;
      box.appendChild(big);
    }
    if (card.big_label) box.appendChild(el("div", "story-label", card.big_label));
    if (card.series && card.series.length > 1) box.appendChild(storySpark(card.series));
    if (card.match) {
      var chips = setChips(card.match.sets, card.match.winner);
      chips.classList.add("story-chips");
      box.appendChild(chips);
    }
    (card.lines || []).forEach(function (line) { box.appendChild(el("div", "story-line", line)); });
    if (card.items) {
      var grid = el("div", "story-badges");
      card.items.forEach(function (a) {
        var it = el("div", "story-badge");
        var badge = el("div", "badge badge-sm " + (a.tier ? "badge-" + a.tier : "badge-earned"));
        badge.appendChild(useIcon(a.icon));
        it.appendChild(badge);
        it.appendChild(el("span", "", a.name));
        grid.appendChild(it);
      });
      box.appendChild(grid);
    }
    return box;
  }

  function storySpark(values) {
    var W = 280, H = 70;
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (hi === lo) hi += 1;
    var s = svg("svg", { "class": "story-spark", viewBox: "0 0 " + W + " " + H });
    var d = values.map(function (v, i) {
      return (i ? "L" : "M") + (4 + (W - 8) * i / (values.length - 1)).toFixed(1) + " " + (6 + (H - 12) * (1 - (v - lo) / (hi - lo))).toFixed(1);
    }).join(" ");
    s.appendChild(svg("path", { d: d }));
    return s;
  }

  function renderMonth(c, d) {
    enterFullscreen();   // v2.183.0: «Твой месяц» во весь экран, как сторис
    c.appendChild(header(d.title, ""));
    var nav = el("div", "day-nav");
    function arrow(target, name, label) {
      var b = el("button", "day-arrow");
      b.type = "button";
      b.setAttribute("aria-label", label);
      b.appendChild(icon(name));
      if (!target) b.disabled = true;
      else b.addEventListener("click", function () { haptic("select"); location.replace("#/month/" + target); });
      return b;
    }
    nav.appendChild(arrow(d.prev, "chevron-left", "Предыдущий месяц"));
    nav.appendChild(el("div", "day-label", d.label || d.ym));
    nav.appendChild(arrow(d.next, "chevron-right", "Следующий месяц"));
    c.appendChild(nav);
    if (d.empty) { emptyState(c, d.empty); return; }

    var stage = el("div", "story");
    var bars = el("div", "story-bars");
    d.cards.forEach(function () { var b = el("i"); b.appendChild(el("b")); bars.appendChild(b); });
    stage.appendChild(bars);
    var slot = el("div", "story-slot");
    stage.appendChild(slot);
    var hint = el("div", "story-hint", "Нажми справа — дальше, слева — назад");
    stage.appendChild(hint);
    c.appendChild(stage);
    var index = 0;
    function show(i) {
      index = Math.max(0, Math.min(d.cards.length - 1, i));
      slot.textContent = "";
      slot.appendChild(storyCard(d.cards[index]));
      [].forEach.call(bars.children, function (b, k) { b.className = k < index ? "is-done" : k === index ? "is-now" : ""; });
    }
    stage.addEventListener("click", function (e) {
      var rect = stage.getBoundingClientRect();
      haptic("select");
      show(e.clientX - rect.left < rect.width / 3 ? index - 1 : index + 1);
      hint.classList.add("is-hidden");
    });
    show(0);
  }

  // ── Жесты (v2.177.0) ───────────────────────────────────────────────────────
  // Свайп влево/вправо по разделам профиля и «потяни вниз — обновить». Жест
  // засчитывается, только если движение явно горизонтальное (или вертикальное
  // от самого верха страницы), иначе это обычная прокрутка.
  var SWIPE_MIN = 70, PULL_MIN = 80;

  function nextTab(route, dir) {
    var m = route.match(PLAYER_ROUTE);
    if (!m) return null;
    var keys = PROFILE_TABS.map(function (t) { return t[0]; });
    var i = keys.indexOf(m[2] || "") + dir;
    if (i < 0 || i >= keys.length) return null;
    return "player/" + m[1] + (keys[i] ? "/" + keys[i] : "");
  }

  // Потянуть вниз: только если Telegram не закроет приложение этим же жестом
  // (свайпы закрытия отключены с клиента 7.7, v2.171.0) или вне Telegram
  function pullAllowed() {
    if (!tg) return true;
    try { return !!(tg.isVersionAtLeast && tg.isVersionAtLeast("7.7")); } catch (e) { return false; }
  }

  var touch = null;
  var pullMark = null;

  // Мячик над столом (v2.186.0): пока тянешь, мяч дважды отскакивает от кромки
  // стола (высота — две дуги |sin| по ходу натяжения), отпустил на полном
  // натяжении — мяч улетает вверх, экран обновляется. Высота отскока в px.
  function ballHeight(p) { return Math.round(Math.abs(Math.sin(2 * Math.PI * Math.min(1, Math.max(0, p)))) * 26); }

  function pullScene() {
    var scene = el("div", "pull-scene");
    var ball = el("div", "pull-ball");
    ball.appendChild(useIcon("ball-tennis"));
    scene.appendChild(ball);
    scene.appendChild(el("div", "pull-table"));
    document.body.appendChild(scene);
    return { scene: scene, ball: ball, half: false };
  }

  function dropScene(mark, fly) {
    mark.scene.classList.add(fly ? "is-flying" : "is-leaving");
    setTimeout(function () { mark.scene.remove(); }, fly ? 520 : 220);
  }

  document.addEventListener("touchstart", function (e) {
    if (e.touches.length !== 1 || document.querySelector(".sheet-wrap")) { touch = null; return; }
    var t = e.touches[0];
    var inChips = e.target.closest && e.target.closest(".chips, .filters, .segmented, svg, .story");
    touch = { x: t.clientX, y: t.clientY, top: window.scrollY <= 0, swipe: !inChips };
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    if (!touch || !touch.top || !pullAllowed()) return;
    var dy = e.touches[0].clientY - touch.y;
    var dx = Math.abs(e.touches[0].clientX - touch.x);
    if (dy > 24 && dy > dx * 2) {
      if (!pullMark) pullMark = pullScene();
      var p = Math.min(1, (dy - 24) / (PULL_MIN - 24));
      pullMark.scene.style.opacity = String(Math.min(1, p * 2));
      pullMark.scene.style.transform = "translate(-50%, " + Math.round(Math.min(dy * 0.4, 36)) + "px)";
      if (!reduceMotion) {
        pullMark.ball.style.transform = "translateY(" + (-ballHeight(p)) + "px) rotate(" + Math.round(p * 360) + "deg)";
        if (p >= 0.5 && !pullMark.half) { pullMark.half = true; haptic("select"); }   // первый отскок
      }
      if (p >= 1 && !pullMark.scene.classList.contains("is-ready")) haptic("select");   // второй
      pullMark.scene.classList.toggle("is-ready", p >= 1);
    }
  }, { passive: true });

  document.addEventListener("touchend", function (e) {
    var start = touch;
    touch = null;
    var mark = pullMark;
    pullMark = null;
    if (!start) { if (mark) dropScene(mark, false); return; }
    var t = e.changedTouches[0];
    var dx = t.clientX - start.x, dy = t.clientY - start.y;
    if (start.top && pullAllowed() && dy > PULL_MIN && dy > Math.abs(dx) * 2) {
      haptic("impact");
      if (mark) dropScene(mark, true);   // мяч улетает вверх, экран обновляется
      refresh();
      return;
    }
    if (mark) dropScene(mark, false);
    if (start.swipe && Math.abs(dx) > SWIPE_MIN && Math.abs(dx) > Math.abs(dy) * 2) {
      var target = nextTab(currentRoute(), dx < 0 ? 1 : -1);
      if (target) { haptic("select"); location.replace("#/" + target); }
    }
  }, { passive: true });
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
      li.appendChild(goalRing(goal.progress, goal.target, goal.done));
      var body = el("div", "body");
      body.appendChild(el("div", "name", goal.title));
      if (goal.note) body.appendChild(el("div", "sub", goal.note));
      li.appendChild(body);
      li.appendChild(el("div", "meta goal-count", goal.done ? "готово" : goal.progress + " / " + goal.target));
      list.appendChild(li);
    });
    wrap.appendChild(list);
    var foot = [];
    if (g.all_done) foot.push("Неделя закрыта");
    if (g.streak) foot.push("Закрыто недель подряд: " + g.streak);
    if (foot.length) wrap.appendChild(el("p", "footnote goals-foot" + (g.all_done ? " is-done" : ""), foot.join(" · ")));
    return wrap;
  }

  // «Пока тебя не было»: что изменилось с прошлого захода. Время захода —
  // в памяти устройства (CloudStorage — общая для телефона и компьютера).
  // Карточка — не чаще, чем раз в SINCE_MIN_GAP_MS, и один раз за запуск.
  var VISIT_KEY = "last_visit_v1";
  var SINCE_MIN_GAP_MS = 2 * 3600 * 1000;
  var sinceChecked = false;

  function sinceCard(c, d) {
    var card = el("div", "card since");
    var top = el("div", "pair-top");
    top.appendChild(el("span", "eyebrow", d.title));
    top.appendChild(el("span", "eyebrow-meta", d.since));
    card.appendChild(top);
    var close = el("button", "hint-close");
    close.type = "button";
    close.setAttribute("aria-label", "Скрыть");
    close.appendChild(useIcon("x"));
    close.addEventListener("click", function () { haptic("select"); card.remove(); });
    card.appendChild(close);
    var list = el("ul", "list list-plain");
    d.items.forEach(function (it) { list.appendChild(itemRow(it)); });
    card.appendChild(list);
    var anchor = c.querySelector(".podium") || c.children[1] || null;
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(card, anchor);
    else c.appendChild(card);
  }

  function checkSince(c) {
    if (sinceChecked) return;
    sinceChecked = true;
    var now = Date.now();
    storageGet(VISIT_KEY, function (raw) {
      if (raw === undefined) return;                  // памяти нет — без карточки
      storageSet(VISIT_KEY, String(now));
      var last = parseInt(raw, 10);
      if (!last || now - last < SINCE_MIN_GAP_MS) return;
      api("since?t=" + Math.floor(last / 1000)).then(function (d) {
        if (d && d.items && d.items.length && c.isConnected) sinceCard(c, d);
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

  // ── Живой дизайн (v2.182.0): переходы по направлению, «переезд» строк
  // рейтинга, конфетти новой ачивки, заставка нового звания, мини-графики,
  // скелеты по форме экрана. Везде: при «уменьшить движение» — без анимаций.

  // Направление перехода: вглубь — экран въезжает справа, «Назад» — слева,
  // между вкладками — мягкое проявление. Своя история адресов: кнопка «Назад»
  // Telegram и жест «назад» приходят обычной сменой адреса.
  var navStack = [];

  // Чистая часть (проверяется в Node): [направление, новая история]
  function nextNav(stack, route, viewer) {
    var isTab = route === "" || route === "rating" || route === "player/" + viewer;
    if (stack.length && stack[stack.length - 1] === route) return ["none", stack];
    if (isTab) {
      // вернулись на вкладку, с которой уходили вглубь, — это «назад»
      return [stack.length > 1 && stack[0] === route ? "back" : "tab", [route]];
    }
    var i = stack.lastIndexOf(route);
    if (i >= 0) return ["back", stack.slice(0, i + 1)];
    return ["forward", stack.concat([route])];
  }

  function navDirection(route) {
    var r = nextNav(navStack, route, viewerId);
    navStack = r[1];
    return r[0];
  }

  // Конфетти: короткий салют из цветных лепестков вокруг элемента
  var CONFETTI_COLORS = ["var(--gold)", "var(--accent)", "var(--win)", "var(--them)", "var(--loss)"];

  function confetti(anchor) {
    if (reduceMotion || !anchor || !anchor.getBoundingClientRect) return;
    var box = anchor.getBoundingClientRect();
    var layer = el("div", "confetti");
    layer.style.left = (box.left + box.width / 2) + "px";
    layer.style.top = (box.top + box.height / 2) + "px";
    for (var i = 0; i < 18; i++) {
      var bit = el("i", "confetti-bit");
      var angle = (Math.PI * 2 * i) / 18 + Math.random() * 0.3;
      var dist = 40 + Math.random() * 46;
      bit.style.setProperty("--dx", Math.round(Math.cos(angle) * dist) + "px");
      bit.style.setProperty("--dy", Math.round(Math.sin(angle) * dist - 20) + "px");
      bit.style.setProperty("--rot", Math.round(Math.random() * 360) + "deg");
      bit.style.background = CONFETTI_COLORS[i % CONFETTI_COLORS.length];
      bit.style.animationDelay = Math.round(Math.random() * 80) + "ms";
      layer.appendChild(bit);
    }
    document.body.appendChild(layer);
    setTimeout(function () { if (layer.parentNode) layer.parentNode.removeChild(layer); }, 1400);
  }

  // Заставка «Новое звание»: один раз, при первом заходе в свой профиль после повышения
  var TITLE_KEY = "seen_title_v1";

  function celebrateTitle(head) {
    if (!head || head.title_level === undefined) return;
    storageGet(TITLE_KEY, function (raw) {
      if (raw === undefined) return;
      var seen = parseInt(raw, 10);
      storageSet(TITLE_KEY, String(head.title_level));
      if (raw === null || isNaN(seen) || head.title_level <= seen) return;
      var wrap = el("div", "sheet-wrap celebrate-wrap");
      var sheet = el("div", "sheet celebrate");
      sheet.appendChild(el("div", "eyebrow", "Новое звание"));
      var big = el("div", "celebrate-title", head.title);
      sheet.appendChild(big);
      sheet.appendChild(el("div", "sheet-text", "Рейтинг " + head.rating + " — так держать."));
      wrap.appendChild(sheet);
      wrap.addEventListener("click", function () { wrap.remove(); });
      document.body.appendChild(wrap);
      try { if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* нет вибрации */ }
      setTimeout(function () { confetti(big); }, 250);
    });
  }

  // Мини-график рейтинга в строке таблицы: последние 10 матчей
  function sparkPoints(points, W, H) {
    var lo = Math.min.apply(null, points), hi = Math.max.apply(null, points);
    var span = hi - lo || 1;
    return points.map(function (v, i) {
      return (i * W / (points.length - 1)).toFixed(1) + "," + (H - 1 - (v - lo) / span * (H - 2)).toFixed(1);
    });
  }

  function sparkline(points) {
    if (!points || points.length < 2) return null;
    var W = 46, H = 16;
    var pts = sparkPoints(points, W, H);
    var up = points[points.length - 1] >= points[0];
    var s = svg("svg", { "class": "spark " + (up ? "spark-up" : "spark-down"), viewBox: "0 0 " + W + " " + H, "aria-hidden": "true" });
    s.appendChild(svg("polyline", { points: pts.join(" ") }));
    return s;
  }

  // «Переезд» строк рейтинга: если с прошлого просмотра таблицы порядок
  // изменился, строки плавно едут со старых мест на новые (FLIP).
  var ORDER_KEY = "board_order_v1";

  function boardFlip(list, ids) {
    var order;
    try { order = JSON.parse(window.localStorage.getItem(ORDER_KEY) || "null"); } catch (e) { order = null; }
    try { window.localStorage.setItem(ORDER_KEY, JSON.stringify(ids)); } catch (e) { /* без памяти */ }
    if (reduceMotion || !Array.isArray(order) || order.join() === ids.join()) return;
    var rows = list.children;
    var height = rows.length ? rows[0].offsetHeight : 0;
    if (!height) return;
    ids.forEach(function (id, i) {
      var was = order.indexOf(id);
      if (was < 0 || was === i || !rows[i]) return;
      var row = rows[i];
      row.style.transition = "none";
      row.style.transform = "translateY(" + ((was - i) * height) + "px)";
      row.classList.add("is-moving");
    });
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        [].forEach.call(rows, function (row) {
          if (!row.classList.contains("is-moving")) return;
          row.style.transition = "transform .7s cubic-bezier(.2, .8, .2, 1)";
          row.style.transform = "";
          setTimeout(function () { row.classList.remove("is-moving"); row.style.transition = ""; }, 750);
        });
      });
    });
  }

  // Скелет по форме экрана — меньше «прыжков», когда приходят данные
  function skeletonFor(route) {
    if (route === "") return skeleton("home");
    if (route === "rating") return skeleton("rating");
    if (/^match\/\d+$/.test(route)) return skeleton("match");
    return skeleton("list");
  }

  // ── Новое (v2.183.0): «Кто с кем», цифры-табло рейтинга, полноэкранный
  // «Твой месяц», мячик «потяни, чтобы обновить».

  // «Кто с кем»: матрица пар клуба — сколько матчей и кто кого чаще обыгрывал
  function renderPairs(c, d) {
    c.appendChild(header(d.title, d.period));
    if (d.empty) { emptyState(c, d.empty); return; }
    var card = el("div", "card pairs");
    var grid = el("div", "pairs-grid");
    grid.style.setProperty("--cols", String(d.players.length));
    grid.appendChild(el("span", "pairs-corner"));
    d.players.forEach(function (p) { grid.appendChild(avatar(p, "sm")); });
    d.matrix.forEach(function (cells, ri) {
      var rowP = d.players[ri];
      grid.appendChild(avatar(rowP, "sm"));
      cells.forEach(function (cell, ci) {
        if (!cell) { grid.appendChild(el("span", "pairs-self")); return; }
        var colP = d.players[ci];
        var b = el("button", "pairs-cell heat-" + cell.lvl, cell.n ? String(cell.n) : "");
        b.type = "button";
        b.style.animationDelay = (ri * 40 + ci * 15) + "ms";
        b.setAttribute("aria-label", rowP.name + " — " + colP.name + ": " + cell.n);
        b.addEventListener("click", function () {
          var other = d.matrix[ci][ri] ? d.matrix[ci][ri].wins : 0;
          var text = cell.n ? matchesCount(cell.n) + " · счёт " + cell.wins + "–" + other : "Ещё не играли";
          sheetOpen(null, rowP.name + " — " + colP.name, text, "");
          if (String(rowP.id) === String(viewerId)) {
            var wrap = document.querySelector(".sheet");
            if (wrap) {
              var go2 = el("button", "button button-quiet", "Ты против " + colP.name);
              go2.type = "button";
              go2.addEventListener("click", function () { document.querySelector(".sheet-wrap").remove(); go("h2h/" + colP.id); });
              wrap.appendChild(go2);
            }
          }
        });
        grid.appendChild(b);
      });
    });
    card.appendChild(grid);
    c.appendChild(card);
    c.appendChild(el("p", "footnote", "Число в клетке — матчи пары. Нажмите на клетку, чтобы увидеть счёт."));
    var facts = section("Главное");
    d.facts.forEach(function (it) { facts.list.appendChild(itemRow(it)); });
    c.appendChild(facts);
  }

  function matchesCount(n) {
    var m10 = n % 10, m100 = n % 100;
    return n + " " + (m100 >= 11 && m100 <= 14 ? "матчей" : m10 === 1 ? "матч" : m10 >= 2 && m10 <= 4 ? "матча" : "матчей");
  }

  // Цифры-табло: если рейтинг изменился с прошлого просмотра этой карточки,
  // цифры «перелистываются» со старого значения на новое, как на механическом
  // табло. Иначе — обычный досчёт. Последнее увиденное — на устройстве.
  var SEEN_RATING_KEY = "seen_rating_v1";

  function odometerDigits(from, to) {
    // Чистая часть (проверяется в Node): пары [было, стало] по знакам; null — не табло
    if (from === to || from.length !== to.length) return null;
    var out = [];
    for (var i = 0; i < to.length; i++) {
      var a = from.charAt(i), b = to.charAt(i);
      var isDigit = /\d/.test(a) && /\d/.test(b);
      if (!isDigit && a !== b) return null;
      out.push(isDigit ? [parseInt(a, 10), parseInt(b, 10)] : b);
    }
    return out;
  }

  function ratingNumber(cls, text, playerId) {
    var seen = {};
    try { seen = JSON.parse(window.localStorage.getItem(SEEN_RATING_KEY) || "{}") || {}; } catch (e) { seen = {}; }
    var prev = seen[playerId];
    seen[playerId] = text;
    try { window.localStorage.setItem(SEEN_RATING_KEY, JSON.stringify(seen)); } catch (e) { /* без памяти */ }
    var digits = prev && !reduceMotion && !app.classList.contains("quiet") ? odometerDigits(prev, text) : null;
    if (!digits) return num(cls, text);
    var wrap = el("div", cls + " odometer");
    wrap.setAttribute("aria-label", text);
    // по окончании прокрутки — обычный текст: у цифр разная ширина, а колонки
    // табло одинаковые, поэтому в покое число выглядит как везде
    setTimeout(function () { wrap.classList.remove("odometer"); wrap.textContent = text; },
      250 + 600 + text.length * 80 + 150);
    digits.forEach(function (d, i) {
      if (typeof d === "string") { wrap.appendChild(el("span", "odo-sep", d)); return; }
      var col = el("span", "odo-col");
      var strip = el("span", "odo-strip");
      for (var k = 0; k <= 9; k++) strip.appendChild(el("span", "odo-digit", String(k)));
      strip.style.transform = "translateY(" + (-d[0] * 10) + "%)";
      col.appendChild(strip);
      wrap.appendChild(col);
      if (d[0] !== d[1]) {
        setTimeout(function () {
          strip.style.transition = "transform " + (0.6 + i * 0.08) + "s cubic-bezier(.2, .8, .2, 1)";
          strip.style.transform = "translateY(" + (-d[1] * 10) + "%)";
        }, 250);
      }
    });
    return wrap;
  }

  // «Твой месяц» во весь экран (Telegram 8.0+): как сторис; при уходе с экрана — обратно
  function fullscreenAvailable() {
    try { return !!(tg && tg.requestFullscreen && tg.isVersionAtLeast && tg.isVersionAtLeast("8.0")); }
    catch (e) { return false; }
  }

  function enterFullscreen() {
    if (!fullscreenAvailable() || tg.isFullscreen) return;
    try { tg.requestFullscreen(); } catch (e) { /* клиент не разрешил */ }
  }

  function leaveFullscreen() {
    try { if (tg && tg.isFullscreen && tg.exitFullscreen) tg.exitFullscreen(); } catch (e) { /* уже вышли */ }
  }

  function syncFullscreenClass() {
    root.classList.toggle("is-fullscreen", !!(tg && tg.isFullscreen));
    if (fsButton) {
      var on = !!(tg && tg.isFullscreen);
      fsButton.textContent = "";
      fsButton.appendChild(icon(on ? "minimize" : "maximize", "ico-btn"));
      fsButton.setAttribute("aria-label", on ? "Обычный размер" : "Во весь экран");
      fsButton.title = on ? "Обычный размер" : "Во весь экран";
    }
  }

  // Во весь экран всегда (v2.186.0): на телефоне — сразу при запуске (Telegram
  // 8.0+; кнопки Telegram «закрыть» и «⋮» остаются поверх, под них — отступ
  // safe area). На компьютере окно приложения Telegram не даёт «чуть увеличить»,
  // только на весь монитор — поэтому там кнопка в углу, выбор запоминается на
  // этом устройстве (localStorage, не CloudStorage: на телефоне свой режим).
  var FS_KEY = "fullscreen_desktop_v1";
  var fsButton = null;

  function isPhoneClient(platform) { return platform === "android" || platform === "ios"; }

  function desktopWantsFullscreen() {
    try { return window.localStorage.getItem(FS_KEY) === "1"; } catch (e) { return false; }
  }

  function wantFullscreen() {
    return isPhoneClient(clientInfo().platform) || desktopWantsFullscreen();
  }

  function setupFullscreen() {
    if (!fullscreenAvailable()) return;
    if (wantFullscreen()) enterFullscreen();
    if (isPhoneClient(clientInfo().platform) || fsButton) return;
    fsButton = el("button", "fs-toggle");
    fsButton.type = "button";
    fsButton.addEventListener("click", function () {
      var on = !tg.isFullscreen;
      try { window.localStorage.setItem(FS_KEY, on ? "1" : "0"); } catch (e) { /* без памяти — до закрытия */ }
      if (on) enterFullscreen(); else leaveFullscreen();
    });
    document.body.appendChild(fsButton);
    root.classList.add("has-fs-toggle");
    syncFullscreenClass();
  }

  // ── Гонка рейтинга (v2.184.0): рейтинг клуба за 30 дней — полосы игроков
  // меняются местами день за днём. Пуск/пауза и ползунок по дням.

  // Чистая часть (проверяется в Node): места игроков в кадре — индексы по убыванию
  function raceOrder(frame) {
    return frame.map(function (v, i) { return i; }).sort(function (a, b) { return frame[b] - frame[a] || a - b; });
  }

  function renderRace(c, d) {
    c.appendChild(header(d.title, d.period));
    if (d.empty) { emptyState(c, d.empty); return; }
    var ROW = 46;
    var card = el("div", "card race");
    var dayLabel = el("div", "race-day scoreboard", d.days[0]);
    card.appendChild(dayLabel);
    var track = el("div", "race-track");
    track.style.height = (d.players.length * ROW) + "px";
    var lo = Infinity, hi = -Infinity;
    d.frames.forEach(function (f) { f.forEach(function (v) { lo = Math.min(lo, v); hi = Math.max(hi, v); }); });
    lo -= (hi - lo) * 0.15 + 5;
    var bars = d.players.map(function (p) {
      var row = clickable(el("div", "race-row"), "player/" + p.id);
      var bar = el("div", "race-bar");
      bar.style.setProperty("--hue", String((p.id * 47) % 360));
      row.appendChild(avatar(p, "sm"));
      row.appendChild(el("span", "race-name", p.name));
      row.appendChild(bar);
      var val = el("span", "race-value scoreboard", "");
      row.appendChild(val);
      track.appendChild(row);
      return { row: row, bar: bar, val: val, id: p.id };
    });
    card.appendChild(track);

    var controls = el("div", "race-controls");
    var play = el("button", "button button-quiet race-play", "Пуск");
    play.type = "button";
    var slider = el("input", "race-slider");
    slider.type = "range";
    slider.min = "0";
    slider.max = String(d.days.length - 1);
    slider.value = "0";
    slider.setAttribute("aria-label", "День");
    controls.appendChild(play);
    controls.appendChild(slider);
    card.appendChild(controls);
    c.appendChild(card);
    c.appendChild(el("p", "footnote", "Полоса — рейтинг на конец дня. Нажмите на игрока, чтобы открыть профиль."));

    function show(i) {
      var frame = d.frames[i];
      var order = raceOrder(frame);
      dayLabel.textContent = d.days[i];
      order.forEach(function (col, place) {
        var b = bars[col];
        b.row.style.transform = "translateY(" + (place * ROW) + "px)";
        b.bar.style.width = Math.max(4, (frame[col] - lo) / (hi - lo) * 100) + "%";
        b.val.textContent = frame[col].toFixed(1);
        b.row.classList.toggle("is-viewer", String(b.id) === String(viewerId));
      });
      slider.value = String(i);
    }

    var timer = null, at = 0;
    function stop() { if (timer) { clearInterval(timer); timer = null; } play.textContent = at >= d.days.length - 1 ? "Сначала" : "Пуск"; }
    play.addEventListener("click", function () {
      haptic("select");
      if (timer) { stop(); return; }
      if (at >= d.days.length - 1) { at = 0; show(0); }
      play.textContent = "Пауза";
      timer = setInterval(function () {
        if (!card.isConnected) { stop(); return; }
        at += 1;
        show(at);
        if (at >= d.days.length - 1) stop();
      }, reduceMotion ? 900 : 550);
    });
    slider.addEventListener("input", function () { stop(); at = parseInt(slider.value, 10) || 0; show(at); });
    show(0);
    // сразу видно, чем закончится: если движение не хочется — показываем сегодня
    if (reduceMotion) { at = d.days.length - 1; show(at); }
  }

  // ── Маршруты ───────────────────────────────────────────────────────────────
  // [шаблон адреса, вкладка снизу, загрузка данных, отрисовка]
  function one(path) { return function () { return api(path); }; }

  var PLAYER_ROUTE = /^player\/(\d+)(?:\/(matches|achievements|style))?$/;

  var routes = [
    [/^$/, "home", one("home"), renderHome, "home"],
    [/^rating$/, "rating", one("leaderboard"), renderLeaderboard, "rating"],
    [/^h2h\/(\d+)$/, null, function (m) { return api("h2h/" + m[1]); }, function (c, d) { renderVersus(c, d); }, "h2h"],
    [/^records$/, "home", one("records"), renderRecords, "records"],
    [/^matches$/, "home", one("matches"), renderClubMatches, "matches"],
    [/^activity\/club$/, "home", one("activity/club"), renderClubActivity, "activity"],
    [/^throne$/, "home", one("throne"), renderThrone, "throne"],
    // Итоги дня (v2.176.0) — на лету за любой день: summary/day/ГГГГММДД
    [/^summary\/(day|week|month|quarter|year)(?:\/(\d{8}))?$/, "home", function (m) {
      if (m[1] !== "day") return api("summary/" + m[1]);
      var date = m[2] ? m[2].slice(0, 4) + "-" + m[2].slice(4, 6) + "-" + m[2].slice(6) : "";
      return Promise.all([api("summary/day"), api("day" + (date ? "?date=" + date : ""))])
        .then(function (r) { return { tabs: r[0].tabs, day: r[1] }; });
    }, function (c, d, m) { renderSummary(c, d, m[1]); }, "summary"],
    [/^match\/(\d+)$/, null, function (m) { return api("match/" + m[1]); }, function (c, d) { renderMatch(c, d); }, "match"],
    [/^when(?:\/(me))?$/, "home", function (m) { return api("when?who=" + (m[1] || "club")); },
      function (c, d, m) { renderWhen(c, d, m[1] || "club"); }, "when"],
    [/^usage$/, "home", one("usage"), renderUsage, "usage"],
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
      var b = el("button", "tab" + (t[0] === active ? " is-active" : ""));
      b.type = "button";
      b.appendChild(icon(t[3]));
      b.appendChild(el("span", "tab-label", t[2]));
      b.addEventListener("click", function () {
        haptic("select");
        var target = t[1] === "me" ? "player/" + viewerId : t[1];
        if (currentRoute() === target) { window.scrollTo({ top: 0, behavior: "smooth" }); return; }
        location.hash = "#/" + target;
      });
      tabbar.appendChild(b);
    });
  }

  function currentRoute() { return location.hash.replace(/^#\/?/, ""); }

  // Позиция прокрутки по адресу: возврат «назад» попадает на то же место
  var scrollPos = {};
  var renderedRoute = null;

  function finishRender(route, keepScroll) {
    renderedRoute = route;
    track(route);
    if (keepScroll) return;
    window.scrollTo(0, scrollPos[route] || 0);
  }

  function render(quiet) {
    var route = currentRoute();
    app.classList.toggle("quiet", quiet === true);
    if (renderedRoute !== null && renderedRoute !== route) scrollPos[renderedRoute] = window.scrollY;
    if (route === "me") { location.replace("#/player/" + viewerId); return; }
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
        track(route);
        return;
      }
      shell = null;
      app.textContent = "";
      app.classList.remove("is-wide");
      app.setAttribute("data-enter", quiet === true ? "none" : navDirection(route));
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
        emptyState(app, err.message, true);
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
    app.setAttribute("data-enter", quiet === true ? "none" : navDirection(route));
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
      emptyState(app, err.message, true);
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
      scrollPos[route] = y;
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
      setupFullscreen();
      if (tg.BackButton) {
        tg.BackButton.onClick(function () { history.back(); });
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
  window.addEventListener("hashchange", function () { render(); });
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
      scrollPos[""] = window.scrollY;   // игрок мог уже пролистать снимок — место сохраняется
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
