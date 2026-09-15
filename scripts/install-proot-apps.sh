#!/bin/bash
# Cove's proot-apps driver. See https://github.com/linuxserver/proot-apps
#
# Two entry points:
#
#  * Boot: mounted at /custom-cont-init.d/98-install-proot-apps.sh and run as
#    root with no arguments. Installs the apps listed in $PROOT_APPS (space/comma
#    separated) that aren't installed yet, as a background task.
#
#  * Control plane: the backend runs this file's text through
#    `docker exec -u abc … bash -c "<script>" cove-proot <command> [args]`
#    (so it works in containers launched before the script was mounted):
#      list                     installed apps + their layer digests
#      start <id> <op> <app>…   record a queued task (op: install|update|remove)
#      run <id>                 execute a recorded task (a detached exec)
#      tasks                    state of every recorded task
#      log <id>                 tail of one task's output
#      clear                    forget finished tasks
#
# Everything that touches files runs as the desktop user, never root: /config
# and the task directory are writable by that user, so a root write there could
# be redirected through a planted symlink. Output is line-oriented and
# tab-separated; the backend treats it as untrusted and re-validates every field.
set -u

APP_RE='^[a-z0-9][a-z0-9._-]{0,63}$'
ID_RE='^[A-Za-z0-9-]{1,64}$'
# Registry pulls in proot-apps have no timeout, so a stalled ghcr.io blob would
# otherwise hang a task (and the lock every later task waits on) forever.
APP_TIMEOUT=900
# Active (queued/running) tasks allowed at once, and finished ones kept.
MAX_ACTIVE=3
MAX_KEPT=20

# ── Boot (root) ────────────────────────────────────────────────────────────────
if [ "$#" -eq 0 ]; then
  apps="${PROOT_APPS:-}"
  apps="${apps//,/ }"
  [ -z "${apps// /}" ] && exit 0
  # Background it: custom-cont-init.d runs *before* the desktop services start,
  # so a long app list here would block the GUI from ever becoming ready and the
  # workspace would overrun the launch deadline. Apps appear as each finishes.
  # Older releases appended the boot log as root, which the desktop user can't
  # append to now. Move it aside rather than chown it: rename(2) acts on the
  # directory entry itself, so a planted symlink or hard link can't redirect it.
  log=/config/.cove-proot-apps.log
  if [ -e "${log}" ] && [ "$(stat -c %u "${log}" 2>/dev/null)" = 0 ]; then
    mv -f -- "${log}" "${log}.old" 2>/dev/null
  fi
  # shellcheck disable=SC2086
  HOME=/config s6-setuidgid abc /bin/bash "$0" boot $apps </dev/null >/dev/null 2>&1 &
  echo "[cove] proot-apps: installing in background (${apps}); see the tasks menu"
  exit 0
fi

# ── Everything below runs as the desktop user ──────────────────────────────────
if [ "$(id -u)" -eq 0 ]; then
  echo "refusing to run as root" >&2
  exit 1
fi

export HOME=/config
# System directories first, so the helpers below resolve to the image's own
# binaries. LinuxServer Selkies images ship proot-apps at /proot-apps (with a
# per-user copy in ~/.local/bin); neither is on the default PATH.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/proot-apps:/config/.local/bin"

APPS_DIR="${HOME}/proot-apps"
PREFIX="ghcr.io_linuxserver_proot-apps_"
# Per-container (not /config): a task can't outlive its container, and a shared
# profile mounts one /config into several workspaces whose tasks must not mix.
TASKS_DIR="/tmp/cove-proot-apps-$(id -u)"
BOOT_LOG="${HOME}/.cove-proot-apps.log"

ensure_tasks_dir() {
  mkdir -m 700 -p "${TASKS_DIR}" 2>/dev/null
  # Anything else already sitting at this path isn't ours to write through.
  if [ -L "${TASKS_DIR}" ] || [ ! -d "${TASKS_DIR}" ] || [ ! -O "${TASKS_DIR}" ]; then
    echo "task directory ${TASKS_DIR} is unusable" >&2
    exit 1
  fi
}

valid_id() { [[ "$1" =~ $ID_RE ]]; }
valid_app() { [[ "$1" =~ $APP_RE ]]; }
now() { date +%s; }

