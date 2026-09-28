#!/bin/bash
# One-time, NO-ROOT setup for running the watcher on WSL/Linux.
# Installs: python venv + patchright + Chromium, and a private Xvfb (virtual display).
set -euo pipefail
BASE="${WATCHER_HOME:-$HOME/.flight-watcher}"
mkdir -p "$BASE/debs" "$BASE/root"
python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" -q install -r "$(dirname "$0")/../requirements.txt"
"$BASE/venv/bin/patchright" install chromium

cd "$BASE/debs"
apt-get download libxfont2 libfontenc1 xkb-data x11-xkb-utils libunwind8 libxkbfile1 2>/dev/null || true
# xvfb itself: the local apt index is often stale, so read the current filename from the pool
for pkg in "universe xvfb" "main xserver-common"; do
  set -- $pkg
  f=$(curl -s "http://security.ubuntu.com/ubuntu/pool/$1/x/xorg-server/" \
      | grep -oE "href=\"$2_[^\"]*(amd64|all)\.deb\"" | grep -v v3 | sort -V | tail -1 | cut -d'"' -f2)
  curl -sLO "http://security.ubuntu.com/ubuntu/pool/$1/x/xorg-server/$f"
done
for d in *.deb; do dpkg -x "$d" "$BASE/root"; done

cat > "$BASE/xvfb-run.sh" <<EOS
#!/bin/bash
# Runs a command under a private Xvfb. Xvfb sits inside bwrap so /usr/bin also holds xkbcomp.
R=$BASE/root
D=\${XVFB_DISPLAY:-:97}
ARGS=(--ro-bind / / --dev /dev --proc /proc --bind /tmp /tmp --tmpfs /usr/bin)
for f in /usr/bin/*; do ARGS+=(--ro-bind "\$f" "\$f"); done
ARGS+=(--ro-bind \$R/usr/bin/xkbcomp /usr/bin/xkbcomp --setenv LD_LIBRARY_PATH \$R/usr/lib/x86_64-linux-gnu)
bwrap "\${ARGS[@]}" \$R/usr/bin/Xvfb \$D -screen 0 1400x2200x24 -nolisten tcp -xkbdir \$R/usr/share/X11/xkb >/tmp/xvfb.log 2>&1 &
XP=\$!
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S /tmp/.X11-unix/X\${D#:} ] && break; sleep 0.5; done
export DISPLAY=\$D
"\$@"; RC=\$?
kill \$XP 2>/dev/null; exit \$RC
EOS
chmod +x "$BASE/xvfb-run.sh"
echo "Done. Run:  scripts/run-local.sh"
