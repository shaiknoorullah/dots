#!/usr/bin/env bash
# waybar-kube.sh — current kube context/namespace of the default KUBECONFIG
# (discovered per machine by kube-discover.sh; exported via .zshenv, environment.d and hyprland.lua).
# class kube-up / kube-down from a 3s /readyz probe so an unreachable cluster shows red.
if [ -z "${KUBECONFIG:-}" ] && [ -r "${XDG_CONFIG_HOME:-$HOME/.config}/dots/kube.env" ]; then
  . "${XDG_CONFIG_HOME:-$HOME/.config}/dots/kube.env"
fi
[ -n "${KUBECONFIG:-}" ] && export KUBECONFIG
# KUBECONFIG is legitimately unset when this machine has no kubeconfig at all, so the tooltip needs a
# label either way: kubectl falls back to ~/.kube/config, and that is what it would be reading.
kcfg="${KUBECONFIG:-$HOME/.kube/config (default)}"
ctx=$(kubectl config current-context 2>/dev/null)
[ -z "$ctx" ] && { printf '{"text":"​none","class":"kube-none","tooltip":"no context in %s"}\n' "$kcfg"; exit 0; }
ns=$(kubectl config view --minify -o jsonpath='{..namespace}' 2>/dev/null); ns=${ns:-default}
short=${ctx#*@}   # strip any user@ prefix: user@cluster -> cluster
if timeout 3 kubectl get --raw /readyz >/dev/null 2>&1; then cls=kube-up; st=reachable; else cls=kube-down; st=UNREACHABLE; fi
printf '{"text":"​%s%s","class":"%s","tooltip":"%s · ns %s · %s\\n%s"}\n' \
  "$short" "$([ "$ns" != default ] && printf ' (%s)' "$ns")" "$cls" "$ctx" "$ns" "$st" "$kcfg"