# Process start time (field 22 of /proc/<pid>/stat), which tells a live task
# apart from an unrelated process that later reused its pid.
proc_start() {
  local stat rest
  stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
  rest=${stat##*) }
  # shellcheck disable=SC2086
  set -- $rest
  echo "${20}"
}

# Read one key from a task's state file (plain key=value lines).
state_get() {
  local line
  while IFS= read -r line; do
    if [ "${line%%=*}" = "$2" ]; then
      echo "${line#*=}"
      return
    fi
  done <"${TASKS_DIR}/$1/state" 2>/dev/null
}

# Update keys in a task's state file atomically (write-then-rename), so a
# concurrent `tasks` never reads a half-written file.
state_set() {
  local id=$1 file tmp key line keep
  shift
  file="${TASKS_DIR}/${id}/state"
  tmp="${file}.$$"
  {
    if [ -f "${file}" ]; then
      while IFS= read -r line; do
        keep=1
        for key in "$@"; do
          [ "${line%%=*}" = "${key%%=*}" ] && keep=0
        done
        [ "${keep}" -eq 1 ] && echo "${line}"
      done <"${file}"
    fi
    for key in "$@"; do echo "${key}"; done
  } >"${tmp}" && mv -f "${tmp}" "${file}"
}

# queued|running|done|failed|interrupted — a task whose runner died (container
# restart, OOM kill, the exec never starting) reports interrupted, not running.
effective_state() {
  local id=$1 state pid start created
  state=$(state_get "${id}" state)
  case "${state}" in
    queued | running)
      pid=$(state_get "${id}" pid)
      if [ -z "${pid}" ]; then
        created=$(state_get "${id}" created)
        # Never picked up by a runner within a minute: the exec didn't start.
        if [ -n "${created}" ] && [ $(( $(now) - created )) -gt 60 ]; then
          echo interrupted
          return
        fi
      else
        start=$(state_get "${id}" pid_start)
        if [ "$(proc_start "${pid}")" != "${start}" ]; then
          echo interrupted
          return
        fi
      fi
      ;;
  esac
  echo "${state}"
}

is_active() {
  case "$(effective_state "$1")" in
    queued | running) return 0 ;;
  esac
  return 1
}

# Task ids sort by creation (they start with a zero-padded timestamp).
task_ids() {
  local d
  for d in "${TASKS_DIR}"/*/; do
    [ -f "${d}state" ] || continue
    d=${d%/}
    d=${d##*/}
    valid_id "${d}" && echo "${d}"
  done
}

prune() {
  local ids=() id n
  while IFS= read -r id; do ids+=("${id}"); done < <(task_ids)
  n=${#ids[@]}
  for id in "${ids[@]}"; do
    [ "${n}" -le "${MAX_KEPT}" ] && break
    if ! is_active "${id}"; then
      rm -rf -- "${TASKS_DIR:?}/${id}"
      n=$((n - 1))
    fi
  done
}

# Create a queued task. Exit 3 when too many are already active.
create_task() {
  local id=$1 op=$2 app active=0 t
  shift 2
  valid_id "${id}" || { echo "invalid task id" >&2; exit 2; }
  case "${op}" in
    install | update | remove) ;;
    *) echo "invalid op" >&2; exit 2 ;;
  esac
  [ "$#" -gt 0 ] || { echo "no apps" >&2; exit 2; }
  for app in "$@"; do
    valid_app "${app}" || { echo "invalid app name" >&2; exit 2; }
  done
  ensure_tasks_dir
  while IFS= read -r t; do
    is_active "${t}" && active=$((active + 1))
  done < <(task_ids)
  if [ "${active}" -ge "${MAX_ACTIVE}" ]; then
    echo "too many active tasks" >&2
    exit 3
  fi
  mkdir -m 700 "${TASKS_DIR}/${id}" || exit 1
  : >"${TASKS_DIR}/${id}/log"
  state_set "${id}" "op=${op}" "apps=$*" "state=queued" "created=$(now)" "done=0"
  prune
}

