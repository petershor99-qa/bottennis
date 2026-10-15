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
    var score = el("div", "versus-score");
    score.appendChild(num("versus-num" + (d.winner === "a" ? " tone-win" : ""), String(aSets)));
    score.appendChild(el("span", "versus-colon", ":"));
    score.appendChild(num("versus-num" + (d.winner === "b" ? " tone-win" : ""), String(bSets)));
    row.appendChild(side(d.a, "a"));
    row.appendChild(score);
    row.appendChild(side(d.b, "b"));
    card.appendChild(row);
    card.appendChild(el("div", "sub center", [d.date, d.boss ? "боссфайт" : "", d.winner ? "" : "ничья"].filter(Boolean).join(" · ")));
    c.appendChild(card);

    var sets = section("Партии");
    d.sets.forEach(function (st, i) {
      var li = el("li", "row set-row stagger");
      li.style.animationDelay = (i * 80) + "ms";
      li.appendChild(el("div", "set-n", String(i + 1)));
      var bars = el("div", "set-bars");
      var max = Math.max(st.a, st.b, 11);
      [["a", st.a, st.a > st.b], ["b", st.b, st.b > st.a]].forEach(function (x) {
        var line = el("div", "set-line");
        var bar = el("i", "set-bar set-bar-" + x[0] + (x[2] ? " is-won" : ""));
        bar.style.width = Math.round(x[1] / max * 100) + "%";
        line.appendChild(bar);
        line.appendChild(el("span", "set-pts" + (x[2] ? " is-won" : ""), String(x[1])));
        bars.appendChild(line);
      });
      li.appendChild(bars);
      sets.list.appendChild(li);
    });
    c.appendChild(sets);

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

