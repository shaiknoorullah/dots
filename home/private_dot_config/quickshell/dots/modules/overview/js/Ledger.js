// SPDX-License-Identifier: MIT
// Copyright (c) 2026 luna (@lunanoir21)
//
// Day-bucket arithmetic for the Week page and the heatmap grid. Pure functions,
// no QML imports. The heatmap SCORE is never computed here: it comes from
// dots-overview in heatmap.json. This module only sums, pads and lays out.
// Fresh implementation in the shape of tally-screentime (MIT); see Fmt.js.
.pragma library

// Day keys are "YYYY-MM-DD" built from local calendar parts. They are never
// derived by adding 86400 s to a timestamp, because a DST day is 23 or 25 hours.
function dayKey(date) {
    var y = date.getFullYear(), m = date.getMonth() + 1, d = date.getDate()
    return y + "-" + (m < 10 ? "0" : "") + m + "-" + (d < 10 ? "0" : "") + d
}

function parseKey(key) {
    var p = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key))
    if (!p) return null
    return { y: +p[1], m: +p[2], d: +p[3] }
}

// Calendar-day shift in UTC, so no zone or DST rule can move the result.
function addDays(key, n) {
    var p = parseKey(key)
    if (!p) return key
    var t = new Date(Date.UTC(p.y, p.m - 1, p.d + n))
    var m = t.getUTCMonth() + 1, d = t.getUTCDate()
    return t.getUTCFullYear() + "-" + (m < 10 ? "0" : "") + m + "-" + (d < 10 ? "0" : "") + d
}

// 0 = Monday ... 6 = Sunday
function weekdayOf(key) {
    var p = parseKey(key)
    if (!p) return 0
    return (new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay() + 6) % 7
}

// Sum workedSec / done over days whose key lies in [fromKey, toKey]. Lexical
// comparison is correct for zero-padded ISO keys.
function sumRange(days, fromKey, toKey) {
    var out = { workedSec: 0, done: 0, count: 0 }
    var list = days || []
    for (var i = 0; i < list.length; i++) {
        var e = list[i]
        if (!e || !e.d || e.d < fromKey || e.d > toKey) continue
        out.workedSec += Math.max(0, +e.workedSec || 0)
        out.done += Math.max(0, +e.done || 0)
        out.count++
    }
    return out
}

// Exactly n entries ending at endKey, oldest first. Missing days are zero.
function lastN(days, n, endKey) {
    var byKey = {}
    var list = days || []
    for (var i = 0; i < list.length; i++) if (list[i] && list[i].d) byKey[list[i].d] = list[i]
    var out = []
    for (var k = n - 1; k >= 0; k--) {
        var key = addDays(endKey, -k)
        var e = byKey[key]
        out.push({ d: key, workedSec: e ? Math.max(0, +e.workedSec || 0) : 0,
                   done: e ? Math.max(0, +e.done || 0) : 0, score: e ? (+e.score || 0) : 0 })
    }
    return out
}

// Percent shares that always sum to 100 (largest remainder). The plain
// Math.round of each share can sum to 99 or 101, which looks like a bug on a
// bar chart. All-zero or empty input yields all zeros.
function shares(values) {
    var vals = (values || []).map(function (v) { return Math.max(0, +v || 0) })
    var total = vals.reduce(function (a, b) { return a + b }, 0)
    if (total <= 0) return vals.map(function () { return 0 })
    var raw = vals.map(function (v) { return 100 * v / total })
    var out = raw.map(Math.floor)
    var left = 100 - out.reduce(function (a, b) { return a + b }, 0)
    var order = raw.map(function (r, i) { return { i: i, f: r - Math.floor(r) } })
    order.sort(function (a, b) { return b.f - a.f || a.i - b.i })
    for (var j = 0; j < left; j++) out[order[j].i]++
    return out
}

// Lays days out for a cols x rows grid, oldest first, filled column by column
// (cell i -> col floor(i/rows), row i % rows). Takes the LAST cols*rows days
// and left-pads with empty cells so the grid is always full: 17 x 7 = 119.
function heatCells(days, cols, rows) {
    var total = cols * rows
    var list = (days || []).slice(-total)
    var out = []
    var pad = total - list.length
    for (var i = 0; i < total; i++) {
        var e = i < pad ? null : list[i - pad]
        out.push({
            i: i, col: Math.floor(i / rows), row: i % rows,
            d: e ? e.d : "", workedSec: e ? Math.max(0, +e.workedSec || 0) : 0,
            done: e ? (+e.done || 0) : 0,
            score: e ? Math.max(0, Math.min(4, Math.floor(+e.score || 0))) : 0,
            empty: !e
        })
    }
    return out
}

function maxSec(rows) {
    var m = 0
    for (var i = 0; i < (rows || []).length; i++) m = Math.max(m, +rows[i].sec || +rows[i].workedSec || 0)
    return m
}