run_task() {
  local id=$1 op apps app pid rc=0 failed="" n=0 timeout_cmd=()
  valid_id "${id}" || exit 2
  ensure_tasks_dir
  [ -f "${TASKS_DIR}/${id}/state" ] || exit 1
  [ "$(state_get "${id}" state)" = queued ] || exit 1
  [ -z "$(state_get "${id}" pid)" ] || exit 1
  op=$(state_get "${id}" op)
  apps=$(state_get "${id}" apps)

  pid=$BASHPID
  state_set "${id}" "pid=${pid}" "pid_start=$(proc_start "${pid}")"
  exec >>"${TASKS_DIR}/${id}/log" 2>&1 </dev/null

  # One task at a time per container: proot-apps isn't safe to run twice over
  # the same app tree. flock is released by the kernel if we die.
  if command -v flock >/dev/null 2>&1; then
    exec 9>"${TASKS_DIR}/.lock"
    if ! flock -n 9; then
      echo "[cove] waiting for another proot-apps task to finish…"
      flock 9
    fi
  fi
  state_set "${id}" "state=running" "started=$(now)"

  if ! command -v proot-apps >/dev/null 2>&1; then
    echo "[cove] proot-apps is not available in this image"
    state_set "${id}" "state=failed" "exit=127" "finished=$(now)" "failed=${apps}"
    return
  fi
  command -v timeout >/dev/null 2>&1 && timeout_cmd=(timeout -k 30 "${APP_TIMEOUT}")

  for app in ${apps}; do
    valid_app "${app}" || continue
    state_set "${id}" "current=${app}"
    case "${op}" in
      install)
        if [ -d "${APPS_DIR}/${PREFIX}${app}" ]; then
          echo "[cove] ${app} is already installed, skipping"
          rc=0
        else
          echo "[cove] proot-apps install ${app}"
          ${timeout_cmd[@]+"${timeout_cmd[@]}"} proot-apps install "${app}"
          rc=$?
        fi
        ;;
      update)
        echo "[cove] proot-apps update ${app}"
        ${timeout_cmd[@]+"${timeout_cmd[@]}"} proot-apps update "${app}"
        rc=$?
        # proot-apps deletes the old tree before downloading the new one, so a
        # failed update leaves the app uninstalled.
        if [ "${rc}" -ne 0 ] && [ ! -d "${APPS_DIR}/${PREFIX}${app}" ]; then
          echo "[cove] ${app} was removed by the failed update; install it again"
        fi
        ;;
      remove)
        if [ ! -d "${APPS_DIR}/${PREFIX}${app}" ]; then
          echo "[cove] ${app} is not installed, nothing to remove"
          rc=0
        else
          echo "[cove] proot-apps remove ${app}"
          ${timeout_cmd[@]+"${timeout_cmd[@]}"} proot-apps remove "${app}"
          rc=$?
        fi
        ;;
    esac
    if [ "${rc}" -ne 0 ]; then
      [ "${rc}" -eq 124 ] && echo "[cove] ${app}: timed out after ${APP_TIMEOUT}s"
      echo "[cove] ${app}: failed (exit ${rc})"
      failed="${failed:+${failed} }${app}"
    fi
    n=$((n + 1))
    state_set "${id}" "done=${n}"
  done

  echo "[cove] proot-apps ${op}: finished"
  if [ -n "${failed}" ]; then
    state_set "${id}" "state=failed" "exit=1" "finished=$(now)" "current=" "failed=${failed}"
  else
    state_set "${id}" "state=done" "exit=0" "finished=$(now)" "current="
  fi
}

cmd=$1
shift
case "${cmd}" in
  boot)
    # Anything that goes wrong before the task log exists lands here instead.
    exec >>"${BOOT_LOG}" 2>&1
    # Queue only what's missing, so a reboot with everything installed records
    # no task at all.
    missing=()
    for app in "$@"; do
      if ! valid_app "${app}"; then
        echo "[cove] proot-apps: skipping invalid app name '${app}'"
        continue
      fi
      [ -d "${APPS_DIR}/${PREFIX}${app}" ] || missing+=("${app}")
    done
    [ "${#missing[@]}" -eq 0 ] && exit 0
    id="$(printf '%012d' "$(now)")-boot"
    ( create_task "${id}" install "${missing[@]}" ) || exit 1
    run_task "${id}"
    # Keep the long-lived log in the home for debugging after the container is gone.
    cat "${TASKS_DIR}/${id}/log" >>"${BOOT_LOG}" 2>/dev/null
    ;;
  list)
    echo "ARCH	$(uname -m)"
    if command -v proot-apps >/dev/null 2>&1; then echo "PROOT	1"; else echo "PROOT	0"; fi
    for d in "${APPS_DIR}"/*/; do
      [ -d "${d}" ] || continue
      d=${d%/}
      name=${d##*/}
      sha=""
      if [ -f "${d}/SHALAYER" ]; then
        read -r sha <"${d}/SHALAYER" 2>/dev/null
      fi
      downloading=0
      [ -e "${d}/DOWNLOADING" ] && downloading=1
      echo "APP	${name}	${sha}	${downloading}"
    done
    ;;
  start)
    [ "$#" -ge 3 ] || exit 2
    create_task "$@"
    ;;
  run)
    [ "$#" -eq 1 ] || exit 2
    run_task "$1"
    ;;
  tasks)
    [ -d "${TASKS_DIR}" ] || exit 0
    ensure_tasks_dir
    while IFS= read -r id; do
      printf 'TASK\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${id}" "$(state_get "${id}" op)" "$(effective_state "${id}")" \
        "$(state_get "${id}" exit)" "$(state_get "${id}" created)" \
        "$(state_get "${id}" started)" "$(state_get "${id}" finished)" \
        "$(state_get "${id}" done)" "$(state_get "${id}" current)" \
        "$(state_get "${id}" apps)" "$(state_get "${id}" failed)"
    done < <(task_ids)
    ;;
  log)
    [ "$#" -eq 1 ] && valid_id "$1" || exit 2
    ensure_tasks_dir
    [ -f "${TASKS_DIR}/$1/log" ] || exit 4
    tail -c 65536 "${TASKS_DIR}/$1/log"
    ;;
  clear)
    [ -d "${TASKS_DIR}" ] || exit 0
    ensure_tasks_dir
    while IFS= read -r id; do
      is_active "${id}" || rm -rf -- "${TASKS_DIR:?}/${id}"
    done < <(task_ids)
    ;;
  *)
    exit 2
    ;;
esac
