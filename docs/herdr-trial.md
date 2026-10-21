# Herdr trial (7 days) — keep exactly one multiplexer afterwards

Switch:  `sed -i 's/multiplexer = "tmux"/multiplexer = "herdr"/' ~/.config/chezmoi/chezmoi.toml && chezmoi apply`
Back:    `sed -i 's/multiplexer = "herdr"/multiplexer = "tmux"/' ~/.config/chezmoi/chezmoi.toml && chezmoi apply`
(`promptChoiceOnce` keeps the stored value, so `chezmoi init` will not reset it.)

| # | Criterion | Pass if | Result |
|---|---|---|---|
| 1 | vi copy mode + yank | search, select, yank to wl-clipboard work from keyboard | |
| 2 | Claude agents state | sidebar shows working/blocked/idle correctly for 3+ concurrent Claude Code panes | |
| 3 | Restore | after reboot, agents resume with their original flags (incl. `--dangerously-skip-permissions`, `-c`) | |
| 4 | Notifications | a blocked agent produces a SwayNC toast within 10 s | |
| 5 | Popups | pass-menu and ssh-menu work from prefix keys | |
| 6 | nvim | working without tmux-config's "hide status when nvim focused" is acceptable | |
| 7 | Memory | `ps -o rss= -C herdr` with 6 agents ≤ 200 MB | |
| 8 | Stability | no crash in `journalctl --user -u herdr` over 7 days | |

Decision: Herdr wins only if 1–5 and 8 pass. Record the decision and date here, then run Task 12.

## Notes for the trial

- Server unit: `systemctl --user enable --now herdr.service` (runs `herdr server`, the headless server; the
  client attaches with plain `herdr`). The tmux unit is not deployed while `multiplexer = "herdr"`.
- Config: `~/.config/herdr/config.toml` (chezmoi-rendered). Validate with `herdr config check`; reload the
  running server with `herdr server reload-config`.
- Prefix is `ctrl+space` (same as tmux-config). `prefix+p` opens the pass popup and `prefix+S` the SSH key popup
  (as in tmux). Herdr's default `previous_tab` (`prefix+p`) was rebound to `prefix+comma`; `prefix+?` lists the
  active bindings.
- Toasts: `[ui.toast] delivery = "system"` sends background notifications through the OS service (SwayNC).
- What chezmoi owns here: `~/.config/herdr/config.toml` (templated) and the plugin set —
  `.chezmoiscripts/28-herdr-plugins.sh` installs all 13 plugins pinned to reviewed commits and
  re-pins when the list changes. herdr's own runtime state (installed plugin trees and their built
  binaries, `plugins.json`, sockets, logs, `session.json`, session snapshots) is deliberately
  unmanaged and is listed in `.chezmoiignore`, so `chezmoi add -r ~/.config/herdr` cannot sweep
  sockets or session state into this public repo.
- The shared scripts (pass-menu, ssh-menu, task-status, git-status, ssh-askpass, herdr-keys) live in
  `~/.config/dots-mux` under both multiplexers. `claude-notify.sh` moved there too, with its icon
  (`~/.config/dots-mux/assets/claude.png`): it used to sit under `~/.config/tmux/scripts` and bail out
  unless `$TMUX` was set, so under Herdr the Claude Code hook raised nothing at all — and its icon was
  dropped anyway, since the herdr profile ignores `.config/tmux`. It now recognises both multiplexers
  and records where to jump. The click itself is handled by `claude-focus.sh`, which SwayNC runs
  on action (`scripts.claude-focus.run-on = "action"`); it focuses the terminal window via
  Hyprland and then `herdr workspace focus` + `herdr tab focus`. herdr can only focus panes
  directionally, so it lands on the tab — exact for a single-pane agent tab. The hook cannot wait
  for the click itself: `notify-send --action` implies `--wait`, and a hook that blocks stalls
  Claude, while a backgrounded listener is orphaned and never receives ActionInvoked.
