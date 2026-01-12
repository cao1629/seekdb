#!/bin/bash
# Setup environment variables for OceanBase development
# Usage: source setup_env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Add all bin directories from deps to PATH
export PATH="${SCRIPT_DIR}/deps/3rd/usr/local/oceanbase/devtools/bin:$PATH"
export PATH="${SCRIPT_DIR}/deps/3rd/usr/local/oceanbase/deps/devel/bin:$PATH"
export PATH="${SCRIPT_DIR}/deps/3rd/usr/bin:$PATH"
export PATH="${SCRIPT_DIR}/deps/3rd/u01/obclient/bin:$PATH"

# Add library paths
export LD_LIBRARY_PATH="${SCRIPT_DIR}/deps/3rd/usr/local/oceanbase/devtools/lib:$LD_LIBRARY_PATH"
export LD_LIBRARY_PATH="${SCRIPT_DIR}/deps/3rd/usr/local/oceanbase/deps/devel/lib:$LD_LIBRARY_PATH"

echo "Environment setup complete."
echo "  cmake: $(which cmake 2>/dev/null || echo 'not found')"
echo "  gcc:   $(which gcc 2>/dev/null || echo 'not found')"
echo "  g++:   $(which g++ 2>/dev/null || echo 'not found')"
