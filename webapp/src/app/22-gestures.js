
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

  // ── Жесты 2 (v2.190.0) ─────────────────────────────────────────────────────
  // «Назад» свайпом от левого края. На iPhone внутри Telegram системного жеста
  // «назад» нет (на Android его делает система — через кнопку «Назад» Telegram).
  // Тянешь от края — выезжает круглая стрелка; на полном натяжении она
  // «щёлкает» (вибрация, стрелка наполняется цветом); отпустил — шаг назад.
  // Отпустил раньше — стрелка уезжает обратно. На главных вкладках жеста нет.
  var EDGE_ZONE = 24;

  // натяжение 0..1; полное — 90 px (порог внутри: в Node переменные модуля не заданы)
  function edgeProgress(dx) { return Math.max(0, Math.min(1, dx / 90)); }

  function isRootRoute(route, viewer) { return route === "" || route === "rating" || route === "player/" + viewer; }

  var edge = null;

  document.addEventListener("touchstart", function (e) {
    edge = null;
    if (e.touches.length !== 1 || document.querySelector(".sheet-wrap")) return;
    var t = e.touches[0];
    if (t.clientX > EDGE_ZONE || isRootRoute(currentRoute(), viewerId)) return;
    if (touch) touch.swipe = false;   // от края — не листание разделов профиля
    edge = { x: t.clientX, y: t.clientY, mark: null, ready: false };
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    if (!edge) return;
    var t = e.touches[0];
    var dx = t.clientX - edge.x, dy = t.clientY - edge.y;
    if (!edge.mark) {
      if (Math.abs(dy) > 14 && Math.abs(dy) > dx) { edge = null; return; }   // это прокрутка
      if (dx < 10) return;
      edge.mark = el("div", "edge-back");
      edge.mark.appendChild(icon("chevron", "edge-back-ico"));
      document.body.appendChild(edge.mark);
    }
    var p = edgeProgress(dx);
    edge.mark.style.top = Math.round(t.clientY - 22) + "px";
    edge.mark.style.transform = "translateX(" + Math.round(p * 64) + "px) scale(" + (0.7 + p * 0.3).toFixed(2) + ")";
    edge.mark.style.opacity = String(Math.min(1, p * 1.6));
    if (p >= 1 && !edge.ready) haptic("select");
    edge.ready = p >= 1;
    edge.mark.classList.toggle("is-ready", edge.ready);
  }, { passive: true });

  function edgeEnd(goBack) {
    var cur = edge;
    edge = null;
    if (!cur || !cur.mark) return;
    var mark = cur.mark;
    if (goBack && cur.ready) {
      mark.classList.add("is-going");
      haptic("impact");
      history.back();
    } else {
      mark.style.transform = "translateX(0) scale(0.7)";
      mark.style.opacity = "0";
    }
    setTimeout(function () { mark.remove(); }, 260);
  }

  document.addEventListener("touchend", function () { edgeEnd(true); }, { passive: true });
  document.addEventListener("touchcancel", function () { edgeEnd(false); }, { passive: true });

  // Долгое нажатие на игрока (рейтинг, лента, подиум, «Кто с кем», гонка —
  // везде, где нажатие на игрока открывает профиль): быстрое меню «Вызвать на
  // матч / Ты против … / Профиль». На компьютере — правая кнопка мыши. Пока
  // палец держит, строка чуть «вдавливается»; сработало — вибрация, и отпускание
  // пальца уже не считается нажатием.
  var LONG_PRESS_MS = 450;
  var press = null;

  function playerFromRoute(route) {
    var m = /^player\/(\d+)(?:\/|$)/.exec(route || "");
    return m ? m[1] : null;
  }

  function cancelPress() {
    if (!press) return;
    clearTimeout(press.timer);
    press.node.classList.remove("is-pressing");
    press = null;
  }

  function pressTarget(target) {
    var node = target && target.closest ? target.closest("[data-route]") : null;
    var id = node ? playerFromRoute(node.getAttribute("data-route")) : null;
    return id ? { node: node, id: id } : null;
  }

  document.addEventListener("touchstart", function (e) {
    cancelPress();
    if (e.touches.length !== 1 || document.querySelector(".sheet-wrap")) return;
    var hit = pressTarget(e.target);
    if (!hit) return;
    var t = e.touches[0];
    hit.node.classList.add("is-pressing");
    press = { node: hit.node, id: hit.id, x: t.clientX, y: t.clientY, timer: setTimeout(function () {
      var cur = press;
      press = null;
      cur.node.classList.remove("is-pressing");
      suppressClicks();
      haptic("impact");
      playerSheet(cur.id);
    }, LONG_PRESS_MS) };
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    if (!press) return;
    var t = e.touches[0];
    if (Math.abs(t.clientX - press.x) > 10 || Math.abs(t.clientY - press.y) > 10) cancelPress();
  }, { passive: true });
  document.addEventListener("touchend", cancelPress, { passive: true });
  document.addEventListener("touchcancel", cancelPress, { passive: true });

  document.addEventListener("contextmenu", function (e) {
    var hit = pressTarget(e.target);
    if (!hit) return;
    e.preventDefault();   // без меню браузера; на телефоне быстрое меню открывает таймер
    if (e.pointerType === "touch" || document.querySelector(".sheet-wrap")) return;
    playerSheet(hit.id);
  });

  // Быстрое меню игрока. Шапка (имя, звание и рейтинг) — из того же ответа, что
  // и профиль (обычно уже в кэше), действия доступны сразу.
  function playerSheet(id) {
    var old = document.querySelector(".sheet-wrap");
    if (old) old.remove();
    var own = String(id) === String(viewerId);
    var wrap = el("div", "sheet-wrap");
    var sheet = el("div", "sheet quick-sheet");
    var head = el("div", "quick-head");
    var av = avatar({ id: Number(id), initial: "" }, "lg");
    var who = el("div", "quick-who");
    var name = el("div", "sheet-title", own ? "Ты" : "");
    var sub = el("div", "quick-sub", "");
    who.appendChild(name);
    who.appendChild(sub);
    head.appendChild(av);
    head.appendChild(who);
    sheet.appendChild(head);
    var actions = el("div", "quick-actions");
    sheet.appendChild(actions);

    function action(text, ico, quiet, onTap) {
      var b = el("button", "button" + (quiet ? " button-quiet" : ""));
      b.type = "button";
      b.appendChild(icon(ico, "ico-btn"));
      b.appendChild(document.createTextNode(text));
      b.addEventListener("click", function () { wrap.remove(); onTap(); });
      actions.appendChild(b);
      return b;
    }

    var versus = null;
    if (own) {
      action("Мой профиль", "user", false, function () { go("player/" + id); });
      action("Твой месяц", "calendar", true, function () { go("month"); });
    } else {
      actions.appendChild(challengeButton(id));
      versus = action("Ты против", "swords", true, function () { go("h2h/" + id); });
      action("Профиль", "user", true, function () { go("player/" + id); });
    }
    api("player/" + id).then(function (d) {
      if (!d || !d.head) return;
      if (!own) name.textContent = d.head.name;
      if (!av.classList.contains("has-photo")) av.textContent = d.head.initial;
      sub.textContent = d.head.rating + " · " + d.head.subtitle;
      if (versus) versus.lastChild.textContent = "Ты против " + d.head.name;
    }, function () { /* без шапки меню всё равно работает */ });

    sheet.addEventListener("click", function (e) { e.stopPropagation(); });
    wrap.addEventListener("click", function () { wrap.remove(); });
    wrap.appendChild(sheet);
    document.body.appendChild(wrap);
    haptic("select");
  }
