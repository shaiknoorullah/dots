pragma ComponentBehavior: Bound
import QtQuick
import qs.modules.overview
import qs.modules.overview.parts
import qs.modules.overview.parts as Parts
import "../js/Fmt.js" as Fmt

// 119 days of work, five-step ramp from T1's score. Hover a cell for date and time.
FocusScope {
    id: pg
    readonly property bool dim: Feed.dim
    readonly property var days: Feed.heatDoc ? Feed.heatDoc.days : []
    readonly property real total: days.reduce(function (a, d) { return a + (+d.workedSec || 0) }, 0)
    readonly property int active: days.filter(function (d) { return (+d.workedSec || 0) > 0 }).length
    readonly property real best: days.reduce(function (a, d) { return Math.max(a, +d.workedSec || 0) }, 0)

    Column {
        anchors.left: parent.left
        anchors.top: parent.top
        spacing: Look.sXl

        Row {
            spacing: Look.sL
            Txt { text: "Last 119 days"; role: "title"; dim: pg.dim }
            StaleMark { visible: pg.dim; anchors.verticalCenter: parent.verticalCenter }
        }
        Parts.Heatmap {
            id: hm
            days: pg.days
            dim: pg.dim
        }
        Txt {
            height: 18
            text: hm.hoverText
            role: "monoS"
            faint: true
            dim: pg.dim
            elide: Text.ElideNone
        }
        Row {
            spacing: Look.sS
            Txt { text: "less"; role: "caption"; faint: true; dim: pg.dim; anchors.verticalCenter: parent.verticalCenter }
            Repeater {
                model: Look.heatRamp
                delegate: Rectangle {
                    required property real modelData
                    width: Look.cell; height: Look.cell; radius: 2
                    color: Look.withAlpha(pg.dim ? Look.fgFaint : Look.accent, modelData)
                    border.width: modelData === 0 ? 1 : 0
                    border.color: Look.withAlpha(Look.outline, Look.aDivider)
                }
            }
            Txt { text: "more"; role: "caption"; faint: true; dim: pg.dim; anchors.verticalCenter: parent.verticalCenter }
        }
        Column {
            width: hm.width
            spacing: Look.sS
            StatRow { width: parent.width; label: "Total"; value: Fmt.hm(pg.total); dim: pg.dim }
            StatRow { width: parent.width; label: "Days worked"; value: String(pg.active); dim: pg.dim }
            StatRow { width: parent.width; label: "Best day"; value: Fmt.hm(pg.best); dim: pg.dim }
        }
        Empty { visible: pg.days.length === 0 }
    }
}
