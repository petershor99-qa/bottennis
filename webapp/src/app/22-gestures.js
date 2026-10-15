
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
      if (!pullMark) {
        // v2.183.0: мячик вместо кружка — крутится, пока тянешь
        pullMark = el("div", "pull-mark");
        pullMark.appendChild(useIcon("ball-tennis"));
        document.body.appendChild(pullMark);
      }
      var p = Math.min(1, dy / PULL_MIN);
      pullMark.style.opacity = String(p);
      pullMark.style.transform = "translate(-50%, " + Math.min(dy * 0.5, 50) + "px) rotate(" + Math.round(p * 270) + "deg)";
      pullMark.classList.toggle("is-ready", p >= 1);
    }
  }, { passive: true });

  document.addEventListener("touchend", function (e) {
    var start = touch;
    touch = null;
    var mark = pullMark;
    pullMark = null;
    if (mark) mark.remove();
    if (!start) return;
    var t = e.changedTouches[0];
    var dx = t.clientX - start.x, dy = t.clientY - start.y;
    if (start.top && pullAllowed() && dy > PULL_MIN && dy > Math.abs(dx) * 2) {
      haptic("impact");
      // мячик подпрыгивает, пока экран обновляется (не меньше полсекунды)
      var ball = el("div", "pull-mark is-bouncing");
      ball.appendChild(useIcon("ball-tennis"));
      document.body.appendChild(ball);
      var started = Date.now();
      var done = function () {
        setTimeout(function () { ball.remove(); }, Math.max(0, 500 - (Date.now() - started)));
      };
      var r = refresh();
      if (r && r.then) r.then(done, done); else done();
      return;
    }
    if (start.swipe && Math.abs(dx) > SWIPE_MIN && Math.abs(dx) > Math.abs(dy) * 2) {
      var target = nextTab(currentRoute(), dx < 0 ? 1 : -1);
      if (target) { haptic("select"); location.replace("#/" + target); }
    }
  }, { passive: true });
