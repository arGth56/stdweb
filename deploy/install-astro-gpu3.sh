#!/usr/bin/env bash
# Astrometry pipeline binaries for STDWeb/STDPipe on Ubuntu (gpu-3).
# Run: sudo bash deploy/install-astro-gpu3.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  source-extractor \
  scamp \
  psfex \
  astrometry.net \
  astrometry-data-2mass \
  swarp \
  libcfitsio-dev \
  build-essential \
  git

echo ""
echo "Astrometry.net index download (astrometry-data-2mass) may take several minutes and ~GB disk."
index_count="$(find /usr/share/astrometry -name 'index-*.fits' 2>/dev/null | wc -l)"
echo "Index files in /usr/share/astrometry: ${index_count}"
if [[ "${index_count}" -lt 10 ]]; then
  echo "WARNING: very few indexes — blind matching (solve-field) will fail until indexes are present."
fi

install_hotpants_from_source() {
  if command -v hotpants >/dev/null && [[ "$(command -v hotpants)" == /usr/local/bin/hotpants ]]; then
    echo "hotpants already at /usr/local/bin/hotpants"
    return 0
  fi
  local build
  build="$(mktemp -d /tmp/hotpants-build.XXXXXX)"
  # Subshell so EXIT trap does not reference $build after this function returns (set -u).
  (
    trap 'rm -rf "$build"' EXIT
    git clone --depth 1 https://github.com/acbecker/hotpants.git "$build"
    cd "$build"
    sed -i 's|^CFITSIOINCDIR =.*|CFITSIOINCDIR = /usr/include|' Makefile
    sed -i 's|^LIBDIR        =.*|LIBDIR        = /usr/lib/x86_64-linux-gnu|' Makefile
    sed -i 's/-pedantic-errors -Wall/-pedantic-errors -Wall -fcommon/' Makefile
    make -j"$(nproc)"
    install -m 755 hotpants maskim extractkern /usr/local/bin/
  )
  echo "Built hotpants -> /usr/local/bin/hotpants"
}

install_hotpants_from_source

echo ""
echo "Installed binaries:"
for b in source-extractor scamp psfex solve-field swarp Swarp hotpants; do
  if command -v "$b" >/dev/null; then
    echo "  $b -> $(command -v "$b")"
  elif [[ "$b" == Swarp ]]; then
    :
  else
    echo "  $b -> NOT FOUND"
  fi
done
swarp_path="$(command -v swarp 2>/dev/null || command -v Swarp 2>/dev/null || true)"
if [[ -n "${swarp_path}" ]]; then
  echo "  (use STDPIPE_SWARP=${swarp_path})"
fi
echo ""
echo "Add to /var/www/stdweb/.env (adjust paths if 'which' differs):"
cat <<'EOF'
STDPIPE_SEXTRACTOR=/usr/bin/source-extractor
STDPIPE_SCAMP=/usr/bin/scamp
STDPIPE_PSFEX=/usr/bin/psfex
STDPIPE_SOLVE_FIELD=/usr/bin/solve-field
STDPIPE_SOLVE_FIELD_CONFIG=/etc/astrometry.cfg
STDPIPE_SWARP=/usr/bin/Swarp
STDPIPE_HOTPANTS=/usr/local/bin/hotpants
EOF
