  // ── Разделы клуба ──────────────────────────────────────────────────────────
  function clubMatchRow(m) {
    var li = m.id ? clickable(el("li", "row"), "match/" + m.id) : el("li", "row");
    var body = el("div", "body");
    var names = el("div", "name name-pair");
    names.appendChild(el("span", m.winner === "a" ? "win" : "", m.a));
    names.appendChild(document.createTextNode(" — "));
    names.appendChild(el("span", m.winner === "b" ? "win" : "", m.b));
    body.appendChild(names);
    body.appendChild(el("div", "sub", [m.date, m.score, m.winner ? "" : "ничья"].filter(Boolean).join(" · ")));
    li.appendChild(body);
    return li;
  }

  function renderClubMatches(c, d) {
    c.appendChild(header(d.title, d.subtitle));
    if (d.empty) { emptyState(c, d.empty); return; }
    var s = section();
    d.matches.forEach(function (m) { s.list.appendChild(clubMatchRow(m)); });
    c.appendChild(s);
    var next = d.next_offset;
    if (next !== null) {
      var btn = el("button", "button button-quiet", "Показать ещё");
      btn.type = "button";
      btn.addEventListener("click", function () {
        btn.disabled = true;
        api("matches?offset=" + next).then(function (more) {
          more.matches.forEach(function (m) { s.list.appendChild(clubMatchRow(m)); });
          next = more.next_offset;
          btn.disabled = false;
          if (next === null) btn.remove();
        }, function () { btn.disabled = false; });
      });
      c.appendChild(btn);
    }
  }

  // Витрина рекордов (v2.199.0, макет и тексты утверждены владельцем): главный
  // рекорд золотой карточкой, «Под угрозой» — кто догоняет, плитки, «Рекордсмены»;
  // остальные рекорды — прежними строками ниже. Числа досчитываются, плитки
  // появляются по очереди (с «уменьшить движение» — сразу).
  // Пара (дерби, длинный матч, апсет) — аватары внахлёст и имена одной строкой,
  // иначе в узкой плитке второе имя переносилось отдельно от аватара
  function recordHolders(r) {
    var line = el("div", "rec-holders" + (r.holders.length > 1 ? " is-pair" : ""));
    var faces = el("span", "rec-faces");
    r.holders.forEach(function (p) { faces.appendChild(avatar(p, "sm")); });
    line.appendChild(faces);
    var sep = r.key === "upset" ? " → " : " vs ";
    line.appendChild(el("span", "rec-name", r.holders.map(function (p) { return p.name; }).join(sep)));
    return line;
  }

  function recordHero(r) {
    var card = r.route ? clickable(el("div", "card rec-hero"), r.route) : el("div", "card rec-hero");
    var top = el("div", "rec-hero-top");
    top.appendChild(icon("trophy", "ico-gold"));
    top.appendChild(el("span", "", "Главный рекорд"));
    card.appendChild(top);
    card.appendChild(el("div", "rec-hero-title", r.title));
    var value = el("div", "rec-hero-value");
    value.appendChild(num("scoreboard", r.value));
    value.appendChild(el("span", "rec-unit", r.unit));
    card.appendChild(value);
    var foot = el("div", "rec-hero-foot");
    foot.appendChild(recordHolders(r));
    if (r.held) foot.appendChild(el("span", "rec-held", r.held));
    card.appendChild(foot);
    return card;
  }

  function recordThreats(list) {
    var wrap = el("section", "group");
    wrap.appendChild(el("h2", "group-title", "Под угрозой"));
    var card = el("div", "card rec-threats");
    list.forEach(function (t) {
      var row = clickable(el("div", "rec-threat"), "player/" + t.player_id);
      row.appendChild(el("div", "rec-threat-label", t.label));
      var bar = el("div", "rec-bar");
      var fill = el("i", "rec-bar-fill rec-tone-" + t.tone);
      fill.style.width = Math.round(t.progress * 100) + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("div", "rec-threat-note", t.note));
      card.appendChild(row);
    });
    wrap.appendChild(card);
    return wrap;
  }

  function recordTile(r, i) {
    var cls = "rec-tile rec-tile-" + r.key + " stagger";
    var t = r.route ? clickable(el("div", cls), r.route) : el("div", cls);
    t.style.animationDelay = (i * 50) + "ms";
    if (r.new) t.appendChild(el("span", "rec-new", "новый"));
    t.appendChild(icon(r.icon, "rec-ico rec-ico-" + r.key));
    t.appendChild(el("div", "rec-tile-title", r.title));
    var value = el("div", "rec-tile-value");
    value.appendChild(num("scoreboard", r.value));
    value.appendChild(el("span", "rec-unit", r.unit));
    t.appendChild(value);
    if (r.holders.length) t.appendChild(recordHolders(r));
    if (r.date && !r.holders.length) t.appendChild(el("div", "sub", r.date));
    return t;
  }

  function recordHoldersRow(list) {
    var wrap = el("section", "group");
    wrap.appendChild(el("h2", "group-title", "Рекордсмены"));
    var row = el("div", "rec-champs");
    list.forEach(function (p, i) {
      var cell = clickable(el("div", "rec-champ" + (i === 0 ? " is-top" : "")), "player/" + p.id);
      cell.appendChild(avatar(p, "md"));
      cell.appendChild(el("div", "rec-champ-name", p.name));
      cell.appendChild(el("div", "rec-champ-count", String(p.count)));
      row.appendChild(cell);
    });
    wrap.appendChild(row);
    return wrap;
  }

  function renderRecords(c, d) {
    c.appendChild(header(d.title, d.summary || ""));
    if (d.empty) { emptyState(c, d.empty); return; }
    var s0 = d.showcase;
    if (s0) {
      if (s0.hero) c.appendChild(recordHero(s0.hero));
      if (s0.threats && s0.threats.length) c.appendChild(recordThreats(s0.threats));
      if (s0.tiles && s0.tiles.length) {
        var grid = el("div", "rec-tiles");
        s0.tiles.forEach(function (r, i) { grid.appendChild(recordTile(r, i)); });
        c.appendChild(grid);
      }
      if (s0.holders && s0.holders.length > 1) c.appendChild(recordHoldersRow(s0.holders));
    }
    d.categories.forEach(function (cat) {
      var s = section(cat.title);
      cat.items.forEach(function (it) { s.list.appendChild(itemRow(it)); });
      c.appendChild(s);
    });
  }

  function renderClubActivity(c, d) {
    c.appendChild(header(d.title, d.period + " · " + d.total));
    var s = section();
    var li = el("li", "row row-chart");
    li.appendChild(heatmap(d.days));
    s.list.appendChild(li);
    c.appendChild(s);
    c.appendChild(legend(d));
  }

  // Трон (v2.191.0): картинка владельца (webapp/static/throne.webp), чемпион
  // «сидит» на троне — аватар в золотой рамке с короной; за троном мягкое
  // золотое свечение и искры. Таймер правления тикает каждую секунду. Сменился
  // чемпион с прошлого визита на этом устройстве — корона падает на нового с
  // салютом. Ниже — претендент (тот же, что у боссфайта) и летопись трона.
  var SEEN_CHAMPION_KEY = "seen_champion_v1";

  // Чистые части (проверяются в Node)
  function reignClock(ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    var d = Math.floor(s / 86400);
    s -= d * 86400;
    var h = Math.floor(s / 3600);
    s -= h * 3600;
    var m = Math.floor(s / 60);
    s -= m * 60;
    function two(x) { return (x < 10 ? "0" : "") + x; }
    return (d ? d + " д " : "") + two(h) + ":" + two(m) + ":" + two(s);
  }

  // Доли полосы летописи: по длительности правлений, не меньше 3% у каждого
  function timelineShares(items, now) {
    var spans = items.map(function (r) { return Math.max(0, (r.end_ts || now) - r.start_ts); });
    var total = spans.reduce(function (a, b) { return a + b; }, 0) || 1;
    var raw = spans.map(function (x) { return Math.max(3, x / total * 100); });
    var sum = raw.reduce(function (a, b) { return a + b; }, 0);
    return raw.map(function (x) { return Math.round(x / sum * 10000) / 100; });
  }

  function throneSparks() {
    var box = el("div", "throne-sparks");
    for (var i = 0; i < 12; i++) {
      var s = el("i", "throne-spark");
      s.style.setProperty("--x", (12 + (i * 37) % 76) + "%");
      s.style.setProperty("--drift", ((i % 2 ? 1 : -1) * (6 + (i * 13) % 18)) + "px");
      s.style.animationDelay = ((i * 0.53) % 3.2).toFixed(2) + "s";
      box.appendChild(s);
    }
    return box;
  }

  function throneCard(cur, art, prev) {
    var card = clickable(el("div", "card throne-card"), "player/" + cur.player_id);
    var seen = null;
    try {
      seen = window.localStorage.getItem(SEEN_CHAMPION_KEY);
      window.localStorage.setItem(SEEN_CHAMPION_KEY, String(cur.player_id));
    } catch (e) { /* без памяти — без анимации смены */ }
    var newKing = seen !== null && seen !== String(cur.player_id);
    if (newKing) {
      card.classList.add("is-new-king");
      card.appendChild(el("div", "throne-news", prev ? "Трон сменился: " + prev + " → " + cur.name : "Новый чемпион"));
    }

    var stage = el("div", "throne-stage" + (art ? "" : " no-art"));
    stage.appendChild(el("div", "throne-glow"));
    if (art) {
      var img = el("img", "throne-img");
      img.alt = "";
      img.decoding = "async";
      img.width = 540;
      img.height = 786;
      img.addEventListener("error", function () { stage.classList.add("no-art"); img.remove(); });
      img.src = art;
      stage.appendChild(img);
    }
    stage.appendChild(throneSparks());
    var seat = el("div", "throne-sitter");
    seat.appendChild(icon("crown", "crown crown-big"));
    seat.appendChild(avatar({ id: cur.player_id, initial: cur.name.charAt(0).toUpperCase() }, "xl"));
    stage.appendChild(seat);
    card.appendChild(stage);
    card.appendChild(el("div", "hero-name", cur.name));

    var reign = el("div", "throne-reign");
    reign.appendChild(document.createTextNode("на троне "));
    var clock = el("span", "throne-clock", cur.since_ts ? reignClock(Date.now() - cur.since_ts) : cur.duration);
    reign.appendChild(clock);
    if (cur.defenses_text) reign.appendChild(document.createTextNode(" · " + cur.defenses_text));
    card.appendChild(reign);
    card.appendChild(el("div", "sub", "с " + cur.since));
    if (cur.since_ts) {
      var tick = setInterval(function () {
        if (!card.isConnected) { clearInterval(tick); return; }
        clock.textContent = reignClock(Date.now() - cur.since_ts);
      }, 1000);
    }
    if (newKing) setTimeout(function () { haptic("impact"); confetti(seat); }, reduceMotion ? 0 : 900);
    return card;
  }

  function contenderCard(k) {
    var card = el("div", "card contender contender-" + k.kind);
    var row = clickable(el("div", "contender-row"), "player/" + k.player_id);
    row.appendChild(avatar({ id: k.player_id, initial: k.initial }, "md"));
    var body = el("div", "body");
    body.appendChild(el("div", "eyebrow", k.title));
    body.appendChild(el("div", "name", k.name));
    body.appendChild(el("div", "sub", k.text));
    row.appendChild(body);
    row.appendChild(el("div", "contender-rating scoreboard", k.rating));
    card.appendChild(row);
    var me = String(viewerId);
    if (k.kind === "challenger" && String(k.player_id) === me) {
      var b = challengeButton(k.champion_id);
      b.lastChild.textContent = "Вызвать чемпиона на боссфайт";
      card.appendChild(b);
    } else if (String(k.champion_id) === me) {
      card.appendChild(el("div", "sub contender-note", k.kind === "challenger"
        ? "Может вызвать тебя на боссфайт: победит — трон его."
        : "Претендента пока нет — трон в безопасности."));
    }
    return card;
  }

  function timelineCard(items) {
    var card = el("div", "card throne-timeline");
    var head = el("div", "pair-top");
    head.appendChild(el("span", "eyebrow", "Летопись трона"));
    head.appendChild(el("span", "eyebrow-meta", "нажми на отрезок"));
    card.appendChild(head);
    var bar = el("div", "timeline-bar");
    var shares = timelineShares(items, Date.now());
    items.forEach(function (r, i) {
      var seg = el("button", "timeline-seg" + (r.end_ts ? "" : " is-now"));
      seg.type = "button";
      seg.style.width = shares[i] + "%";
      seg.style.setProperty("--hue", String((r.player_id * 47) % 360));
      seg.style.animationDelay = (i * 70) + "ms";
      seg.setAttribute("aria-label", r.name + ": " + r.duration);
      seg.addEventListener("click", function () {
        sheetOpen(null, r.name, r.period + " · " + r.duration, r.end_ts ? "" : "Правит сейчас");
      });
      bar.appendChild(seg);
    });
    card.appendChild(bar);
    var ends = el("div", "timeline-ends");
    ends.appendChild(el("span", "", items[0].period.split(" – ")[0]));
    ends.appendChild(el("span", "", "сейчас"));
    card.appendChild(ends);
    // кто сколько раз правил — по цвету отрезков
    var count = {}, order = [];
    items.forEach(function (r) {
      if (!count[r.player_id]) { count[r.player_id] = { name: r.name, n: 0 }; order.push(r.player_id); }
      count[r.player_id].n += 1;
    });
    var legend = el("div", "timeline-legend");
    order.forEach(function (id) {
      var it = el("span", "legend-item");
      var dot = el("i", "timeline-dot");
      dot.style.setProperty("--hue", String((id * 47) % 360));
      it.appendChild(dot);
      it.appendChild(document.createTextNode(count[id].name + (count[id].n > 1 ? " ×" + count[id].n : "")));
      legend.appendChild(it);
    });
    card.appendChild(legend);
    return card;
  }

  function renderThrone(c, d) {
    c.appendChild(header(d.title, d.facts.map(function (f) { return f.label + ": " + f.value; }).join(" · ")));
    if (d.empty) { emptyState(c, d.empty); return; }
    var timeline = d.timeline || [];
    var prev = timeline.length > 1 ? timeline[timeline.length - 2].name : "";
    if (d.current) c.appendChild(throneCard(d.current, d.art, prev));
    if (d.contender) c.appendChild(contenderCard(d.contender));
    if (d.records && d.records.length) {
      var tiles = el("div", "tiles throne-records");
      d.records.forEach(function (r) {
        var t = el("div", "tile");
        t.appendChild(el("div", "tile-label", r.label));
        t.appendChild(el("div", "tile-value throne-record", r.value));
        tiles.appendChild(t);
      });
      c.appendChild(tiles);
    }
    if (timeline.length > 1) c.appendChild(timelineCard(timeline));
    if (d.reigns.length) {
      var s = section("Прошлые правления");
      d.reigns.forEach(function (r) {
        var li = clickable(el("li", "row"), "player/" + r.player_id);
        li.appendChild(icon("crown", "ico-lead ico-gold"));
        var body = el("div", "body");
        body.appendChild(el("div", "name", r.name));
        body.appendChild(el("div", "sub", r.period + " · " + r.duration));
        if (r.note) body.appendChild(el("div", "sub", r.note));
        li.appendChild(body);
        s.list.appendChild(li);
      });
      c.appendChild(s);
    }
  }

