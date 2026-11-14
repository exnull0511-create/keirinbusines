#!/usr/bin/env bash
# data ブランチを ./data に用意する（無ければ作る）／変更を push する
set -euo pipefail
cmd=${1:-checkout}
REMOTE="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

if [ "$cmd" = checkout ]; then
  if git ls-remote --exit-code "$REMOTE" data >/dev/null 2>&1; then
    git clone -q --depth 1 -b data "$REMOTE" data
  else
    mkdir -p data && cd data && git init -q -b data && git remote add origin "$REMOTE"
    echo "# 競輪データ（自動収集）" > README.md && git add README.md
    git -c user.name=github-actions -c user.email=actions@github.com commit -qm "init data branch"
    git push -q origin data
  fi
elif [ "$cmd" = push ]; then
  cd data
  # rebase でコミットを積み直すため、作業者情報を設定（未設定だと他ジョブと同時に動いたときに push できない）
  git config user.name github-actions
  git config user.email actions@github.com
  git add -A
  if ! git diff --cached --quiet; then
    git -c user.name=github-actions -c user.email=actions@github.com commit -qm "${2:-update} $(date -u +%FT%TZ)"
    ok=0
    for i in 1 2 3 4 5; do
      if git pull -q --rebase origin data && git push -q origin data; then ok=1; break; fi
      git rebase --abort 2>/dev/null || true
      sleep $((i * 5))
    done
    [ "$ok" = 1 ] || { echo "data ブランチへの push に失敗"; exit 1; }
  fi
fi
