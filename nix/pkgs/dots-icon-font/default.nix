# dots-icons: one monospace fallback font carrying four MONOCHROME icon sets.
#
# Why a font at all: starship, tmux, fzf headers and otter-launcher render CHARACTERS, so
# no icon theme can reach them. GTK surfaces (waybar, swaync) use the real SVGs through
# -gtk-icontheme() instead and are deliberately NOT served from here.
#
# Why these four: Phosphor is the UI vocabulary, but it has no brand or language marks --
# which is exactly what a prompt and a launcher need. Simple Icons, Devicon Plain and
# Codicons fill that gap and are all single-colour, so they inherit the terminal's
# foreground like Phosphor does. The multicolour sets (SVG Logos, the coloured Devicon)
# are excluded on purpose: a font glyph has no colour and they would flatten into blobs.
#
# Shipped as its OWN font rather than patched into JetBrainsMono, so fontconfig falls back
# per codepoint and the upstream font stays untouched.
{ lib, stdenvNoCC, fetchFromGitHub, fetchurl, fontforge, python3 }:

let
  iconify = name: version: hash: fetchurl {
    url = "https://registry.npmjs.org/@iconify-json/${name}/-/${name}-${version}.tgz";
    sha256 = hash;
  };
  bundles = {
    simple-icons  = iconify "simple-icons"  "1.2.100" "0arwnvfsi6khnw2cxp2rpkqiask4v098hbg0qammn8d3q4cfr26j";
    devicon-plain = iconify "devicon-plain" "1.2.62"  "03rr5clghjkr1b0rkvqsvqlq1q61kvihnm540fsjz01lp3rzw8y3";
    codicon       = iconify "codicon"       "1.2.75"  "1bd8kfwazc4sppb048wp1v96x48jq4gr6s50qlr71n6gj3j375dw";
  };
in
stdenvNoCC.mkDerivation {
  pname = "dots-icon-font";
  version = "unstable-2026-10-22";

  # same pin as phosphor-icon-theme: one upstream revision for icons and glyphs alike
  src = fetchFromGitHub {
    owner = "phosphor-icons";
    repo = "core";
    rev = "2b75f3ad12b420c9504ef05df8d2564a28f8500e";
    sha256 = "0sjvc91scg5b6f1m084gpalhlcpsdvj61bql0f95r5nkib7h1av9";
  };

  nativeBuildInputs = [ fontforge python3 ];
  dontConfigure = true;

  buildPhase = ''
    runHook preBuild
    mkdir -p bundles
    ${lib.concatStringsSep "\n" (lib.mapAttrsToList (n: t: ''
      mkdir -p bundles/${n}
      tar -xzf ${t} -C bundles/${n} --strip-components=1 package/icons.json
    '') bundles)}

    # The registry is append-only and committed. Regenerating here must not CHANGE any
    # assignment -- a moved codepoint silently breaks every config that holds that
    # character -- so the rebuilt file is compared against the committed one.
    cp ${./codepoints.tsv} check.tsv
    chmod +w check.tsv   # store copies are read-only
    python3 ${./gen-codepoints.py} check.tsv "$src" bundles/*/icons.json
    if ! diff -q check.tsv ${./codepoints.tsv} >/dev/null; then
      echo "codepoints.tsv is stale: upstream has icons the committed registry lacks." >&2
      echo "Run gen-codepoints.py and commit the result." >&2
      diff ${./codepoints.tsv} check.tsv | head -20 >&2
      exit 1
    fi

    fontforge -lang=py -script ${./build.py} dots-icons.ttf ${./codepoints.tsv} \
      "$src" bundles/*/icons.json
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 dots-icons.ttf "$out/share/fonts/truetype/dots-icons.ttf"
    install -Dm644 dots-icons.ttf.manifest.tsv "$out/share/dots-icon-font/manifest.tsv"
    install -Dm644 "$src/LICENSE" "$out/share/licenses/dots-icon-font/LICENSE.phosphor"
    runHook postInstall
  '';

  meta = with lib; {
    description = "Phosphor, Simple Icons, Devicon Plain and Codicons as one monospace fallback font";
    homepage = "https://github.com/phosphor-icons/core";
    license = with licenses; [ mit cc0 cc-by-40 ];
    platforms = platforms.all;
  };
}
