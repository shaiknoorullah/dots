import QtQuick
import QtQuick.Shapes
import qs.modules.overview

// Polyline of n values scaled to the largest. Used for the 7-day strip.
Item {
    id: s
    property var values: []
    property bool dim: false
    implicitHeight: 40
    implicitWidth: 200

    readonly property real peak: {
        var m = 0
        for (var i = 0; i < values.length; i++) m = Math.max(m, values[i])
        return m
    }
    readonly property var pts: {
        var out = []
        var n = values.length
        for (var i = 0; i < n; i++) {
            var x = n > 1 ? i * (width - 2) / (n - 1) + 1 : width / 2
            var y = peak > 0 ? height - 2 - (height - 4) * values[i] / peak : height - 2
            out.push(Qt.point(x, y))
        }
        return out
    }

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: s.dim ? Look.fgFaint : Look.accent
            strokeWidth: 1.5
            fillColor: "transparent"
            joinStyle: ShapePath.RoundJoin
            capStyle: ShapePath.RoundCap
            PathPolyline { path: s.pts }
        }
    }
}
