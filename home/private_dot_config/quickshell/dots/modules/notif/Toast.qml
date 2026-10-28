import Quickshell.Services.Notifications
import QtQuick
import "../theme"
import "../common"

// One toast: 380 wide, padding 14, radius 16. Click opens the source, × dismisses, action
// buttons run the app's actions (NotifCard). It hides itself when its time is up (the
// notification stays in the centre) and re-appears when the app REPLACES it in place:
// the server updates the same Notification object, so summary/body changing is the cue.
Item {
  id: toast

  required property var modelData      // Notification
  property bool dnd: false
  property bool alive: true

  readonly property bool critical: modelData.urgency === NotificationUrgency.Critical
  readonly property int lifetime: modelData.expireTimeout > 0 ? modelData.expireTimeout * 1000 : 6000

  readonly property bool shown: alive && (!dnd || critical)

  width: 380
  implicitHeight: shown ? box.height : 0
  height: implicitHeight
  visible: shown

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
    running: toast.alive && !toast.critical && !hover.hovered
    onTriggered: toast.alive = false
  }

  Surface {
    id: box
    corner: Theme.radius.card
    border.color: toast.critical ? Qt.alpha(Theme.color.red, 0.8) : Qt.alpha(Theme.color.outline, Theme.alpha.divider)
    width: parent.width
    height: col.implicitHeight + 2 * 14

    NotifCard {
      id: col
      x: 14
      y: 14
      width: parent.width - 28
      notification: toast.modelData
      summaryLines: 2
      bodyLines: 4
      onOpened: toast.alive = false
    }

    HoverHandler { id: hover }
  }
}
