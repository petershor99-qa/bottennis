// Логика страницы Mini App без браузера (v2.174.0). Запуск: node --test tests/js/*.test.js
// app.js в Node не трогает DOM, а отдаёт только чистые функции (см. начало файла).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const app = require(path.join(__dirname, "..", "..", "webapp", "static", "app.js"));

test("matchesWord: склонение и пустой период", () => {
  assert.equal(app.matchesWord(0), "Матчей не было");
  assert.equal(app.matchesWord(1), "1 матч за 90 дней");
  assert.equal(app.matchesWord(3), "3 матча за 90 дней");
  assert.equal(app.matchesWord(5), "5 матчей за 90 дней");
  assert.equal(app.matchesWord(11), "11 матчей за 90 дней");
  assert.equal(app.matchesWord(21), "21 матч за 90 дней");
  assert.equal(app.matchesWord(112), "112 матчей за 90 дней");
});

test("screenName: свой профиль, чужой профиль, id отрезаются", () => {
  assert.equal(app.screenName("", 7), "home");
  assert.equal(app.screenName("player/7", 7), "me");
  assert.equal(app.screenName("player/7/achievements", 7), "me_achievements");
  assert.equal(app.screenName("player/3", 7), "player");
  assert.equal(app.screenName("player/3/matches", 7), "player_matches");
  assert.equal(app.screenName("h2h/3", 7), "h2h");
  assert.equal(app.screenName("summary/day", 7), "summary_day");
  // id зрителя ещё не известен — любой профиль считается чужим
  assert.equal(app.screenName("player/7", null), "player");
});

test("splitNumber: число отделяется от подписи", () => {
  assert.deepEqual(app.splitNumber("1080.1"), ["", "", "1080.1", ""]);
  assert.deepEqual(app.splitNumber("60%"), ["", "", "60", "%"]);
  assert.deepEqual(app.splitNumber("+12.4"), ["+", "", "12.4", ""]);
  assert.deepEqual(app.splitNumber("−3.5"), ["", "−", "3.5", ""]);
  assert.deepEqual(app.splitNumber("#2 из 7"), ["#", "", "2", " из 7"]);
  assert.equal(app.splitNumber("нет"), null);
});

test("badgeClass: уровень ачивки и закрытые", () => {
  assert.equal(app.badgeClass({ earned: false, hidden: true }), "badge-hidden");
  assert.equal(app.badgeClass({ earned: false, hidden: false }), "badge-locked");
  assert.equal(app.badgeClass({ earned: true, tier: "gold" }), "badge-gold");
  assert.equal(app.badgeClass({ earned: true }), "badge-earned");
});

test("parseLaunchParams: параметры запуска Telegram из адреса", () => {
  const data = "query_id=AA&user=%7B%22id%22%3A1%7D&hash=abc";
  const p = app.parseLaunchParams("#tgWebAppData=" + encodeURIComponent(data) +
    "&tgWebAppVersion=7.10&tgWebAppPlatform=tdesktop");
  assert.equal(p.tgWebAppData, data);
  assert.equal(p.tgWebAppVersion, "7.10");
  assert.equal(p.tgWebAppPlatform, "tdesktop");
  // маршрут приложения и пустой адрес — не параметры запуска
  assert.deepEqual(app.parseLaunchParams("#/player/3"), {});
  assert.deepEqual(app.parseLaunchParams(""), {});
  // «+» — пробел, как у telegram-web-app.js; битая кодировка не роняет разбор
  assert.equal(app.parseLaunchParams("#a=b+c&bad=%E0%A4%A").a, "b c");
  assert.equal(app.parseLaunchParams("#bad=%E0%A4%A").bad, "%E0%A4%A");
});

test("isColorDark: тема по цвету фона", () => {
  assert.equal(app.isColorDark("#17212b"), true);
  assert.equal(app.isColorDark("#ffffff"), false);
  assert.equal(app.isColorDark("17212B"), true);
  assert.equal(app.isColorDark(undefined), false);
  assert.equal(app.isColorDark("red"), false);
});

test("filterMatches: соперник и период по МСК-дате", () => {
  const ms = [
    { opponent_id: 2, iso: "2026-10-14", result: "w" },
    { opponent_id: 3, iso: "2026-10-07", result: "l" },  // за границей 7 дней
    { opponent_id: 2, iso: "2026-09-01", result: "d" },
  ];
  assert.equal(app.filterMatches(ms, 0, 0, "2026-10-14").length, 3);
  assert.equal(app.filterMatches(ms, 2, 0, "2026-10-14").length, 2);
  assert.equal(app.filterMatches(ms, 0, 7, "2026-10-14").length, 1);   // 8–14 октября включительно
  assert.equal(app.filterMatches(ms, 0, 30, "2026-10-14").length, 2);
  assert.equal(app.filterMatches(ms, 3, 7, "2026-10-14").length, 0);
});

