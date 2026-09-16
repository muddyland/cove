#!/bin/bash
# Cove's in-workspace app driver: LinuxServer proot-apps
# (https://github.com/linuxserver/proot-apps) and AppImages.
#
# Two entry points:
#
#  * Boot: mounted twice under /custom-cont-init.d and run as root with no
#    arguments; which list it installs comes from the name it was invoked as —
#    98-install-proot-apps.sh installs $PROOT_APPS, 97-install-appimages.sh
#    installs $COVE_APPIMAGES. Anything already installed is skipped.
#
#  * Control plane: the backend runs this file's text through
#    `docker exec -u abc … bash -c "<script>" cove-apps <command> [args]`
#    (so it works in containers launched before the script was mounted):
#      list                          installed proot-apps + their layer digests
#      appimages                     installed AppImages + where they came from
#      start <id> <kind> <op> <arg>… record a queued task
#      run <id>                      execute a recorded task (a detached exec)
#      tasks                         state of every recorded task
#      log <id>                      tail of one task's output
#      clear                         forget finished tasks
#
#    kind is proot or appimage. proot ops take app names; appimage install takes
#    URLs, update takes <slug> <url> (the new download replaces the app), and
#    remove takes slugs.
#
# Everything that touches files runs as the desktop user, never root: /config
# and the task directory are writable by that user, so a root write there could
# be redirected through a planted symlink. Output is line-oriented and
# tab-separated; the backend treats it as untrusted and re-validates every field.
set -u

APP_RE='^[a-z0-9][a-z0-9._-]{0,63}$'
SLUG_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
# A URL reaches curl as one argv token and is stored in a line-oriented metadata
# file. Allow only the characters URLs are actually made of (query strings
# included) — no whitespace, quotes, backslashes or shell metacharacters, none of
# which belong in one — and require a host after the scheme.
URL_RE='^https?://[A-Za-z0-9._~%-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%!*+,:@/?&=#()-]*)?$'
ID_RE='^[A-Za-z0-9-]{1,64}$'
# Registry pulls in proot-apps have no timeout, so a stalled ghcr.io blob would
# otherwise hang a task (and the lock every later task waits on) forever.
APP_TIMEOUT=900
# Active (queued/running) tasks allowed at once, and finished ones kept.
MAX_ACTIVE=3
MAX_KEPT=20

# ── Boot (root) ────────────────────────────────────────────────────────────────
if [ "$#" -eq 0 ]; then
  case "$0" in
    *appimage*) boot_kind=appimage; apps="${COVE_APPIMAGES:-}"; log=/config/.cove-appimages.log ;;
    *)          boot_kind=proot;    apps="${PROOT_APPS:-}";     log=/config/.cove-proot-apps.log ;;
  esac
  apps="${apps//,/ }"
  [ -z "${apps// /}" ] && exit 0
  # Background it: custom-cont-init.d runs *before* the desktop services start,
  # so a long app list here would block the GUI from ever becoming ready and the
  # workspace would overrun the launch deadline. Apps appear as each finishes.
  # Older releases appended the boot log as root, which the desktop user can't
  # append to now. Move it aside rather than chown it: rename(2) acts on the
  # directory entry itself, so a planted symlink or hard link can't redirect it.
  if [ -e "${log}" ] && [ "$(stat -c %u "${log}" 2>/dev/null)" = 0 ]; then
    mv -f -- "${log}" "${log}.old" 2>/dev/null
  fi
  # shellcheck disable=SC2086
  HOME=/config s6-setuidgid abc /bin/bash "$0" "boot-${boot_kind}" $apps </dev/null >/dev/null 2>&1 &
  echo "[cove] ${boot_kind}: installing in background (${apps}); see the tasks menu"
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
# AppImages are extracted per slug, with their provenance beside them.
AI_DIR="${HOME}/.cove-appimages"
AI_META="${AI_DIR}/.cove-meta"
DESKTOP_DIR="${HOME}/.local/share/applications"
# Per-container (not /config): a task can't outlive its container, and a shared
# profile mounts one /config into several workspaces whose tasks must not mix.
TASKS_DIR="/tmp/cove-apps-$(id -u)"
BOOT_LOG="${HOME}/.cove-proot-apps.log"
AI_BOOT_LOG="${HOME}/.cove-appimages.log"

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
valid_slug() { [[ "$1" =~ $SLUG_RE ]]; }
valid_url() { [[ "$1" =~ $URL_RE ]]; }
now() { date +%s; }

