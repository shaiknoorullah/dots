import QtQuick
import qs.modules.overview

// Quiet text button. Hover lifts (neutral overlay), it does not recolour.
// primary fills with accent; everything else is an outline chip.
Item {
    id: b
    property string text: ""
    property string icon: ""
    property bool primary: false
    property bool dim: false
    signal clicked()

    implicitHeight: 28
    implicitWidth: row.implicitWidth + Look.sXl * 2
    opacity: enabled ? 1 : 0.4
    activeFocusOnTab: true

    Rectangle {
        anchors.fill: parent
        radius: Look.rChip
        color: b.primary ? (b.dim ? Look.fgFaint : Look.accent) : "transparent"
        border.width: b.primary ? 0 : 1
        border.color: Look.withAlpha(Look.outline, Look.aDivider * 2)
    }
    Rectangle {
        anchors.fill: parent
        radius: Look.rChip
        color: Look.withAlpha(Look.fg, Look.aHover)
        visible: ma.containsMouse && b.enabled
    }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: Look.sM
        Glyph {
            visible: b.icon !== ""
            name: b.icon
            size: 16
            anchors.verticalCenter: parent.verticalCenter
            color: b.primary ? Look.accentFg : Look.fgDim
        }
        Txt {
            text: b.text
            role: "bodyS"
            anchors.verticalCenter: parent.verticalCenter
            color: b.primary ? Look.accentFg : (b.dim ? Look.fgFaint : Look.fg)
            elide: Text.ElideNone
        }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: if (b.enabled) b.clicked()
    }
    Keys.onReturnPressed: if (b.enabled) b.clicked()
    Keys.onSpacePressed: if (b.enabled) b.clicked()
}
