pragma ComponentBehavior: Bound
import Quickshell
import Quickshell.Wayland
import QtQuick
import "../theme"

// Toasts, top right, offset 8,38 from the screen corner (just under the bar), stacked
// with an 8 px gap. The window is exactly as tall as what is showing and is unmapped
// when nothing is, so it never swallows a click it has no business with.
PanelWindow {
  id: win

  property var notifs: null
  // how many toasts are on screen right now (the IPC test hook asks; nothing binds to it)
  function visibleCount() {
    var n = 0;
    for (var i = 0; i < stack.children.length; i++)
      if (stack.children[i].alive !== undefined && stack.children[i].visible) n++;
    return n;
  }

  anchors { top: true; right: true }
// PanelWindow.margins is real but Quickshell does not export it to qmllint
  // qmllint disable unqualified unresolved-type
  margins.top: 38
  margins.right: 8
  implicitWidth: 380
  implicitHeight: Math.max(1, stack.implicitHeight)
  visible: stack.implicitHeight > 0
  color: "transparent"
  exclusionMode: ExclusionMode.Ignore
  WlrLayershell.namespace: "qs-notif"
  WlrLayershell.layer: WlrLayer.Overlay

  Column {
    id: stack
    width: parent.width
    spacing: Theme.space.l

    Repeater {
      model: win.notifs ? win.notifs.model : null

      Toast {
        dnd: win.notifs ? win.notifs.dnd : false
      }
    }
  }
}
