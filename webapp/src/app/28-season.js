  // ── Сезонное оформление (v2.214.0) ─────────────────────────────────────────
  // Снег на главной с 1 декабря по 10 января (дата устройства). Лёгкий слой из
  // «снежинок» на transform и opacity, без картинок; «уменьшить движение» — без снега.
  function snowSeason(date) {
    var m = date.getMonth(), d = date.getDate();
    return m === 11 || (m === 0 && d <= 10);
  }

  function syncSnow(onHome) {
    var layer = document.getElementById("snow");
    var want = onHome && !reduceMotion && snowSeason(new Date());
    if (!want) { if (layer) layer.remove(); return; }
    if (layer) return;
    layer = el("div", "snow");
    layer.id = "snow";
    layer.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 26; i++) {
      var flake = el("i");
      flake.style.left = (i * 37 % 100) + "%";
      flake.style.setProperty("--s", (3 + i % 4) + "px");
      flake.style.setProperty("--d", (9 + i * 7 % 8) + "s");
      flake.style.setProperty("--dl", "-" + (i * 13 % 12) + "s");
      flake.style.setProperty("--x", ((i % 2 ? 1 : -1) * (12 + i % 5 * 6)) + "px");
      layer.appendChild(flake);
    }
    document.body.appendChild(layer);
  }

  // «Игрок недели» (v2.214.0): значок на аватаре; id приходит с /api/me
  var weekStar = null;

  function starBadge(person) {
    var b = el("button", "avatar-star");
    b.type = "button";
    b.setAttribute("aria-label", "Игрок недели");
    b.appendChild(useIcon("medal"));
    b.addEventListener("click", function (e) {
      e.stopPropagation();
      sheetOpen(null, "Игрок недели", "Прошлая неделя: " + weekStar.delta, "");
    });
    return b;
  }
