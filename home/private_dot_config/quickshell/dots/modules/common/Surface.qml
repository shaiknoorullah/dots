import QtQuick
import "../theme"

// A translucent panel. The compositor blurs it (layer rule per namespace), which is
// why it is translucent rather than merely dark.
Rectangle {
  property real fill: Theme.alpha.panel

  color: Qt.alpha(Theme.color.surface, fill)
  radius: Theme.radius.panel
  border.width: 1
  border.color: Qt.alpha(Theme.color.outline, Theme.alpha.divider)
}
