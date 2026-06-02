#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/configuration"
zip -r --update ../configuration.zip UDM_BUNDLE.json policies/
echo "Written: $(dirname "$0")/configuration.zip"
