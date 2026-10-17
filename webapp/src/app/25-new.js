  // ── Новое (v2.183.0): «Кто с кем», цифры-табло рейтинга, полноэкранный
  // «Твой месяц», мячик «потяни, чтобы обновить».

  // «Кто с кем»: матрица пар клуба — сколько матчей и кто кого чаще обыгрывал
  function renderPairs(c, d) {
    c.appendChild(header(d.title, d.period));
    if (d.empty) { emptyState(c, d.empty); return; }
    var card = el("div", "card pairs");
    var grid = el("div", "pairs-grid");
    grid.style.setProperty("--cols", String(d.players.length));
    grid.appendChild(el("span", "pairs-corner"));
    d.players.forEach(function (p) { grid.appendChild(avatar(p, "sm")); });
    d.matrix.forEach(function (cells, ri) {
      var rowP = d.players[ri];
      grid.appendChild(avatar(rowP, "sm"));
      cells.forEach(function (cell, ci) {
        if (!cell) { grid.appendChild(el("span", "pairs-self")); return; }
        var colP = d.players[ci];
        var b = el("button", "pairs-cell heat-" + cell.lvl, cell.n ? String(cell.n) : "");
        b.type = "button";
        b.style.animationDelay = (ri * 40 + ci * 15) + "ms";
        b.setAttribute("aria-label", rowP.name + " — " + colP.name + ": " + cell.n);
        b.addEventListener("click", function () {
          var other = d.matrix[ci][ri] ? d.matrix[ci][ri].wins : 0;
          var text = cell.n ? matchesCount(cell.n) + " · счёт " + cell.wins + "–" + other : "Ещё не играли";
          sheetOpen(null, rowP.name + " — " + colP.name, text, "");
          var wrap = document.querySelector(".sheet");
          if (wrap) {
            var mine = String(rowP.id) === String(viewerId);
            var go2 = el("button", "button button-quiet", (mine ? "Ты" : rowP.name) + " против " + colP.name);
            go2.type = "button";
            go2.addEventListener("click", function () {
              document.querySelector(".sheet-wrap").remove();
              go(mine ? "h2h/" + colP.id : "pair/" + rowP.id + "/" + colP.id);   // v2.193.0: любая пара
            });
            wrap.appendChild(go2);
          }
        });
        grid.appendChild(b);
      });
    });
    card.appendChild(grid);
    c.appendChild(card);
    c.appendChild(el("p", "footnote", "Число в клетке — матчи пары. Нажмите на клетку, чтобы увидеть счёт."));
    var facts = section("Главное");
    d.facts.forEach(function (it) { facts.list.appendChild(itemRow(it)); });
    c.appendChild(facts);
  }

  function matchesCount(n) {
    var m10 = n % 10, m100 = n % 100;
    return n + " " + (m100 >= 11 && m100 <= 14 ? "матчей" : m10 === 1 ? "матч" : m10 >= 2 && m10 <= 4 ? "матча" : "матчей");
  }

  // Цифры-табло: если рейтинг изменился с прошлого просмотра этой карточки,
  // цифры «перелистываются» со старого значения на новое, как на механическом
  // табло. Иначе — обычный досчёт. Последнее увиденное — на устройстве.
  var SEEN_RATING_KEY = "seen_rating_v1";

  function odometerDigits(from, to) {
    // Чистая часть (проверяется в Node): пары [было, стало] по знакам; null — не табло
    if (from === to || from.length !== to.length) return null;
    var out = [];
    for (var i = 0; i < to.length; i++) {
      var a = from.charAt(i), b = to.charAt(i);
      var isDigit = /\d/.test(a) && /\d/.test(b);
      if (!isDigit && a !== b) return null;
      out.push(isDigit ? [parseInt(a, 10), parseInt(b, 10)] : b);
    }
    return out;
  }

  function ratingNumber(cls, text, playerId) {
    var seen = {};
    try { seen = JSON.parse(window.localStorage.getItem(SEEN_RATING_KEY) || "{}") || {}; } catch (e) { seen = {}; }
    var prev = seen[playerId];
    seen[playerId] = text;
    try { window.localStorage.setItem(SEEN_RATING_KEY, JSON.stringify(seen)); } catch (e) { /* без памяти */ }
    var digits = prev && !reduceMotion && !app.classList.contains("quiet") ? odometerDigits(prev, text) : null;
    if (!digits) return num(cls, text);
    var wrap = el("div", cls + " odometer");
    wrap.setAttribute("aria-label", text);
    // по окончании прокрутки — обычный текст: у цифр разная ширина, а колонки
    // табло одинаковые, поэтому в покое число выглядит как везде
    setTimeout(function () { wrap.classList.remove("odometer"); wrap.textContent = text; },
      250 + 600 + text.length * 80 + 150);
    digits.forEach(function (d, i) {
      if (typeof d === "string") { wrap.appendChild(el("span", "odo-sep", d)); return; }
      var col = el("span", "odo-col");
      var strip = el("span", "odo-strip");
      for (var k = 0; k <= 9; k++) strip.appendChild(el("span", "odo-digit", String(k)));
      strip.style.transform = "translateY(" + (-d[0] * 10) + "%)";
      col.appendChild(strip);
      wrap.appendChild(col);
      if (d[0] !== d[1]) {
        setTimeout(function () {
          strip.style.transition = "transform " + (0.6 + i * 0.08) + "s cubic-bezier(.2, .8, .2, 1)";
          strip.style.transform = "translateY(" + (-d[1] * 10) + "%)";
        }, 250);
      }
    });
    return wrap;
  }

  // «Твой месяц» во весь экран (Telegram 8.0+): как сторис; при уходе с экрана — обратно
  function fullscreenAvailable() {
    try { return !!(tg && tg.requestFullscreen && tg.isVersionAtLeast && tg.isVersionAtLeast("8.0")); }
    catch (e) { return false; }
  }

  function enterFullscreen() {
    if (!fullscreenAvailable() || tg.isFullscreen) return;
    try { tg.requestFullscreen(); } catch (e) { /* клиент не разрешил */ }
  }

  function leaveFullscreen() {
    try { if (tg && tg.isFullscreen && tg.exitFullscreen) tg.exitFullscreen(); } catch (e) { /* уже вышли */ }
  }

  function syncFullscreenClass() {
    root.classList.toggle("is-fullscreen", !!(tg && tg.isFullscreen));
    if (fsButton) {
      var on = !!(tg && tg.isFullscreen);
      fsButton.textContent = "";
      fsButton.appendChild(icon(on ? "minimize" : "maximize", "ico-btn"));
      fsButton.setAttribute("aria-label", on ? "Обычный размер" : "Во весь экран");
      fsButton.title = on ? "Обычный размер" : "Во весь экран";
    }
  }

  // Во весь экран всегда (v2.186.0): на телефоне — сразу при запуске (Telegram
  // 8.0+; кнопки Telegram «закрыть» и «⋮» остаются поверх, под них — отступ
  // safe area). На компьютере окно приложения Telegram не даёт «чуть увеличить»,
  // только на весь монитор — там кнопка в углу, выбор запоминается на этом
  // устройстве (localStorage, не CloudStorage: на телефоне свой режим).
  // v2.200.0: в Telegram для компьютера (Windows/Linux, macOS) по умолчанию
  // во весь экран — узкое окно было неудобным; кнопкой можно вернуть окно.
  var FS_KEY = "fullscreen_desktop_v1";
  var fsButton = null;

  function isPhoneClient(platform) { return platform === "android" || platform === "ios"; }

  function desktopFullscreenDefault(platform, saved) {
    if (saved === "1" || saved === "0") return saved === "1";
    return platform === "tdesktop" || platform === "macos";
  }

  function desktopWantsFullscreen() {
    var saved = null;
    try { saved = window.localStorage.getItem(FS_KEY); } catch (e) { saved = null; }
    return desktopFullscreenDefault(clientInfo().platform, saved);
  }

  function wantFullscreen() {
    return isPhoneClient(clientInfo().platform) || desktopWantsFullscreen();
  }

  function setupFullscreen() {
    // v2.203.1: приложение могло открыться сразу во весь экран — без события
    // fullscreenChanged отметки не было, и верх экрана уезжал под камеру
    syncFullscreenClass();
    if (!fullscreenAvailable()) return;
    if (wantFullscreen()) enterFullscreen();
    if (isPhoneClient(clientInfo().platform) || fsButton) return;
    fsButton = el("button", "fs-toggle");
    fsButton.type = "button";
    fsButton.addEventListener("click", function () {
      var on = !tg.isFullscreen;
      try { window.localStorage.setItem(FS_KEY, on ? "1" : "0"); } catch (e) { /* без памяти — до закрытия */ }
      if (on) enterFullscreen(); else leaveFullscreen();
    });
    document.body.appendChild(fsButton);
    root.classList.add("has-fs-toggle");
    syncFullscreenClass();
  }

