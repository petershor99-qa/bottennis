  // ── Экран матча (v2.168.0) ─────────────────────────────────────────────────
  // Характер матча для анимации (v2.177.0): только у ярких матчей, по данным
  // партий — камбэк, разгром, дьюсы, боссфайт. Обычный матч — без эффектов.
  function matchMood(d) {
    if (!d.winner || !d.sets.length) return "";
    var w = d.winner, l = w === "a" ? "b" : "a";
    var lostFirst = d.sets[0][l] > d.sets[0][w];
    // v2.198.0: «Камбэк» — только с 0:2 по партиям (редкий, золотой); проиграл
    // одну первую и выиграл — «Отыгрался» (было «Камбэк» и за 0:1)
    var lostTwo = d.sets.length >= 3 && lostFirst && d.sets[1][l] > d.sets[1][w];
    var allWon = d.sets.every(function (s) { return s[w] > s[l]; });
    var margin = d.sets.reduce(function (sum, s) { return sum + (s[w] - s[l]); }, 0) / d.sets.length;
    var deuces = d.sets.filter(function (s) { return Math.max(s.a, s.b) > 11; }).length;
    if (d.boss) return "boss";
    if (lostTwo) return "comeback";
    if (lostFirst && d.sets.length >= 3) return "rally";
    if (allWon && margin >= 6) return "blowout";
    if (deuces >= 2) return "deuce";
    return "";
  }

  var MOOD_LABELS = { boss: "Боссфайт", comeback: "Камбэк", rally: "Отыгрался", blowout: "Разгром", deuce: "Дьюсы" };

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
    // v2.193.0: строка ведёт на экран пары (для участника — «Ты против …»)
    h.list.appendChild(clickable(itemRow({
      label: d.a.name + " — " + d.b.name,
      value: d.h2h.a + " : " + d.h2h.b + (d.h2h.draws ? ", ничьих " + d.h2h.draws : ""),
      note: d.h2h.total
    }), "pair/" + d.a.id + "/" + d.b.id));
    c.appendChild(h);
    var share = el("div", "share-row");
    share.appendChild(shareButton("match", function () { return matchCardBlocks(d); }));
    c.appendChild(share);
  }

