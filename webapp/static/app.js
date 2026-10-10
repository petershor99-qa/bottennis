// Mini App клуба — страница «Рейтинг клуба» (v2.160.0). Только чтение.
// Все тексты приходят с сервера (bot/webapp/api.py), здесь только разметка.
// Данные вставляются через textContent — имена игроков не интерпретируются как HTML.
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  var root = document.documentElement;
  var DARK_BG = "#0A0F1F";

  function applyTheme() {
    var dark = tg && tg.colorScheme === "dark";
    root.setAttribute("data-theme", dark ? "dark" : "light");
    if (!tg) return;
    try {
      var bg = dark ? DARK_BG : (tg.themeParams.secondary_bg_color || "#EFEFF4");
      tg.setHeaderColor(bg);
      tg.setBackgroundColor(bg);
    } catch (e) { /* старые клиенты Telegram без этих методов */ }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showState(text) {
    document.getElementById("state").textContent = text;
    document.getElementById("board").hidden = true;
    document.getElementById("gap").hidden = true;
  }

  function renderRow(row, dark) {
    var li = el("li", "row" + (row.is_viewer ? " is-viewer" : ""));
    li.appendChild(el("div", "lead", dark ? String(row.rank) : row.initial));

    var body = el("div", "body");
    body.appendChild(el("div", "name", row.is_viewer ? row.name + ", ты" : row.name));
    body.appendChild(el("div", "sub", row.subtitle));
    li.appendChild(body);

    var value = el("div", "value");
    value.appendChild(el("div", "rating", row.rating));
    var place = el("div", "place");
    if (dark) {
      if (row.week_label) place.appendChild(el("span", row.week_change > 0 ? "up" : "", row.week_label));
    } else {
      place.appendChild(document.createTextNode("#" + row.rank));
      if (row.week_change !== 0) {
        place.appendChild(document.createTextNode(" · "));
        var sign = row.week_change > 0 ? "+" : "−";
        place.appendChild(el("span", row.week_change > 0 ? "up" : "", sign + Math.abs(row.week_change)));
      }
    }
    value.appendChild(place);
    li.appendChild(value);
    return li;
  }

  function render(data) {
    var dark = root.getAttribute("data-theme") === "dark";
    if (data.empty) { showState(data.empty); return; }
    document.getElementById("state").textContent = "";
    document.getElementById("players-label").textContent = dark ? data.players_label : "";

    var board = document.getElementById("board");
    board.textContent = "";
    data.rows.forEach(function (row) { board.appendChild(renderRow(row, dark)); });
    board.hidden = false;

    var gap = document.getElementById("gap");
    gap.textContent = data.gap || "";
    gap.hidden = !data.gap;
  }

  var lastData = null;

  function load() {
    var initData = tg && tg.initData;
    if (!initData) { showState("Откройте приложение из бота."); return; }
    fetch("api/leaderboard", { headers: { "Authorization": "tma " + initData } })
      .then(function (resp) {
        return resp.json().then(function (body) { return { ok: resp.ok, body: body }; });
      })
      .then(function (res) {
        if (!res.ok) { showState(res.body.error || "Не удалось загрузить данные."); return; }
        lastData = res.body;
        render(lastData);
      })
      .catch(function () { showState("Нет связи с сервером. Попробуйте ещё раз."); });
  }

  applyTheme();
  if (tg) {
    tg.ready();
    tg.expand();
    tg.onEvent("themeChanged", function () {
      applyTheme();
      if (lastData) render(lastData);
    });
  }
  load();
})();