test("recordLine и compactDate", () => {
  assert.equal(app.recordLine([{ result: "w" }, { result: "l" }, { result: "w" }]), "3 матча · 2–1");
  assert.equal(app.recordLine([{ result: "d" }]), "1 матч · 0–0–1");
  assert.equal(app.compactDate("2026-10-13"), "20261013");
});

test("toggledReactions: одна реакция игрока", () => {
  const base = [
    { kind: "fire", count: 2, mine: true }, { kind: "like", count: 0, mine: false },
  ];
  const swapped = app.toggledReactions(base, "like");
  assert.deepEqual(swapped.map((r) => [r.kind, r.count, r.mine]), [["fire", 1, false], ["like", 1, true]]);
  const removed = app.toggledReactions(base, "fire");
  assert.deepEqual(removed.map((r) => [r.kind, r.count, r.mine]), [["fire", 1, false], ["like", 0, false]]);
});

test("matchMood: характер матча по партиям", () => {
  const m = (sets, winner, boss) => ({ sets: sets.map(([a, b]) => ({ a, b })), winner, boss: !!boss });
  assert.equal(app.matchMood(m([[5, 11], [11, 9], [11, 8]], "a")), "rally");          // 0:1 → 2:1 — «Отыгрался» (v2.198.0)
  assert.equal(app.matchMood(m([[5, 11], [7, 11], [11, 9], [11, 8], [11, 6]], "a")), "comeback");   // с 0:2 — «Камбэк»
  assert.equal(app.matchMood(m([[5, 11], [11, 7], [9, 11], [11, 8], [11, 6]], "a")), "rally");     // 0:1, 1:1, 1:2 — не 0:2
  assert.equal(app.matchMood(m([[11, 3], [11, 4]], "a")), "blowout");
  assert.equal(app.matchMood(m([[12, 10], [13, 11]], "a")), "deuce");
  assert.equal(app.matchMood(m([[11, 9], [11, 9]], "a")), "");
  assert.equal(app.matchMood(m([[11, 9]], "b", true)), "boss");
  assert.equal(app.matchMood(m([[11, 9], [9, 11]], "")), "");   // ничья — без эффектов
});

test("nextNav: вглубь, назад, вкладки, повтор (v2.182.0)", () => {
  let [dir, st] = app.nextNav([], "", 7);
  assert.equal(dir, "tab"); assert.deepEqual(st, [""]);
  [dir, st] = app.nextNav(st, "records", 7);
  assert.equal(dir, "forward"); assert.deepEqual(st, ["", "records"]);
  [dir, st] = app.nextNav(st, "player/3", 7);
  assert.equal(dir, "forward");
  [dir, st] = app.nextNav(st, "records", 7);               // «Назад» на шаг
  assert.equal(dir, "back"); assert.deepEqual(st, ["", "records"]);
  [dir, st] = app.nextNav(st, "", 7);                      // назад на вкладку, с которой ушли
  assert.equal(dir, "back"); assert.deepEqual(st, [""]);
  [dir, st] = app.nextNav(st, "rating", 7);                // другая вкладка — проявление
  assert.equal(dir, "tab");
  [dir] = app.nextNav(st, "rating", 7);                     // тот же экран — без анимации
  assert.equal(dir, "none");
  [dir] = app.nextNav(["rating"], "player/7", 7);           // своя вкладка «Я»
  assert.equal(dir, "tab");
});

test("sparkPoints: от старых к новым, ровная линия не делит на ноль", () => {
  assert.deepEqual(app.sparkPoints([1000, 1010, 1005], 46, 16), ["0.0,15.0", "23.0,1.0", "46.0,8.0"]);
  assert.deepEqual(app.sparkPoints([1000, 1000], 10, 10), ["0.0,9.0", "10.0,9.0"]);
});

test("odometerDigits: пары цифр, разделители, разная длина — не табло (v2.183.0)", () => {
  assert.deepEqual(app.odometerDigits("1081.3", "1098.0"), [[1, 1], [0, 0], [8, 9], [1, 8], ".", [3, 0]]);
  assert.equal(app.odometerDigits("999.5", "1000.1"), null);     // стало длиннее — обычный досчёт
  assert.equal(app.odometerDigits("1000.0", "1000.0"), null);    // не менялось
  assert.equal(app.odometerDigits("1000.0", "1000,0"), null);    // другой разделитель
});

test("raceOrder: места по убыванию рейтинга, при равенстве — порядок списка (v2.184.0)", () => {
  assert.deepEqual(app.raceOrder([1000, 1100, 1050]), [1, 2, 0]);
  assert.deepEqual(app.raceOrder([1000, 1000, 1200]), [2, 0, 1]);
});

