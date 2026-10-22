  // ── Тема, вибрация ─────────────────────────────────────────────────────────
  // Параметры запуска из адреса: «#tgWebAppData=…&tgWebAppVersion=…» -> объект.
  // Разбор как у telegram-web-app.js; адрес-маршрут «#/rating» — пустой объект.
  // v2.203.1: повторный запуск в уже открытом приложении — Telegram меняет только
  // хвост адреса после # (#tgWebAppData=…), страница не перезагружается. Экран
  // берётся из ?r= (как при обычном старте); null — это не запуск.
  function relaunchRoute(hash, search) {
    if (!parseLaunchParams(hash).tgWebAppData) return null;
    var r = null;
    try { r = new URLSearchParams(search || "").get("r"); } catch (e) { r = null; }
    return r !== null && /^[a-z0-9_\/]{0,60}$/.test(r) ? r : "";
  }

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
  // После долгого нажатия (v2.190.0) отпускание того же пальца — не нажатие: ни
  // по фону, ни по кнопке меню, всплывшего под пальцем. Новое касание сбрасывает.
  var swallowClick = false;

  function suppressClicks() { swallowClick = true; }

  function tapGuard(now, target, state) {
    if (now - state.multiTouchAt < 400) return false;
    if (target && target === state.lastTap.node && now - state.lastTap.at < 350) return false;
    state.lastTap = { node: target, at: now };
    return true;
  }

  if (typeof document !== "undefined") {
    var noteTouches = function (e) { if (e.touches && e.touches.length > 1) multiTouchAt = Date.now(); };
    document.addEventListener("touchstart", function (e) { swallowClick = false; noteTouches(e); }, { passive: true, capture: true });
    document.addEventListener("touchmove", noteTouches, { passive: true, capture: true });
    document.addEventListener("click", function (e) {
      if (swallowClick) { swallowClick = false; e.stopPropagation(); e.preventDefault(); return; }
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
    node.setAttribute("data-route", route);   // долгое нажатие на игрока (v2.190.0)
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

  // Длинные списки порциями (v2.189.0): на клубе в тысячи матчей история игрока
  // рисовала все строки сразу (5000 элементов) — первые step, остальное по кнопке
  var LIST_STEP = 40;

  function appendInChunks(list, items, make, step) {
    step = step || LIST_STEP;
    var at = 0;
    var more = null;
    function next() {
      var end = Math.min(items.length, at + step);
      var frag = document.createDocumentFragment();
      for (; at < end; at++) frag.appendChild(make(items[at], at));
      if (more) list.insertBefore(frag, more); else list.appendChild(frag);
      var left = items.length - at;
      if (!left) { if (more) more.remove(); return; }
      if (!more) {
        more = el("li", "more-row");
        var b = el("button", "more", "");
        b.type = "button";
        b.addEventListener("click", function () { haptic("select"); next(); });
        more.appendChild(b);
        list.appendChild(more);
      }
      more.firstChild.textContent = "Показать ещё (" + left + ")";
    }
    next();
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

  function emptyState(container, text, isError, err) {
    var view = isError ? errorView(err && err.status, text, !!(tg && tg.close)) : null;
    var box = el("div", "state" + (isError ? " state-error" : ""));
    box.appendChild(isError ? useIcon("info-circle", "state-ico") : tableArt());
    box.appendChild(el("p", "state-text", view ? view.text : text));
    if (view && view.action !== "none") {
      var again = el("button", "more", view.label);
      again.type = "button";
      again.addEventListener("click", function () {
        haptic("impact");
        if (view.action === "close") { try { tg.close(); } catch (e) { /* закрыть нечем */ } return; }
        if (view.action === "home") { location.hash = "#/"; return; }
        apiCache = {}; shell = null; render();
      });
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

  function num(cls, text) {
    var n = el("div", cls);
    countUp(n, text);
    // v2.204.0: слово в плитке («Поражение») не рвётся посередине — уменьшается шрифт
    if (/(^| )tile-value( |$)/.test(cls) && !/\d/.test(text)) { n.classList.add("is-word"); fitWord(n); }
    return n;
  }

  // Шрифт меньше на 1 px, пока слово не влезет в ширину (не мельче minPx)
  function fitWord(node, minPx) {
    var tries = 0;
    requestAnimationFrame(function fit() {
      if (!node.isConnected || !node.clientWidth) { if (++tries < 20) requestAnimationFrame(fit); return; }
      var size = parseFloat(window.getComputedStyle(node).fontSize) || 28;
      while (node.scrollWidth > node.clientWidth + 1 && size > (minPx || 13)) {
        size -= 1;
        node.style.fontSize = size + "px";
      }
    });
  }

  // v2.201.0: «+» — победа, «−» — поражение, «0.0» — без цвета (ничья с нулевой дельтой)
  function deltaTone(text) {
    var c = String(text || "").charAt(0);
    return c === "+" ? "tone-win" : (c === "−" || c === "-") ? "tone-loss" : "";
  }

  // Досчитать число от одного значения до другого (рейтинг с прошлого захода)
  function countBetween(node, from, to, decimals) {
    if (reduceMotion) { node.textContent = to.toFixed(decimals); return; }
    var start = null, duration = 1100;
    function frame(ts) {
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / duration);
      node.textContent = (from + (to - from) * (1 - Math.pow(1 - p, 3))).toFixed(decimals);
      if (p < 1) requestAnimationFrame(frame);
    }
    node.textContent = from.toFixed(decimals);
    requestAnimationFrame(frame);
  }

