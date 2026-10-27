pragma Singleton
import QtQuick

// TEST STUB of T2's modules/theme/Theme.qml. Same key names as tokens.json (preset
// = fixed Dracula). It exists only so the overview renders under tests/overview-ui.sh
// without T2; the real singleton replaces it at integration. Never shipped on its own.
QtObject {
    readonly property var color: ({
        bg: "#282a36", surface: "#21222c", surfaceDim: "#191a21", surfaceContainer: "#2c2e3b",
        surfaceContainerHigh: "#343746", outline: "#6272a4", outlineVariant: "#44475a",
        fg: "#f8f8f2", fgDim: "#bfc0cc", fgFaint: "#6f7391",
        accent: "#bd93f9", accentFg: "#282a36", amber: "#ffb86c", red: "#ff5555",
        green: "#50fa7b", purple: "#bd93f9", pink: "#ff79c6", cyan: "#8be9fd", yellow: "#f1fa8c"
    })
    readonly property var alpha: ({ slab: 0.78, panel: 0.86, wash: 0.20, hover: 0.10, divider: 0.24 })
    readonly property var radius: ({ chip: 8, panel: 12, card: 16, pill: 999 })
    readonly property var space: ({ xs: 2, s: 4, m: 6, l: 8, xl: 12, xxl: 16, xxxl: 24 })
    readonly property var font: ({
        sans: "Lato", mono: "JetBrainsMono Nerd Font", icon: "Material Symbols Rounded",
        size: { caption: 11, bodyS: 12, body: 13, title: 16, headline: 20, display: 28 }
    })
    readonly property var motion: ({ fast: 120, base: 180, slow: 240 })
    readonly property var bar: ({ height: 30, gutter: 8, chipGapPx: 6 })
}
