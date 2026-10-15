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

  function renderRecords(c, d) {
    c.appendChild(header(d.title));
    if (d.empty) { emptyState(c, d.empty); return; }
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

  // Трон из ракеток (v2.188.0, макет утверждён владельцем): веер ракеток вместо
  // мечей Железного трона — свой рисунок, без оформления сериала. Аватар
  // чемпиона сидит на троне, над ним корона, рядом мяч.
  var THRONE_ANGLES = [-72, -48, -24, 0, 24, 48, 72];

  function throneArt() {
    var s = svg("svg", { "class": "throne-art", viewBox: "0 0 272 210", "aria-hidden": "true" });
    var g = svg("g", { transform: "translate(136 160)" });
    THRONE_ANGLES.forEach(function (a, i) {
      // k: 0 по краям, 3 в центре — центральная ракетка самая высокая и крупная
      var k = 3 - Math.abs(3 - i), dist = 88 + k * 9, rx = 18 + k * 1.3, ry = 22 + k * 1.3;
      // внешняя группа поворачивает ракетку, внутренняя «выезжает» вдоль неё
      var outer = svg("g", { transform: "rotate(" + a + ")" });
      var r = svg("g", { "class": "throne-racket" });
      outer.appendChild(r);
      // ручка уходит за спинку трона, лопасть торчит наружу
      r.appendChild(svg("rect", { x: -4.5, y: -(dist - ry + 4), width: 9, height: dist - ry - 26, rx: 3, "class": "throne-handle" }));
      r.appendChild(svg("ellipse", { cx: 0, cy: -dist, rx: rx, ry: ry, "class": i % 2 ? "throne-blade-black" : "throne-blade-red" }));
      r.style.animationDelay = (i * 60) + "ms";
      g.appendChild(outer);
    });
    [["rect", { x: -66, y: -40, width: 132, height: 66, rx: 10, "class": "throne-back" }],
     ["rect", { x: -74, y: 20, width: 148, height: 18, rx: 6, "class": "throne-seat" }],
     ["rect", { x: -62, y: 38, width: 10, height: 12, rx: 3, "class": "throne-back" }],
     ["rect", { x: 52, y: 38, width: 10, height: 12, rx: 3, "class": "throne-back" }],
     ["rect", { x: -90, y: -6, width: 26, height: 12, rx: 5, "class": "throne-seat" }],
     ["rect", { x: 64, y: -6, width: 26, height: 12, rx: 5, "class": "throne-seat" }],
     ["circle", { cx: 79, cy: -13, r: 7, "class": "throne-ball" }]   // мяч лежит на подлокотнике
    ].forEach(function (x) { g.appendChild(svg(x[0], x[1])); });
    s.appendChild(g);
    return s;
  }

  function throneCard(cur) {
    var card = clickable(el("div", "card throne-card"), "player/" + cur.player_id);
    var stage = el("div", "throne-stage");
    stage.appendChild(throneArt());
    var seat = el("div", "throne-sitter");
    seat.appendChild(icon("crown", "crown crown-big"));
    seat.appendChild(avatar({ id: cur.player_id, initial: cur.name.charAt(0).toUpperCase() }, "xl"));
    stage.appendChild(seat);
    card.appendChild(stage);
    card.appendChild(el("div", "hero-name", cur.name));
    card.appendChild(el("div", "throne-reign", ["на троне " + cur.duration, cur.defenses_text].filter(Boolean).join(" · ")));
    card.appendChild(el("div", "sub", "с " + cur.since));
    return card;
  }

  function renderThrone(c, d) {
    c.appendChild(header(d.title, d.facts.map(function (f) { return f.label + ": " + f.value; }).join(" · ")));
    if (d.empty) { emptyState(c, d.empty); return; }
    if (d.current) c.appendChild(throneCard(d.current));
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

