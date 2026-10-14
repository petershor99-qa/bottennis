
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
