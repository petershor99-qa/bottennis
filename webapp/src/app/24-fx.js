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

  // v2.208.0: «Назад» (кнопка Telegram, свайп от края). Если в приложении есть
  // предыдущий экран — назад по истории; если экран открыт сразу по ссылке из бота
  // («Матч», «Рейтинг в приложении») и вернуться некуда — на главную. Раньше
  // нажатие в этом случае не делало ничего: видимая кнопка «Назад» была мёртвой.
  function canGoBack(stack) { return stack.length > 1; }

  // v2.211.0: раздел своего профиля («Стиль», «Матчи») — назад на сам профиль, не на главную
  function backFallback(route) {
    var m = String(route).match(/^(player\/\d+)\/[a-z]+$/);
    return "#/" + (m ? m[1] : "");
  }

  function appBack() {
    var fallback = backFallback(currentRoute());
    if (!canGoBack(navStack)) { location.replace(fallback); return; }
    var before = location.hash;
    history.back();
    // в истории WebView может не оказаться записи (повторный запуск из бота подменил адрес):
    // тогда «Назад» молчала — через мгновение, если адрес не сменился, идём по запасному пути
    setTimeout(function () { if (location.hash === before) location.replace(fallback); }, 400);
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

  // Мячики от кольца закрытой цели (v2.200.0)
  function ballBurst(anchor) {
    if (reduceMotion || !anchor || !anchor.getBoundingClientRect) return;
    var box = anchor.getBoundingClientRect();
    var layer = el("div", "confetti");
    layer.style.left = (box.left + box.width / 2) + "px";
    layer.style.top = (box.top + box.height / 2) + "px";
    for (var i = 0; i < 10; i++) {
      var ball = el("i", "ball-bit");
      var angle = (Math.PI * 2 * i) / 10 + Math.random() * 0.4;
      var dist = 26 + Math.random() * 26;
      ball.style.setProperty("--dx", Math.round(Math.cos(angle) * dist) + "px");
      ball.style.setProperty("--dy", Math.round(Math.sin(angle) * dist) + "px");
      ball.style.animationDelay = Math.round(Math.random() * 60) + "ms";
      layer.appendChild(ball);
    }
    document.body.appendChild(layer);
    setTimeout(function () { if (layer.parentNode) layer.parentNode.removeChild(layer); }, 1000);
  }

  function hapticSuccess() {
    try { if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* нет вибрации */ }
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

