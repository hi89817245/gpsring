#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  cat >&2 <<'USAGE'
Usage: scripts/publish_firmware.sh /path/to/firmware.bin [version]

Copies a GPSRing firmware .bin to /share/esp32 and refreshes static index/manifest files.
Download URLs after start88:
  LAN:    http://192.168.120.218:8802/firmware/<file>.bin
  Public: https://gps.xdove.win/firmware/<file>.bin
USAGE
  exit 2
fi

src="$1"
version="${2:-}"
if [[ ! -f "$src" ]]; then
  echo "ERROR: firmware file not found: $src" >&2
  exit 1
fi
if [[ "${src##*.}" != "bin" ]]; then
  echo "ERROR: expected a .bin firmware file: $src" >&2
  exit 1
fi

dest_dir="${GPSRING_FIRMWARE_DIR:-/share/esp32}"
mkdir -p "$dest_dir"
chmod 777 "$dest_dir" 2>/dev/null || true

base="$(basename "$src")"
dest="$dest_dir/$base"
cp -f "$src" "$dest"
chmod 644 "$dest"
sha256="$(sha256sum "$dest" | awk '{print $1}')"
size="$(stat -c '%s' "$dest")"
ts="$(date -Iseconds)"

python3 - "$dest_dir" "$base" "$version" "$sha256" "$size" "$ts" <<'PY'
import html, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
latest = sys.argv[2]
version = sys.argv[3]
sha256 = sys.argv[4]
size = int(sys.argv[5])
ts = sys.argv[6]
files = sorted(root.glob('*.bin'), key=lambda p: p.stat().st_mtime, reverse=True)
items = []
for p in files:
    items.append({
        'file': p.name,
        'size': p.stat().st_size,
        'url_lan': f'http://192.168.120.218:8802/firmware/{p.name}',
        'url_public': f'https://gps.xdove.win/firmware/{p.name}',
        'sha256': sha256 if p.name == latest else None,
    })
manifest = {
    'project': 'GPSRing',
    'version': version or None,
    'latest': latest,
    'published_at': ts,
    'latest_sha256': sha256,
    'latest_size': size,
    'latest_lan_url': f'http://192.168.120.218:8802/firmware/{latest}',
    'latest_public_url': f'https://gps.xdove.win/firmware/{latest}',
    'files': items,
}
(root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
links = '\n'.join(
    f"<li><a href='{html.escape(p.name)}'>{html.escape(p.name)}</a> <small>{p.stat().st_size} bytes</small></li>"
    for p in files
)
(root / 'index.html').write_text(f"""<!doctype html>
<meta charset='utf-8'>
<title>GPSRing Firmware</title>
<style>body{{font-family:system-ui,sans-serif;background:#071b1a;color:#e7fff8;padding:24px}}a{{color:#7dd3fc}}code{{background:#123;padding:2px 6px;border-radius:4px}}</style>
<h1>GPSRing Firmware</h1>
<p><b>Latest:</b> <a href='{html.escape(latest)}'>{html.escape(latest)}</a></p>
<p><b>SHA256:</b> <code>{html.escape(sha256)}</code></p>
<p><b>Published:</b> {html.escape(ts)}</p>
<p><a href='manifest.json'>manifest.json</a></p>
<ul>{links}</ul>
""", encoding='utf-8')
PY

cat <<EOF
Published GPSRing firmware:
  file: $dest
  sha256: $sha256
  LAN: https? no; use http://192.168.120.218:8802/firmware/$base
  Public: https://gps.xdove.win/firmware/$base
  Manifest: https://gps.xdove.win/firmware/manifest.json
EOF
