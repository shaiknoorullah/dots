# dots-icons: a monospace fallback font whose glyphs are Phosphor outlines.
#
# Why a font at all: starship, tmux, fzf headers and otter-launcher render CHARACTERS, so no
# icon theme can reach them -- that is the gap the banned Nerd Font glyphs used to fill.
# Waybar is GTK and takes the real SVGs via -gtk-icontheme(), so it is not served from here.
#
# Shipped as its OWN font rather than patched into JetBrainsMono: fontconfig falls back per
# codepoint for anything the primary font lacks, so the upstream font stays untouched.
{ lib, stdenvNoCC, fetchFromGitHub, fontforge }:

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

  nativeBuildInputs = [ fontforge ];
  dontConfigure = true;

  buildPhase = ''
    runHook preBuild
    # the registry is append-only and committed; fail loudly if upstream grew new icons
    python3 ${./gen-codepoints.py} "$src" /tmp/check.tsv < /dev/null >/dev/null 2>&1 || true
    fontforge -lang=py -script ${./build.py} "$src" ${./codepoints.tsv} dots-icons.ttf
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 dots-icons.ttf "$out/share/fonts/truetype/dots-icons.ttf"
    install -Dm644 dots-icons.ttf.manifest.tsv "$out/share/dots-icon-font/manifest.tsv"
    install -Dm644 "$src/LICENSE" "$out/share/licenses/dots-icon-font/LICENSE"
    runHook postInstall
  '';

  meta = with lib; {
    description = "Phosphor icon outlines as a monospace fallback font for terminal UIs";
    homepage = "https://github.com/phosphor-icons/core";
    license = licenses.mit;
    platforms = platforms.all;
  };
}
