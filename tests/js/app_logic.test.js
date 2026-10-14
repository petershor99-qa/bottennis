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
  assert.equal(app.matchMood(m([[5, 11], [11, 9], [11, 8]], "a")), "comeback");
  assert.equal(app.matchMood(m([[11, 3], [11, 4]], "a")), "blowout");
  assert.equal(app.matchMood(m([[12, 10], [13, 11]], "a")), "deuce");
  assert.equal(app.matchMood(m([[11, 9], [11, 9]], "a")), "");
  assert.equal(app.matchMood(m([[11, 9]], "b", true)), "boss");
  assert.equal(app.matchMood(m([[11, 9], [9, 11]], "")), "");   // ничья — без эффектов
});
