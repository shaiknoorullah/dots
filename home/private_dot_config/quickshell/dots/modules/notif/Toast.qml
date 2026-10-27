import Quickshell.Services.Notifications
import QtQuick
import "../theme"
import "../common"

// One toast: 380 wide, padding 14, radius 12. It hides itself when its time is up (the
// notification stays in the centre) and re-appears when the app REPLACES it in place:
// the server updates the same Notification object, so summary/body changing is the cue.
Item {
  id: toast

  required property var modelData      // Notification
  property bool dnd: false
  property bool alive: true

  readonly property bool critical: modelData.urgency === NotificationUrgency.Critical
  readonly property int lifetime: modelData.expireTimeout > 0 ? modelData.expireTimeout * 1000 : 6000

  width: 380
  implicitHeight: alive && (!dnd || critical) ? box.height : 0
  height: implicitHeight
  visible: implicitHeight > 0
  opacity: visible ? 1 : 0

  Behavior on opacity { Anim { duration: Theme.motion.fast; easing.type: Theme.motion.easeOut } }

  Connections {
    target: toast.modelData
    function onSummaryChanged() { toast.revive(); }
    function onBodyChanged() { toast.revive(); }
  }

  function revive() {
    toast.alive = true;
    life.restart();
  }

  Timer {
    id: life
    interval: toast.lifetime
    running: toast.alive && !toast.critical && !hover.containsMouse
    onTriggered: toast.alive = false
  }

  Surface {
    id: box
    width: parent.width
    height: col.implicitHeight + 2 * 14

    Column {
      id: col
      x: 14
      y: 14
      width: parent.width - 28
      spacing: Theme.space.s

      DText {
        width: parent.width
        text: toast.modelData.appName
        dim: true
        font.pixelSize: Theme.font.size.caption
        elide: Text.ElideRight
        visible: text !== ""
      }
      DText {
        width: parent.width
        text: toast.modelData.summary
        font.pixelSize: Theme.font.size.title
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
        maximumLineCount: 2
        elide: Text.ElideRight
      }
      DText {
        width: parent.width
        text: toast.modelData.body
        dim: true
        font.pixelSize: Theme.font.size.bodyS
        wrapMode: Text.WordWrap
        maximumLineCount: 4
        elide: Text.ElideRight
        visible: text !== ""
      }
    }

    MouseArea {
      id: hover
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      onClicked: mouse => {
        if (mouse.button === Qt.RightButton) toast.modelData.dismiss();
        else toast.alive = false;
      }
    }
  }
}
