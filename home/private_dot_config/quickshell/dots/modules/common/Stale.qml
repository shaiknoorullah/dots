import QtQuick
import "../theme"

// A source that is stale or unavailable renders a hollow question mark at fgFaint.
// Never a spinner: waiting is not news.
Item {
  property bool on: false

  implicitWidth: on ? mark.implicitWidth : 0
  implicitHeight: Theme.bar.height
  visible: on

  Text {
    id: mark
    anchors.centerIn: parent
    text: "?"
    color: Theme.color.fgFaint
    font.family: Theme.font.mono
    font.pixelSize: Theme.font.size.body
  }
}
