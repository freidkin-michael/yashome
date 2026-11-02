#!/bin/sh
# Helper job (apply|update|rollback|restore|restart) run in its own container so it survives the helper being recreated.
set -eu

cd "$(dirname "$0")/.."
mkdir -p .helper

kind=${1:-}
case "$kind" in
    apply|update|rollback|restore|restart) ;;
    *) kind=invalid ;;
esac

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

write_state() {
    printf '{"kind": "%s", "state": "%s", "started": "%s", "ended": %s, "rc": %s}\n' \
        "$kind" "$1" "$started" "$2" "$3" > .helper/job.json.tmp
    mv -f .helper/job.json.tmp .helper/job.json
}

finish() {
    rc=$?
    trap - EXIT
    set +e
    if [ "$rc" -eq 0 ]; then state="done"; else state=error; fi
    echo "== $kind $state rc=$rc $(now)"
    write_state "$state" "\"$(now)\"" "$rc"
    exit "$rc"
}

g() { git -c 'safe.directory=*' "$@"; }

clean_tree() {
    if [ -f .git ]; then
        echo "refusing $1: this checkout is a git worktree, its repository is outside the helper's view" >&2
        exit 4
    fi
    # Untracked files (.env, .helper/) survive a reset and a pull, so only tracked changes block them.
    changes=$(g status --porcelain --untracked-files=no)
    if [ -n "$changes" ]; then
        echo "refusing $1, tracked files have local changes:" >&2
        echo "$changes" >&2
        exit 3
    fi
}

started=$(now)
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
: > .helper/job.log
write_state running null null
exec >> .helper/job.log 2>&1
echo "== $kind start $started"

case "$kind" in
    apply)
        make update
        ;;
    update)
        clean_tree update
        before=$(g rev-parse HEAD)
        g fetch
        if [ "$(g rev-parse '@{u}')" = "$before" ]; then
            echo "already up to date"
            exit 0
        fi
        backup=$(python3 tools/backup.py make)
        echo "backup: $backup"
        g merge --ff-only '@{u}'
        # Only a pull that moved HEAD replaces the rollback target (a failed or empty one keeps it).
        if [ "$(g rev-parse HEAD)" != "$before" ]; then
            printf '%s\n' "$before" > .helper/previous.tmp && mv -f .helper/previous.tmp .helper/previous
            printf '%s\n' "$backup" > .helper/previous-backup.tmp && mv -f .helper/previous-backup.tmp .helper/previous-backup
        fi
        make update
        ;;
    rollback)
        prev=$(cat .helper/previous 2>/dev/null || :)
        [ -n "$prev" ] || { echo "no previous commit recorded" >&2; exit 2; }
        clean_tree rollback
        g reset --hard "$prev"
        make update
        rm -f .helper/previous
        # The code is back; the house's files are not touched. Their state before the update:
        [ -f .helper/previous-backup ] && echo "backup made before the update: $(cat .helper/previous-backup)"
        rm -f .helper/previous-backup
        ;;
    restore)
        f=${2:-}
        case "$f" in yashome-backup-*.tar.gz) ;; *) echo "restore: no backup named" >&2; exit 2 ;; esac
        [ -f "backups/$f" ] || { echo "restore: backups/$f is missing" >&2; exit 2; }
        # the files as they are now stay in a backup of their own before the restore puts the old ones back
        echo "backup of the current state: $(python3 tools/backup.py make)"
        sh tools/restore.sh "backups/$f"
        ;;
    restart)
        # every container of the stack made again: one that came back from Docker's own restart with no network
        # (a link drop took the LAN address while it restarted) gets its endpoint again; a restart alone keeps it lost
        docker compose up -d --force-recreate --remove-orphans
        ;;
    *)
        echo "unknown job kind: ${1:-}" >&2
        exit 2
        ;;
esac
