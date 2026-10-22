#!/usr/bin/env bash
# Shared otter-styled fzf options + PATH, sourced by the otter module scripts.
#
# Two conventions live in here, and every picker in the repo follows them so that moving
# between them feels like one program:
#
#   --prompt   is the SAME mark the shell prompt and the launcher use (caret-right from
#              dots-icon-font, starship.toml success_symbol). The purge that stripped the
#              Nerd Font glyphs left a two-space hole here and in five other pickers, which
#              is why they all had a floating indent and no prompt at all. A picker that
#              asks for a VALUE rather than filtering a list appends a word after it
#              ("􀜌 capture", "􀜌 name"); a picker that filters a list does not.
#   --header   names the list, prefixed by the SAME icon as the launcher entry that opens
#              it, so `app` in the launcher and the apps picker show one glyph between them.
#              --header-first puts that name at the very top rather than between the prompt
#              and the results, where it read as the first row of the list.
#
# Icons mark top-level destinations only. A second-level action list (start/stop/restart,
# kill which session) stays plain text: the header icon already says where you are, and a
# column of glyphs beside four verbs is width without information.
export FZF_DEFAULT_OPTS="--reverse --no-scrollbar --no-separator --info=inline \
--border=none --margin=1 --padding=1 --header-first \
--prompt='􀜌  ' --pointer='▌' --marker='▌' \
--color=bg:-1,bg+:-1,fg:-1,fg+:15,hl:5,hl+:13,pointer:5,prompt:5,marker:5,gutter:-1,header:8,preview-bg:-1"
