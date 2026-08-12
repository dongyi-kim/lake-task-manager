#!/usr/bin/env bash
set -euo pipefail

# Historical four-arm prompt-language battery runner. The linked experiment
# worktrees live beside the main repository; results stay in this archive.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../.." && pwd)"
deploy_root="$(cd "$repo_root/.." && pwd)"
results_dir="$repo_root/research/agent-improvement/results"
logs_dir="$repo_root/research/agent-improvement/logs"

mkdir -p "$results_dir" "$logs_dir"

for arm in base ko en guide; do
  worktree="$deploy_root/.exp-$arm"
  if [[ ! -d "$worktree" ]]; then
    echo "missing experiment worktree: $worktree" >&2
    exit 1
  fi

  rm -rf -- "$deploy_root/.cache/agent_index"
  echo "=== $arm start $(date +%H:%M:%S)"
  (
    cd "$worktree"
    JIRA_ENV=mock python -X utf8 -u tools/agent_lang_ab.py \
      "$results_dir/ab-$arm.json" > "$logs_dir/log-$arm-ab.txt" 2>&1
    JIRA_ENV=mock python -X utf8 -u tools/agent_compose_eval.py \
      > "$logs_dir/log-$arm-compose.txt" 2>&1
    JIRA_ENV=mock python -X utf8 -u tools/agent_create_suite.py \
      > "$logs_dir/log-$arm-create.txt" 2>&1
  )
  echo "=== $arm done $(date +%H:%M:%S)"
done

echo "ALL DONE"
