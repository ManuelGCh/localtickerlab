#!/bin/zsh
# Doble clic para abrir el dashboard. Ciérralo cuando termines.
cd "$(dirname "$0")"

for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    exec "$candidate" run.py
  fi
done

echo "No se encontró Python 3.10 o superior."
echo "Instálalo con: brew install python"
read -r "?Pulsa Enter para cerrar..."
