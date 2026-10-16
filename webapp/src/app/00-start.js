// Mini App клуба. v2.160.0 — «Рейтинг клуба»; v2.161.0 — экраны «только
// чтение»; v2.162.0 — свой интерфейс приложения: вкладки «Клуб / Рейтинг / Я»,
// главная с подиумом и матчем дня, профиль-табло с разделами на одном экране,
// ачивки сеткой с уровнями, «ты против него», фото игроков, анимации и вибрация.
// Тексты приходят с сервера (bot/webapp/api.py). Данные вставляются через
// textContent — имена не интерпретируются как HTML.
(function () {
  "use strict";

  // В Node (тесты tests/js, v2.174.0) — только чистые функции, без DOM и Telegram
  if (typeof window === "undefined") {
    module.exports = {
      matchesWord: matchesWord, screenName: screenName, splitNumber: splitNumber, badgeClass: badgeClass,
      parseLaunchParams: parseLaunchParams, isColorDark: isColorDark,
      filterMatches: filterMatches, recordLine: recordLine, compactDate: compactDate,
      toggledReactions: toggledReactions, matchMood: matchMood, tapGuard: tapGuard, chipSets: chipSets, isPhoneClient: isPhoneClient, replayTiming: replayTiming, ballHeight: ballHeight,
      nextNav: nextNav, sparkPoints: sparkPoints, odometerDigits: odometerDigits, raceOrder: raceOrder,
      edgeProgress: edgeProgress, isRootRoute: isRootRoute, playerFromRoute: playerFromRoute,
      reignClock: reignClock, timelineShares: timelineShares, goalsFresh: goalsFresh, desktopFullscreenDefault: desktopFullscreenDefault
    };
    return;
  }

  // Скрипт Telegram может прийти позже страницы или не прийти вовсе (медленный
  // или закрытый telegram.org) — с v2.175.0 приложение не ждёт его: подпись для
  // входа и тему берёт из адреса запуска (#tgWebAppData=…), а кнопку «Назад»,
  // вибрацию и тему Telegram подключает, когда скрипт придёт (attachTelegram).
  var tg = window.Telegram && window.Telegram.WebApp;
  var launch = parseLaunchParams(location.hash);
  if (launch.tgWebAppData) {
    // Скрипт Telegram, пришедший после смены адреса, возьмёт параметры отсюда
    try { window.sessionStorage.setItem("__telegram__initParams", JSON.stringify(launch)); } catch (e) { /* нет памяти */ }
  } else {
    try { launch = JSON.parse(window.sessionStorage.getItem("__telegram__initParams")) || {}; } catch (e) { launch = {}; }
  }
  var telegramAt = null;  // через сколько мс после старта пришёл скрипт Telegram
  var root = document.documentElement;
  var app = document.getElementById("app");
  var tabbar = document.getElementById("tabbar");
  var DARK_BG = "#0A0F1F";
  var SVG = "http://www.w3.org/2000/svg";
  var renderToken = 0;
  var viewerId = null;