# Tabs and newlines would forge extra fields/rows in the listings the backend
# parses, so no value taken from a download ever reaches them raw.
clean() { printf '%s' "${1//[$'\t\n\r']/ }"; }

# An AppImage's install directory name, derived from its URL exactly as the
# original installer did, so apps installed by older releases keep their slug.
slug_for() {
  local file name slug
  file="$(basename "${1%%\?*}")"
  name="${file%.*}"
  slug="$(printf '%s' "${name}" | tr -c 'A-Za-z0-9._-' '_')"
  slug="${slug:0:64}"
  [ -z "${slug}" ] && slug="appimage"
  printf '%s' "${slug}"
}

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

# ── AppImages ─────────────────────────────────────────────────────────────────

ai_meta_get() {  # <slug> <key>
  local line
  while IFS= read -r line; do
    if [ "${line%%=*}" = "$2" ]; then
      printf '%s' "${line#*=}"
      return
    fi
  done <"${AI_META}/$1" 2>/dev/null
}

# The display name an installed AppImage advertises, for apps installed before
# Cove recorded any metadata.
ai_name_from_desktop() {  # <slug>
  local got
  got=$(grep -m1 '^Name=' "${DESKTOP_DIR}/cove-$1.desktop" 2>/dev/null | cut -d= -f2-)
  printf '%s' "${got}"
}

# Download <url> and install it as <slug>, replacing whatever is there now. The
# new copy is only swapped in once it has extracted with a runnable AppRun, so a
# failed download or a truncated file leaves the working app untouched.
ai_install() {  # <slug> <url>
  local slug=$1 url=$2 dest="${AI_DIR}/$1" tmp file appimage disp icon src_desktop out size
  tmp="${AI_DIR}/.work-${slug}.$$"
  rm -rf -- "${tmp}"
  mkdir -p "${AI_DIR}" "${DESKTOP_DIR}" "${AI_META}" "${tmp}" || return 1
  appimage="${tmp}/download"

  echo "[cove] appimage: downloading ${url}"
  if ! ${timeout_cmd[@]+"${timeout_cmd[@]}"} curl -fsSL --retry 2 -o "${appimage}" -- "${url}"; then
    echo "[cove] appimage: download failed"
    rm -rf -- "${tmp}"
    return 1
  fi
  chmod +x "${appimage}"

  echo "[cove] appimage: extracting"
  # --appimage-extract needs no FUSE (these containers have no /dev/fuse) and
  # drops squashfs-root into the CWD. Some runtimes exit non-zero even after a
  # complete extraction, so judge by whether a runnable AppRun landed.
  ( cd "${tmp}" && "${appimage}" --appimage-extract >/dev/null 2>&1 )
  if [ ! -x "${tmp}/squashfs-root/AppRun" ]; then
    echo "[cove] appimage: this file did not extract — is the URL really an AppImage?"
    rm -rf -- "${tmp}"
    return 1
  fi
  size=$(du -sk "${tmp}/squashfs-root" 2>/dev/null | cut -f1)
  rm -f -- "${appimage}"

  # Swap: the old copy only goes once the new one is ready to take its place.
  rm -rf -- "${dest}.old"
  [ -d "${dest}" ] && mv -f -- "${dest}" "${dest}.old"
  if ! mv -f -- "${tmp}/squashfs-root" "${dest}"; then
    echo "[cove] appimage: could not install the extracted app"
    [ -d "${dest}.old" ] && mv -f -- "${dest}.old" "${dest}"
    rm -rf -- "${tmp}"
    return 1
  fi
  rm -rf -- "${dest}.old" "${tmp}"

  # Prefer the app's own .desktop (it carries a friendly Name) and icon.
  disp="${slug}"
  src_desktop="$(find "${dest}" -maxdepth 1 -name '*.desktop' 2>/dev/null | head -1)"
  if [ -n "${src_desktop}" ]; then
    got="$(grep -m1 '^Name=' "${src_desktop}" | cut -d= -f2-)"
    [ -n "${got}" ] && disp="${got}"
  fi
  icon="$(find "${dest}" -maxdepth 3 \( -name '*.png' -o -name '*.svg' \) 2>/dev/null | head -1)"

  out="${DESKTOP_DIR}/cove-${slug}.desktop"
  # Set APPDIR explicitly. An extracted AppImage finds its AppDir by walking up
  # from $0 looking for a dir containing $1 — but $1 here is "--no-sandbox",
  # which matches nothing, so detection collapses to "" and the app binary path
  # becomes "/<bin>" (works from a CLI with no args, fails from the menu).
  cat >"${out}" <<EOF
[Desktop Entry]
Type=Application
Name=$(clean "${disp}")
Exec=env APPDIR=${dest} ${dest}/AppRun --no-sandbox %U
Icon=${icon}
Terminal=false
Categories=AudioVideo;Network;Utility;
EOF
  chmod +x "${out}"

  {
    echo "url=$(clean "${url}")"
    echo "name=$(clean "${disp}")"
    echo "installed=$(now)"
    echo "size_kb=${size:-0}"
  } >"${AI_META}/${slug}.tmp.$$" && mv -f "${AI_META}/${slug}.tmp.$$" "${AI_META}/${slug}"
  echo "[cove] appimage: installed $(clean "${disp}")"
  return 0
}

