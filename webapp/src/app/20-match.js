  // ── Экран матча (v2.168.0) ─────────────────────────────────────────────────
  function renderMatch(c, d) {
    var card = el("div", "card versus");
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
  }

