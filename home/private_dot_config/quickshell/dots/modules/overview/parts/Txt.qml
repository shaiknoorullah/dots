import QtQuick
import qs.modules.overview

// One text style entry point. role picks size and family; numbers always use
// mono so counters do not jitter. dim drops the colour to fgFaint (stale data).
Text {
    id: t
    property string role: "body"        // caption | bodyS | body | title | headline | display | mono | monoS
    property bool dim: false
    property bool faint: false          // secondary text that is not stale

    readonly property bool isMono: role === "mono" || role === "monoS"
    color: dim ? Look.fgFaint : (faint ? Look.fgDim : Look.fg)
    font.family: isMono ? Look.mono : Look.sans
    font.pixelSize: role === "caption" ? Look.fCaption
        : role === "bodyS" || role === "monoS" ? Look.fBodyS
        : role === "title" ? Look.fTitle
        : role === "headline" ? Look.fHeadline
        : role === "display" ? Look.fDisplay
        : Look.fBody
    font.weight: role === "title" || role === "headline" || role === "display" ? Font.DemiBold : Font.Normal
    lineHeight: 1.35
    elide: Text.ElideRight
    renderType: Text.NativeRendering
}
