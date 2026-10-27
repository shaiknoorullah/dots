import QtQuick
import QtTest
import "../js/Ledger.js" as Ledger

TestCase {
    name: "Ledger"

    function days(list) {
        return list.map(function (e) { return { d: e[0], workedSec: e[1], done: e[2] || 0, score: e[3] || 0 } })
    }

    function test_dayKey_uses_calendar_parts() {
        compare(Ledger.dayKey(new Date(2026, 0, 5, 23, 59, 59)), "2026-01-05")
        compare(Ledger.dayKey(new Date(2026, 11, 31, 0, 0, 0)), "2026-12-31")
    }

    function test_addDays_across_dst_and_year() {
        // Europe springs forward 2026-03-29 and falls back 2026-10-25; US 2026-03-08 / 2026-11-01.
        compare(Ledger.addDays("2026-03-28", 1), "2026-03-29")
        compare(Ledger.addDays("2026-03-29", 1), "2026-03-30")
        compare(Ledger.addDays("2026-10-24", 2), "2026-10-26")
        compare(Ledger.addDays("2026-03-07", 2), "2026-03-09")
        compare(Ledger.addDays("2026-12-31", 1), "2027-01-01")
        compare(Ledger.addDays("2028-02-28", 1), "2028-02-29")
        compare(Ledger.addDays("2026-03-01", -1), "2026-02-28")
        compare(Ledger.addDays("nope", 1), "nope")
    }

    function test_weekdayOf() {
        compare(Ledger.weekdayOf("2026-10-26"), 0)   // Monday
        compare(Ledger.weekdayOf("2026-11-01"), 6)   // Sunday
    }

    function test_sumRange_buckets() {
        var d = days([["2026-10-20", 3600, 1], ["2026-10-21", 1800, 2], ["2026-10-22", 600, 0], ["2026-10-30", 99, 9]])
        var r = Ledger.sumRange(d, "2026-10-20", "2026-10-22")
        compare(r.workedSec, 6000)
        compare(r.done, 3)
        compare(r.count, 3)
        compare(Ledger.sumRange(d, "2026-10-21", "2026-10-21").workedSec, 1800)   // single day
        compare(Ledger.sumRange([], "a", "z").workedSec, 0)                        // empty
        compare(Ledger.sumRange(null, "a", "z").count, 0)
        compare(Ledger.sumRange(days([["2026-10-20", -50]]), "2026-10-20", "2026-10-20").workedSec, 0)
    }

    function test_lastN_pads_missing_days() {
        var d = days([["2026-10-21", 100, 1, 2], ["2026-10-23", 300, 0, 4]])
        var w = Ledger.lastN(d, 7, "2026-10-23")
        compare(w.length, 7)
        compare(w[0].d, "2026-10-17")
        compare(w[6].d, "2026-10-23")
        compare(w[4].workedSec, 100)
        compare(w[5].workedSec, 0)
        compare(w[6].score, 4)
        compare(Ledger.lastN([], 1, "2026-10-23")[0].workedSec, 0)   // single day, no data
    }

    function test_shares_sum_to_100() {
        var s = Ledger.shares([1, 1, 1])
        compare(s.reduce(function (a, b) { return a + b }, 0), 100)
        compare(s[0], 34)                      // largest remainder goes to the first of equals
        var t = Ledger.shares([50, 30, 20])
        compare(t[0], 50); compare(t[1], 30); compare(t[2], 20)
        compare(Ledger.shares([0, 0]).join(","), "0,0")
        compare(Ledger.shares([]).length, 0)
        compare(Ledger.shares([5])[0], 100)    // single entry
        var u = Ledger.shares([333, 333, 334])
        compare(u.reduce(function (a, b) { return a + b }, 0), 100)
    }

    function test_heatCells_is_always_119() {
        var all = []
        for (var i = 0; i < 119; i++) all.push({ d: Ledger.addDays("2026-06-29", i), workedSec: i, done: 0, score: i % 5 })
        var c = Ledger.heatCells(all, 17, 7)
        compare(c.length, 119)
        compare(c[0].col, 0); compare(c[0].row, 0)
        compare(c[7].col, 1); compare(c[7].row, 0)
        compare(c[118].col, 16); compare(c[118].row, 6)
        compare(c[118].d, all[118].d)
        compare(c[0].empty, false)

        var few = Ledger.heatCells(all.slice(0, 3), 17, 7)    // short input is left-padded
        compare(few.length, 119)
        compare(few[0].empty, true)
        compare(few[116].empty, false)
        compare(few[118].d, all[2].d)

        compare(Ledger.heatCells([], 17, 7).length, 119)      // empty
        compare(Ledger.heatCells(all.concat(all), 17, 7).length, 119)   // too long: keeps the last 119
    }

    function test_heatCells_clamps_score() {
        var c = Ledger.heatCells([{ d: "2026-10-23", workedSec: 1, score: 9 }, { d: "2026-10-24", workedSec: 1, score: -3 }], 17, 7)
        compare(c[117].score, 4)
        compare(c[118].score, 0)
    }
}
