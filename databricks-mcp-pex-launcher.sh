#!/bin/bash
# A plug-and-play launcher for the Awesome Databricks MCP.
# It ensures dependencies are cached in a local environment for fast startups.

set -e

# All arguments passed to this script will be forwarded to the mcp_launcher.
ARGS=("$@")

INSTALL_DIR="${HOME}/.awesome-databricks-mcp"
REPO_URL="https://github.com/aaronsewall/awesome-databricks-mcp.git"

# --- Helper Functions ---

# Function to ensure uv is installed
ensure_uv() {
    if ! command -v uv &> /dev/null; then
        echo "uv not found. Performing one-time installation..." >&2
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # The uv installer requires sourcing this file to update the PATH
        source "${HOME}/.cargo/env"
        echo "uv installed." >&2
    fi
}

# Function for the first-time installation
first_time_setup() {
    echo "Performing first-time setup for Awesome Databricks MCP..." >&2
    echo "This may take a minute..." >&2
    
    ensure_uv

    git clone --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
    cd "${INSTALL_DIR}"
    
    echo "Creating virtual environment..." >&2
    uv venv
    
    echo "Installing dependencies..." >&2
    # Debug and export the env var to make it available for child processes like pip
    echo "DEBUG: WHENEVER_NO_BUILD_RUST_EXT is set to: '${WHENEVER_NO_BUILD_RUST_EXT}'" >&2
    export WHENEVER_NO_BUILD_RUST_EXT
    uv sync
    
    echo "Setup complete." >&2
}


# --- Main Logic ---

if [ ! -d "${INSTALL_DIR}" ]; then
    first_time_setup
fi

# This part runs on every launch (including after first_time_setup)
cd "${INSTALL_DIR}"
echo "Awesome Databricks MCP found. Checking for updates..." >&2

# Ensure uv is available (it might be a new shell)
ensure_uv

# Fetch the latest version
git fetch
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse @{u})

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "New version available. Updating..." >&2
    git pull
    
    # Sync dependencies in case they've changed
    echo "DEBUG: WHENEVER_NO_BUILD_RUST_EXT is set to: '${WHENEVER_NO_BUILD_RUST_EXT}'" >&2
    export WHENEVER_NO_BUILD_RUST_EXT
    uv pip sync requirements.txt --python "${INSTALL_DIR}/.venv/bin/python"
fi

echo "Starting MCP server..." >&2
# Export the variable again to ensure it's set for the final exec
export WHENEVER_NO_BUILD_RUST_EXT
# Use exec to replace the bash script with the python process
exec "${INSTALL_DIR}/.venv/bin/python" -m scripts.mcp_launcher "${ARGS[@]}"