ai_remove() {  # <slug>
  local slug=$1
  if [ ! -d "${AI_DIR}/${slug}" ] && [ ! -f "${AI_META}/${slug}" ]; then
    echo "[cove] appimage: ${slug} is not installed, nothing to remove"
    return 0
  fi
  rm -rf -- "${AI_DIR:?}/${slug}" "${AI_DIR:?}/${slug}.old"
  rm -f -- "${DESKTOP_DIR}/cove-${slug}.desktop" "${AI_META}/${slug}"
  echo "[cove] appimage: removed ${slug}"
  return 0
}

# Create a queued task. Exit 3 when too many are already active.
create_task() {
  local id=$1 kind=$2 op=$3 app active=0 t names="" urls="" slug
  shift 3
  valid_id "${id}" || { echo "invalid task id" >&2; exit 2; }
  case "${kind}" in proot | appimage) ;; *) echo "invalid kind" >&2; exit 2 ;; esac
  case "${op}" in install | update | remove) ;; *) echo "invalid op" >&2; exit 2 ;; esac
  [ "$#" -gt 0 ] || { echo "no apps" >&2; exit 2; }

  if [ "${kind}" = proot ]; then
    for app in "$@"; do
      valid_app "${app}" || { echo "invalid app name" >&2; exit 2; }
    done
    names="$*"
  elif [ "${op}" = remove ]; then
    for app in "$@"; do
      valid_slug "${app}" || { echo "invalid slug" >&2; exit 2; }
    done
    names="$*"
  elif [ "${op}" = update ]; then
    # <slug> <url>: the download replaces that app in place.
    [ "$#" -eq 2 ] || { echo "update takes a slug and a url" >&2; exit 2; }
    valid_slug "$1" || { echo "invalid slug" >&2; exit 2; }
    valid_url "$2" || { echo "invalid url" >&2; exit 2; }
    names="$1"
    urls="$1	$2"
  else
    for app in "$@"; do
      valid_url "${app}" || { echo "invalid url" >&2; exit 2; }
      slug="$(slug_for "${app}")"
      names="${names:+${names} }${slug}"
      urls="${urls:+${urls}
}${slug}	${app}"
    done
  fi

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
  # URLs live beside the task, not in its state: the listings are tab-separated
  # and a URL has no business in the app-name column.
  [ -n "${urls}" ] && printf '%s\n' "${urls}" >"${TASKS_DIR}/${id}/urls"
  state_set "${id}" "kind=${kind}" "op=${op}" "apps=${names}" "state=queued" "created=$(now)" "done=0"
  prune
}

