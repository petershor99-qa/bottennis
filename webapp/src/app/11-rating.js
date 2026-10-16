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

  // Подсказка про жесты (v2.192.0): один раз, на сенсорном экране; пропадает и
  // сама, как только быстрое меню открыто долгим нажатием
  var GESTURE_HINT_KEY = "gesture_hint_v1";

  function gestureHint(c, before) {
    if (!("ontouchstart" in window)) return;
    storageGet(GESTURE_HINT_KEY, function (seen) {
      if (seen !== null || !before.isConnected) return;
      var card = el("div", "card hint gesture-hint");
      card.appendChild(useIcon("hand-finger", "ico-lead"));
      var b = el("div", "body");
      b.appendChild(el("div", "name", "Подержи палец на игроке"));
      b.appendChild(el("div", "sub", "Откроется быстрое меню: вызвать на матч, «Ты против …», профиль. А провести от левого края экрана — назад."));
      card.appendChild(b);
      var close = el("button", "hint-close");
      close.type = "button";
      close.setAttribute("aria-label", "Закрыть подсказку");
      close.appendChild(useIcon("x"));
      close.addEventListener("click", function () { haptic("select"); storageSet(GESTURE_HINT_KEY, "1"); card.remove(); });
      card.appendChild(close);
      c.insertBefore(card, before);
    });
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
    gestureHint(c, area);
    var ids = d.rows.map(function (row) { return row.player_id; });
    if (pendingRise && mine && pendingRise > myRow.rank) {
      animateRise(c, mine, pendingRise, myRow.rank);
      try { window.localStorage.setItem(ORDER_KEY, JSON.stringify(ids)); } catch (e) { /* без памяти */ }
    } else if (!app.classList.contains("quiet")) {
      requestAnimationFrame(function () { boardFlip(board.list, ids); });
    }
    pendingRise = null;
  }

