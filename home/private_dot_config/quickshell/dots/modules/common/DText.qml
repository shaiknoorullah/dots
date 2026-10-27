import QtQuick
import "../theme"

// Body text. Numerals that change (counters, clocks, percentages) set mono: true so
// a changing digit never jitters the layout.
Text {
  property bool mono: false
  property bool dim: false

  font.family: mono ? Theme.font.mono : Theme.font.sans
  font.pixelSize: Theme.font.size.body
  color: dim ? Theme.color.fgDim : Theme.color.fg
  verticalAlignment: Text.AlignVCenter
  lineHeight: 1.35
  renderType: Text.NativeRendering
}