test("tapGuard: два пальца и двойной тап не дают второго действия (v2.185.0)", () => {
  const a = { id: "a" }, b = { id: "b" };
  let st = { multiTouchAt: 0, lastTap: { node: null, at: 0 } };
  assert.equal(app.tapGuard(10000, a, st), true);
  assert.equal(app.tapGuard(10200, a, st), false);          // тот же элемент через 0,2 с — двойной тап
  assert.equal(app.tapGuard(10300, b, st), true);           // другой элемент — можно
  assert.equal(app.tapGuard(10800, b, st), true);           // тот же, но позже 0,35 с — можно
  st = { multiTouchAt: 20000, lastTap: { node: null, at: 0 } };
  assert.equal(app.tapGuard(20100, a, st), false);          // только что было два пальца
  assert.equal(app.tapGuard(20500, a, st), true);
});

test("chipSets: счёт из ленты для характера матча (v2.185.0)", () => {
  assert.deepEqual(app.chipSets(["5:11", "11:8"]), [{ a: 5, b: 11 }, { a: 11, b: 8 }]);
  assert.deepEqual(app.chipSets(null), []);
  const sets = app.chipSets(["5:11", "6:11", "11:8", "11:8", "11:9"]);
  assert.equal(app.matchMood({ winner: "a", boss: false, sets }), "comeback");
});

test("replayTiming: партии по очереди — около 2 секунд на матч (v2.187.0, было до 4)", () => {
  for (const n of [2, 3, 5, 7]) {
    const t = app.replayTiming(n);
    assert.ok(t.per <= 350 && t.per >= 200);
    assert.ok(t.start + n * (t.per + t.gap) <= 2300, `n=${n}`);
  }
  assert.equal(app.replayTiming(2).per, 350);
  const three = app.replayTiming(3);
  assert.ok(three.start + 3 * (three.per + three.gap) <= 1400);   // обычный матч до двух побед — ~1,3 с
});

test("isPhoneClient: во весь экран сразу только на телефоне (v2.186.0)", () => {
  assert.equal(app.isPhoneClient("android"), true);
  assert.equal(app.isPhoneClient("ios"), true);
  for (const p of ["tdesktop", "macos", "weba", "webk", ""]) assert.equal(app.isPhoneClient(p), false);
});

test("ballHeight: два отскока от стола по ходу натяжения (v2.186.0)", () => {
  assert.equal(app.ballHeight(0), 0);
  assert.equal(app.ballHeight(0.25), 26);     // первая вершина
  assert.equal(app.ballHeight(0.5), 0);       // первый отскок
  assert.equal(app.ballHeight(0.75), 26);     // вторая вершина
  assert.equal(app.ballHeight(1), 0);         // второй отскок
  assert.equal(app.ballHeight(3), 0);         // дальше — лежит на столе
});

test("edgeProgress: натяжение «назад» от края, 0..1", () => {
  assert.equal(app.edgeProgress(-5), 0);
  assert.equal(app.edgeProgress(45), 0.5);
  assert.equal(app.edgeProgress(90), 1);
  assert.equal(app.edgeProgress(300), 1);
});

test("isRootRoute: на главных вкладках жеста «назад» нет", () => {
  assert.equal(app.isRootRoute("", 7), true);
  assert.equal(app.isRootRoute("rating", 7), true);
  assert.equal(app.isRootRoute("player/7", 7), true);
  assert.equal(app.isRootRoute("player/7/matches", 7), false);
  assert.equal(app.isRootRoute("player/3", 7), false);
  assert.equal(app.isRootRoute("h2h/3", 7), false);
});

test("playerFromRoute: долгое нажатие только на игрока", () => {
  assert.equal(app.playerFromRoute("player/12"), "12");
  assert.equal(app.playerFromRoute("player/12/style"), "12");
  assert.equal(app.playerFromRoute("match/12"), null);
  assert.equal(app.playerFromRoute("h2h/12"), null);
  assert.equal(app.playerFromRoute(null), null);
});

test("reignClock: дни и часы правления", () => {
  assert.equal(app.reignClock(0), "00:00:00");
  assert.equal(app.reignClock(-5000), "00:00:00");
  assert.equal(app.reignClock(((3 * 24 + 4) * 3600 + 12 * 60 + 5) * 1000 + 999), "3 д 04:12:05");
  assert.equal(app.reignClock(59 * 1000), "00:00:59");
});

test("timelineShares: доли по длительности, не меньше 3%, в сумме 100", () => {
  const day = 86400000;
  const s = app.timelineShares([
    { start_ts: 0, end_ts: 99 * day },
    { start_ts: 99 * day, end_ts: 99 * day + 1000 },   // правление на секунду — всё равно видно
    { start_ts: 99 * day + 1000, end_ts: null },
  ], 100 * day);
  assert.ok(s[1] >= 2.8);
  assert.ok(s[0] > s[2]);
  assert.ok(Math.abs(s.reduce((a, b) => a + b, 0) - 100) < 0.05);
});
