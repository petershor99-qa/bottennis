  // ── Гонка рейтинга (v2.184.0): рейтинг клуба за 30 дней — полосы игроков
  // меняются местами день за днём. Пуск/пауза и ползунок по дням.

  // Чистая часть (проверяется в Node): места игроков в кадре — индексы по убыванию
  function raceOrder(frame) {
    return frame.map(function (v, i) { return i; }).sort(function (a, b) { return frame[b] - frame[a] || a - b; });
  }

  function renderRace(c, d) {
    c.appendChild(header(d.title, d.period));
    if (d.empty) { emptyState(c, d.empty); return; }
    var ROW = 46;
    var card = el("div", "card race");
    var dayLabel = el("div", "race-day scoreboard", d.days[0]);
    card.appendChild(dayLabel);
    var track = el("div", "race-track");
    track.style.height = (d.players.length * ROW) + "px";
    var lo = Infinity, hi = -Infinity;
    d.frames.forEach(function (f) { f.forEach(function (v) { lo = Math.min(lo, v); hi = Math.max(hi, v); }); });
    lo -= (hi - lo) * 0.15 + 5;
    var bars = d.players.map(function (p) {
      var row = clickable(el("div", "race-row"), "player/" + p.id);
      var bar = el("div", "race-bar");
      bar.style.setProperty("--hue", String((p.id * 47) % 360));
      row.appendChild(avatar(p, "sm"));
      row.appendChild(el("span", "race-name", p.name));
      row.appendChild(bar);
      var val = el("span", "race-value scoreboard", "");
      row.appendChild(val);
      track.appendChild(row);
      return { row: row, bar: bar, val: val, id: p.id };
    });
    card.appendChild(track);

    var controls = el("div", "race-controls");
    var play = el("button", "button button-quiet race-play", "Пуск");
    play.type = "button";
    var slider = el("input", "race-slider");
    slider.type = "range";
    slider.min = "0";
    slider.max = String(d.days.length - 1);
    slider.value = "0";
    slider.setAttribute("aria-label", "День");
    controls.appendChild(play);
    controls.appendChild(slider);
    card.appendChild(controls);
    c.appendChild(card);
    c.appendChild(el("p", "footnote", "Полоса — рейтинг на конец дня. Нажмите на игрока, чтобы открыть профиль."));

    function show(i) {
      var frame = d.frames[i];
      var order = raceOrder(frame);
      dayLabel.textContent = d.days[i];
      order.forEach(function (col, place) {
        var b = bars[col];
        b.row.style.transform = "translateY(" + (place * ROW) + "px)";
        b.bar.style.width = Math.max(4, (frame[col] - lo) / (hi - lo) * 100) + "%";
        b.val.textContent = frame[col].toFixed(1);
        b.row.classList.toggle("is-viewer", String(b.id) === String(viewerId));
      });
      slider.value = String(i);
    }

    var timer = null, at = 0;
    function stop() { if (timer) { clearInterval(timer); timer = null; } play.textContent = at >= d.days.length - 1 ? "Сначала" : "Пуск"; }
    play.addEventListener("click", function () {
      haptic("select");
      if (timer) { stop(); return; }
      if (at >= d.days.length - 1) { at = 0; show(0); }
      play.textContent = "Пауза";
      timer = setInterval(function () {
        if (!card.isConnected) { stop(); return; }
        at += 1;
        show(at);
        if (at >= d.days.length - 1) stop();
      }, reduceMotion ? 900 : 550);
    });
    slider.addEventListener("input", function () { stop(); at = parseInt(slider.value, 10) || 0; show(at); });
    show(0);
    // сразу видно, чем закончится: если движение не хочется — показываем сегодня
    if (reduceMotion) { at = d.days.length - 1; show(at); }
  }

