#!/usr/bin/env bash
# Instala un build de remux publicado por .github/workflows/build-remux.yml en este repo.
# Uso: scripts/install-remux.sh <tag remux-main-XXXXXXXXXXXX> [destino]   (destino por defecto: var/tools/<tag>)
# Descarga el tar.gz de la release, verifica SHA256SUMS y deja bin/{remux,ubv-info,ubv-anonymise}.
set -euo pipefail
TAG=${1:?tag de la release (p. ej. remux-main-d09c3e942bef)}
DEST=${2:-var/tools/$TAG}
REPO=jtelo88/Coldreel
ASSET="$TAG-linux-x86_64.tar.gz"
URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
echo "descargando $URL" >&2
curl -fsSL --retry 3 -o "$tmp/$ASSET" "$URL"
mkdir -p "$tmp/x" && tar xzf "$tmp/$ASSET" -C "$tmp/x"
(cd "$tmp/x" && sha256sum -c SHA256SUMS >&2)
mkdir -p "$DEST" && cp -a "$tmp/x/." "$DEST/" && chmod 755 "$DEST"/bin/*
sha256sum "$tmp/$ASSET" | sed "s|$tmp/||" > "$DEST/$ASSET.sha256"
echo "instalado en $DEST:" >&2; cat "$DEST/BUILD-INFO.txt" >&2
"$DEST/bin/remux" --version >&2
