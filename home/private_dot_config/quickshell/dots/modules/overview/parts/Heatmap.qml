pragma ComponentBehavior: Bound
import QtQuick
import qs.modules.overview
import "../js/Fmt.js" as Fmt
import "../js/Ledger.js" as Ledger

// 17 columns x 7 rows of 11x11 cells, gap 3, radius 2 = exactly 235 x 95.
// Cell colour is accent at a fixed alpha per T1's score (0..4); the score is never
// computed here. Score 0 is alpha 0 by spec, i.e. an empty slot.
Item {
    id: h
    property var days: []
    property bool dim: false
    property int hovered: -1
    readonly property var cells: Ledger.heatCells(days, Look.heatCols, Look.heatRows)
    readonly property var hoverCell: hovered >= 0 ? cells[hovered] : null
    readonly property string hoverText: (hoverCell && !hoverCell.empty)
        ? Fmt.dayLabel(hoverCell.d) + "  " + hoverCell.d + "  " + Fmt.hm(hoverCell.workedSec) : ""
    objectName: "heatmap"

    implicitWidth: Look.heatCols * Look.cell + (Look.heatCols - 1) * Look.cellGap
    implicitHeight: Look.heatRows * Look.cell + (Look.heatRows - 1) * Look.cellGap
    width: implicitWidth
    height: implicitHeight

    Repeater {
        model: h.cells
        delegate: Rectangle {
            id: cell
            required property var modelData
            objectName: "cell"
            x: cell.modelData.col * (Look.cell + Look.cellGap)
            y: cell.modelData.row * (Look.cell + Look.cellGap)
            width: Look.cell
            height: Look.cell
            radius: 2
            color: Look.withAlpha(h.dim ? Look.fgFaint : Look.accent, Look.heatRamp[cell.modelData.score])
            border.width: h.hovered === cell.modelData.i ? 1 : 0
            border.color: Look.fgDim
        }
    }

    // One hover area for the whole grid: maps the pointer to a cell, so the
    // tooltip costs one item instead of 119.
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: function (e) {
            var step = Look.cell + Look.cellGap
            var c = Math.floor(e.x / step), r = Math.floor(e.y / step)
            var inCell = (e.x - c * step) < Look.cell && (e.y - r * step) < Look.cell
            h.hovered = (inCell && c >= 0 && c < Look.heatCols && r >= 0 && r < Look.heatRows)
                ? c * Look.heatRows + r : -1
        }
        onExited: h.hovered = -1
    }
}
