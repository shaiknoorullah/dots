pragma Singleton
import QtQuick
import qs.modules.theme

// The ONLY file in the overview that names Theme.*. Everything else reads Look.*,
// so a change in how T2 exposes tokens is a one-file fix. Values come from
// tokens.json via Theme; the overview never reads palette.yaml or calls matugen.
QtObject {
    // colour roles
    readonly property color surface: Theme.color.surface
    readonly property color surfaceHigh: Theme.color.surfaceContainerHigh
    readonly property color outline: Theme.color.outline
    readonly property color fg: Theme.color.fg
    readonly property color fgDim: Theme.color.fgDim
    readonly property color fgFaint: Theme.color.fgFaint
    readonly property color accent: Theme.color.accent
    readonly property color accentFg: Theme.color.accentFg
    readonly property color amber: Theme.color.amber      // someone waits on me
    readonly property color red: Theme.color.red          // failed, nobody waits

    // alpha
    readonly property real aPanel: Theme.alpha.panel
    readonly property real aWash: Theme.alpha.wash
    readonly property real aHover: Theme.alpha.hover
    readonly property real aDivider: Theme.alpha.divider

    // radius and spacing
    readonly property int rChip: Theme.radius.chip
    readonly property int rPanel: Theme.radius.panel
    readonly property int rCard: Theme.radius.card
    readonly property int rPill: Theme.radius.pill
    readonly property int sXs: Theme.space.xs
    readonly property int sS: Theme.space.s
    readonly property int sM: Theme.space.m
    readonly property int sL: Theme.space.l
    readonly property int sXl: Theme.space.xl
    readonly property int sXxl: Theme.space.xxl
    readonly property int sXxxl: Theme.space.xxxl

    // type
    readonly property string sans: Theme.font.sans
    readonly property string mono: Theme.font.mono
    readonly property string iconFont: Theme.font.icon
    readonly property int fCaption: Theme.font.size.caption
    readonly property int fBodyS: Theme.font.size.bodyS
    readonly property int fBody: Theme.font.size.body
    readonly property int fTitle: Theme.font.size.title
    readonly property int fHeadline: Theme.font.size.headline
    readonly property int fDisplay: Theme.font.size.display

    // motion
    readonly property int mFast: Theme.motion.fast
    readonly property int mBase: Theme.motion.base
    readonly property int mSlow: Theme.motion.slow

    // bar
    readonly property int barHeight: Theme.bar.height

    // fixed by the spec's pixel table, not by tokens
    readonly property int cardWidth: 380
    readonly property int cardMaxHeight: 520
    readonly property int notchW: 8
    readonly property int notchH: 4
    readonly property int panelWidth: 920
    readonly property int panelHeight: 640
    readonly property int cell: 11
    readonly property int cellGap: 3
    readonly property int heatCols: 17
    readonly property int heatRows: 7
    readonly property int ringDiameter: 20
    readonly property int ringStroke: 2
    readonly property var heatRamp: [0, 0.12, 0.30, 0.52, 0.80]

    function withAlpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
    function wash(c) { return withAlpha(c, aWash) }
}
