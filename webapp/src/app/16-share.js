  // ── «Поделиться» картинкой (v2.176.0) ──────────────────────────────────────
  // Карточка рисуется на canvas, бот присылает её игроку в чат — оттуда её можно
  // переслать куда угодно (работает на любом клиенте Telegram).
  var CARD = { W: 1080, pad: 72, bg1: "#0A0F1F", bg2: "#16204A", text: "#F2F4FA", hint: "#8A93AD",
    accent: "#5B9BFF", win: "#51CF66", loss: "#FF6B6B", panel: "rgba(255,255,255,0.06)" };
  var CARD_FONT = "Raleway, -apple-system, 'Segoe UI', Roboto, sans-serif";

  function cardCanvas(blocks) {
    var h = CARD.pad * 2 + blocks.reduce(function (s, b) { return s + b.h; }, 0) + 70;
    var cv = document.createElement("canvas");
    cv.width = CARD.W;
    cv.height = h;
    var g = cv.getContext("2d");
    var grad = g.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, CARD.bg1);
    grad.addColorStop(1, CARD.bg2);
    g.fillStyle = grad;
    g.fillRect(0, 0, CARD.W, h);
    var y = CARD.pad;
    blocks.forEach(function (b) { b.draw(g, y); y += b.h; });
    cardText(g, "Клуб настольного тенниса", CARD.pad, h - CARD.pad + 10, 30, 600, CARD.hint);
    return cv;
  }

  function cardText(g, text, x, y, size, weight, color, align) {
    g.font = (weight || 400) + " " + size + "px " + CARD_FONT;
    g.fillStyle = color || CARD.text;
    g.textAlign = align || "left";
    g.textBaseline = "alphabetic";
    g.fillText(fitText(g, String(text), CARD.W - CARD.pad * 2), x, y);
  }

  function fitText(g, text, max) {
    if (g.measureText(text).width <= max) return text;
    while (text.length > 1 && g.measureText(text + "…").width > max) text = text.slice(0, -1);
    return text + "…";
  }

  function cardPanel(g, x, y, w, h) {
    g.fillStyle = CARD.panel;
    if (g.roundRect) { g.beginPath(); g.roundRect(x, y, w, h, 28); g.fill(); }
    else g.fillRect(x, y, w, h);
  }

  function signedColor(text) {
    var tone = deltaTone(text);
    return tone === "tone-win" ? CARD.win : tone === "tone-loss" ? CARD.loss : CARD.hint;
  }

  function titleBlock(title, sub) {
    return { h: 150, draw: function (g, y) {
      cardText(g, title, CARD.pad, y + 64, 64, 800);
      if (sub) cardText(g, sub, CARD.pad, y + 116, 36, 500, CARD.hint);
    } };
  }

  function statsBlock(stats) {
    return { h: 230, draw: function (g, y) {
      var gap = 24, w = (CARD.W - CARD.pad * 2 - gap * (stats.length - 1)) / stats.length;
      stats.forEach(function (s, i) {
        var x = CARD.pad + i * (w + gap);
        cardPanel(g, x, y, w, 196);
        cardText(g, s[0], x + 32, y + 60, 30, 600, CARD.hint);
        cardText(g, s[1], x + 32, y + 150, s[1].length > 6 ? 56 : 72, 800, s[2] || CARD.text);
      });
    } };
  }

  function lineBlock(text, color, size) {
    return { h: (size || 34) + 30, draw: function (g, y) { cardText(g, text, CARD.pad, y + (size || 34), size || 34, 500, color || CARD.hint); } };
  }

  function personBlock(label, name, sub, value, color) {
    return { h: 210, draw: function (g, y) {
      cardPanel(g, CARD.pad, y, CARD.W - CARD.pad * 2, 180);
      cardText(g, label, CARD.pad + 36, y + 54, 28, 700, CARD.hint);
      cardText(g, name, CARD.pad + 36, y + 112, 52, 800);
      if (sub) cardText(g, sub, CARD.pad + 36, y + 156, 30, 500, CARD.hint);
      if (value) cardText(g, value, CARD.W - CARD.pad - 36, y + 120, 64, 800, color || signedColor(value), "right");
    } };
  }

  function rowsBlock(title, rows) {
    return { h: 70 + rows.length * 74, draw: function (g, y) {
      cardText(g, title, CARD.pad, y + 40, 30, 700, CARD.hint);
      rows.forEach(function (r, i) {
        var ry = y + 112 + i * 74;
        cardText(g, r[0], CARD.pad, ry, 40, 600);
        if (r[1]) cardText(g, r[1], CARD.W - CARD.pad, ry, 40, 800, r[2] || CARD.text, "right");
      });
    } };
  }

  function formBlock(title, marks) {
    return { h: 150, draw: function (g, y) {
      cardText(g, title, CARD.pad, y + 40, 30, 700, CARD.hint);
      var size = 64, gap = 14;
      marks.forEach(function (m, i) {
        g.fillStyle = m === "w" ? CARD.win : m === "l" ? CARD.loss : CARD.hint;
        var x = CARD.pad + i * (size + gap);
        if (g.roundRect) { g.beginPath(); g.roundRect(x, y + 66, size, size, 14); g.fill(); }
        else g.fillRect(x, y + 66, size, size);
      });
    } };
  }

  function dayCardBlocks(d) {
    var blocks = [titleBlock("Итоги дня", d.label),
      statsBlock([["Матчей", String(d.board.matches)], ["Партий", String(d.board.sets)], ["Очков", String(d.board.points)]])];
    if (d.board.usual) blocks.push(lineBlock(d.board.usual));
    if (d.hero) blocks.push(personBlock("Игрок дня", d.hero.name, d.hero.matches + " · " + d.hero.record, d.hero.delta));
    if (d.slump) blocks.push(personBlock("Кто сдал", d.slump.name, d.slump.matches + " · " + d.slump.record, d.slump.delta));
    if (d.match_of_day) {
      var m = d.match_of_day;
      blocks.push(personBlock("Матч дня", m.a.name + " — " + m.b.name, m.sets.join("  ·  "), "", ""));
    }
    if (d.players.length) {
      blocks.push(rowsBlock("Кто как сыграл", d.players.slice(0, 7).map(function (p) {
        return [p.name + "  " + p.record, p.delta, signedColor(p.delta)];
      })));
    }
    return blocks;
  }

  function profileCardBlocks(d) {
    var h = d.head;
    var blocks = [titleBlock(h.name, h.subtitle),
      statsBlock([["Рейтинг", h.rating, CARD.accent]].concat(h.week ? [["За неделю", h.week.split(" ")[0], signedColor(h.week)]] : []))];
    var tiles = (d.tiles || []).slice(0, 6).map(function (t) { return [t.label, t.value]; });
    if (tiles.length) blocks.push(rowsBlock("Коротко", tiles));
    if (d.last10 && d.last10.length) blocks.push(formBlock("Последние матчи", d.last10));
    return blocks;
  }

  function shareButton(kind, buildBlocks) {
    var b = el("button", "button button-quiet share-btn");
    b.type = "button";
    b.appendChild(icon("share"));
    b.appendChild(el("span", "", "Поделиться картинкой"));
    b.addEventListener("click", function () {
      haptic("impact");
      b.disabled = true;
      var image;
      try { image = cardCanvas(buildBlocks()).toDataURL("image/png"); }
      catch (e) { b.disabled = false; sheetOpen(null, "Не получилось", "Не удалось нарисовать картинку.", ""); return; }
      post("share", { kind: kind, image: image }).then(function () {
        sheetOpen(null, "Картинка в чате с ботом", "Открой чат с ботом и перешли её, куда хочешь.", "");
      }, function (err) {
        sheetOpen(null, "Не получилось", err.message, "");
      }).then(function () { b.disabled = false; });
    });
    return b;
  }