run_task() {
  local id=$1 kind op apps app url pid rc=0 failed="" n=0 timeout_cmd=()
  valid_id "${id}" || exit 2
  ensure_tasks_dir
  [ -f "${TASKS_DIR}/${id}/state" ] || exit 1
  [ "$(state_get "${id}" state)" = queued ] || exit 1
  [ -z "$(state_get "${id}" pid)" ] || exit 1
  kind=$(state_get "${id}" kind)
  [ -n "${kind}" ] || kind=proot
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
      echo "[cove] waiting for another app task to finish…"
      flock 9
    fi
  fi
  state_set "${id}" "state=running" "started=$(now)"

  if [ "${kind}" = proot ] && ! command -v proot-apps >/dev/null 2>&1; then
    echo "[cove] proot-apps is not available in this image"
    state_set "${id}" "state=failed" "exit=127" "finished=$(now)" "failed=${apps}"
    return
  fi
  if [ "${kind}" = appimage ] && ! command -v curl >/dev/null 2>&1; then
    echo "[cove] appimage: curl is not available in this image"
    state_set "${id}" "state=failed" "exit=127" "finished=$(now)" "failed=${apps}"
    return
  fi
  command -v timeout >/dev/null 2>&1 && timeout_cmd=(timeout -k 30 "${APP_TIMEOUT}")

  for app in ${apps}; do
    if [ "${kind}" = appimage ]; then
      valid_slug "${app}" || continue
      state_set "${id}" "current=${app}"
      case "${op}" in
        remove)
          ai_remove "${app}"
          rc=$?
          ;;
        install | update)
          url=$(awk -v s="${app}" -F'\t' '$1 == s {print $2; exit}' "${TASKS_DIR}/${id}/urls" 2>/dev/null)
          if [ -z "${url}" ] || ! valid_url "${url}"; then
            echo "[cove] appimage: no usable URL recorded for ${app}"
            rc=1
          elif [ "${op}" = install ] && [ -x "${AI_DIR}/${app}/AppRun" ]; then
            echo "[cove] appimage: ${app} is already installed, skipping"
            rc=0
          else
            ai_install "${app}" "${url}"
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
      continue
    fi
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

  echo "[cove] ${kind} ${op}: finished"
  if [ -n "${failed}" ]; then
    state_set "${id}" "state=failed" "exit=1" "finished=$(now)" "current=" "failed=${failed}"
  else
    state_set "${id}" "state=done" "exit=0" "finished=$(now)" "current="
  fi
}

cmd=$1
shift
case "${cmd}" in
  boot-proot | boot-appimage)
    kind=proot; boot_log="${BOOT_LOG}"
    [ "${cmd}" = boot-appimage ] && { kind=appimage; boot_log="${AI_BOOT_LOG}"; }
    # Anything that goes wrong before the task log exists lands here instead.
    exec >>"${boot_log}" 2>&1
    # Queue only what's missing, so a reboot with everything installed records
    # no task at all.
    missing=()
    for app in "$@"; do
      if [ "${kind}" = appimage ]; then
        if ! valid_url "${app}"; then
          echo "[cove] appimage: skipping unusable URL '${app}'"
          continue
        fi
        [ -x "${AI_DIR}/$(slug_for "${app}")/AppRun" ] || missing+=("${app}")
      else
        if ! valid_app "${app}"; then
          echo "[cove] proot-apps: skipping invalid app name '${app}'"
          continue
        fi
        [ -d "${APPS_DIR}/${PREFIX}${app}" ] || missing+=("${app}")
      fi
    done
    [ "${#missing[@]}" -eq 0 ] && exit 0
    id="$(printf '%012d' "$(now)")-boot-${kind}"
    ( create_task "${id}" "${kind}" install "${missing[@]}" ) || exit 1
    run_task "${id}"
    # Keep the long-lived log in the home for debugging after the container is gone.
    cat "${TASKS_DIR}/${id}/log" >>"${boot_log}" 2>/dev/null
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
  appimages)
    # Installed AppImages, including ones from before Cove recorded provenance
    # (their url is empty, so updating one asks for a URL).
    for d in "${AI_DIR}"/*/; do
      [ -d "${d}" ] || continue
      d=${d%/}
      slug=${d##*/}
      case "${slug}" in *.old) continue ;; esac
      valid_slug "${slug}" || continue
      [ -x "${d}/AppRun" ] || continue
      name=$(ai_meta_get "${slug}" name)
      [ -n "${name}" ] || name=$(ai_name_from_desktop "${slug}")
      [ -n "${name}" ] || name="${slug}"
      printf 'APPIMAGE\t%s\t%s\t%s\t%s\t%s\n' \
        "${slug}" "$(clean "${name}")" "$(clean "$(ai_meta_get "${slug}" url)")" \
        "$(ai_meta_get "${slug}" size_kb)" "$(ai_meta_get "${slug}" installed)"
    done
    ;;
  start)
    [ "$#" -ge 4 ] || exit 2
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
      printf 'TASK\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${id}" "$(state_get "${id}" op)" "$(effective_state "${id}")" \
        "$(state_get "${id}" exit)" "$(state_get "${id}" created)" \
        "$(state_get "${id}" started)" "$(state_get "${id}" finished)" \
        "$(state_get "${id}" done)" "$(state_get "${id}" current)" \
        "$(state_get "${id}" apps)" "$(state_get "${id}" failed)" \
        "$(state_get "${id}" kind)"
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
