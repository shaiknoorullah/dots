# Phosphor-Dark: a freedesktop icon theme generated from the Phosphor icon set (MIT).
#
# Upstream is a UI icon set with its own names (assets/<weight>/<name>.svg), NOT an icon
# theme -- nothing to install, so mapping.tsv bridges Phosphor names onto freedesktop ones
# and build.sh emits index.theme plus the context directories.
#
# Only semantic names are mapped. App/brand logos are deliberately left out (Phosphor has
# no logos) and Inherits=breeze-dark catches them, so no app loses its identity.
{ lib, stdenvNoCC, fetchFromGitHub, gtk3 }:

stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "phosphor-icon-theme";
  version = "2.1.1-unstable-2026-10-22";

  src = fetchFromGitHub {
    owner = "phosphor-icons";
    repo = "core";
    rev = "2b75f3ad12b420c9504ef05df8d2564a28f8500e";
    sha256 = "0sjvc91scg5b6f1m084gpalhlcpsdvj61bql0f95r5nkib7h1av9";
  };

  nativeBuildInputs = [ gtk3 ];   # gtk-update-icon-cache
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    bash ${./build.sh} "$src" ${./mapping.tsv} "$out" Phosphor-Dark
    install -Dm644 "$src/LICENSE" "$out/share/licenses/phosphor-icon-theme/LICENSE"
    # A theme with only scalable dirs works without a cache, but GTK logs a miss on every
    # lookup without one; building it here keeps the profile read-only at runtime.
    gtk-update-icon-cache -q -f -t "$out/share/icons/Phosphor-Dark"
    runHook postInstall
  '';

  meta = with lib; {
    description = "Phosphor icons mapped onto freedesktop icon names, inheriting breeze-dark";
    homepage = "https://github.com/phosphor-icons/core";
    license = licenses.mit;
    platforms = platforms.all;
  };
})
