pragma ComponentBehavior: Bound
import Quickshell
import Quickshell.Io
import Quickshell.Services.Notifications
import QtQuick
import QtQml

// The notification daemon. Quickshell's server and swaync cannot both own
// org.freedesktop.Notifications, so this asks the bus first and, when another daemon
// holds the name, logs one line and stays out. The bell chip then shows a hollow "?".
// No crash loop, no fight for the name.
Scope {
  id: root

  property bool checked: false
  property bool held: false          // another daemon owns the name
  property bool dnd: false
  readonly property var model: lazy.item ? (lazy.item as NotificationServer).trackedNotifications : null
  // counted by an Instantiator: `model.values.length` is not re-notified on every
  // insert/remove, so the bell read 0 while the centre listed a dozen
  readonly property int count: tally.count

  signal arrived(var notification)

  function clearAll() {
    if (!model) return;
    var v = model.values.slice();
    for (var i = 0; i < v.length; i++) v[i].dismiss();
  }

  Instantiator {
    id: tally
    model: root.model
    delegate: QtObject {}
  }

  Process {
    id: probe
    // held = owned by a process that is not this shell; on a config reload the owner is
    // this very process (an ancestor of the probe), which must not count as "another"
    command: ["sh", "-c", "p=$(busctl --user status org.freedesktop.Notifications 2>/dev/null | sed -n 's/^PID=//p'); [ -n \"$p\" ] || exit 1; q=$$; while [ \"$q\" -gt 1 ]; do [ \"$q\" = \"$p\" ] && exit 1; q=$(cut -d' ' -f4 /proc/$q/stat); done; exit 0"]
    running: true
    onExited: code => {
      root.held = (code === 0);
      root.checked = true;
      if (root.held) console.log("notif: org.freedesktop.Notifications is held by another daemon; not claiming it");
    }
  }

  LazyLoader {
    id: lazy
    active: root.checked && !root.held

    NotificationServer {
      keepOnReload: false
      actionsSupported: true
      bodySupported: true
      imageSupported: true
      onNotification: notification => {
        notification.tracked = true;
        root.arrived(notification);
      }
    }
  }
}
