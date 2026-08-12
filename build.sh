#!/bin/sh
# Build the portable single-file artifact: dist/sonos-doctor.pyz
# Runs anywhere with Python >= 3.8 — no installs:  python3 sonos-doctor.pyz snapshot
set -eu
cd "$(dirname "$0")"
rm -rf .build dist
mkdir -p .build dist
cp -R sonosdoctor .build/
find .build -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
cat > .build/__main__.py <<'EOF'
import sys
from sonosdoctor.__main__ import main
sys.exit(main())
EOF
python3 -m zipapp .build -o dist/sonos-doctor.pyz -p "/usr/bin/env python3" -c
rm -rf .build
ls -la dist/
