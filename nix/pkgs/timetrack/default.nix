# timetrack: the work-time CLI. Units live in home/ (timetrack-*), gated by .timetrack.
#
# Pure stdlib on purpose. sqlite-utils and requests are gone with the modules that used
# them: sqlite_utils inferred its schema from the first row written, so the old code could
# not distinguish "no work today" from "the table was never created" -- on the file that
# determines pay. Explicit DDL in migrate.py replaces it, and dropping both means every
# script here is debuggable with the system python, not only inside the Nix env.
{ python3, writeShellApplication, symlinkJoin, taskwarrior3, timewarrior, git }:
let
  py = python3.withPackages (ps: with ps; [ jeepney ]);
  cli = writeShellApplication {
    name = "timetrack";
    runtimeInputs = [ py taskwarrior3 timewarrior git ];
    text = ''
      export PYTHONPATH="${./.}:''${PYTHONPATH:-}"
      exec python -m timetrack "$@"
    '';
  };
  logind = writeShellApplication {
    name = "timetrack-logind";
    runtimeInputs = [ py ];
    text = ''exec python3 ${./logind/timetrack-logind.py}'';
  };
in symlinkJoin { name = "timetrack"; paths = [ cli logind ]; }
