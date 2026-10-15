
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
