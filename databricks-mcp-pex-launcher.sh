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
    uv pip install -r requirements.txt
    
    echo "Setup complete." >&2
}

# Function to run the launcher on subsequent runs
run_existing() {
    echo "Awesome Databricks MCP found. Checking for updates..." >&2
    cd "${INSTALL_DIR}"
    
    # Fetch the latest version without forcing a merge
    git fetch
    # Get the hash of the local and remote heads
    LOCAL=$(git rev-parse @)
    REMOTE=$(git rev-parse @{u})

    if [ "$LOCAL" != "$REMOTE" ]; then
        echo "New version available. Updating..." >&2
        git pull
        ensure_uv
        # Sync dependencies in case they've changed
        uv pip sync requirements.txt --python "${INSTALL_DIR}/.venv/bin/python"
    fi

    echo "Starting MCP server..." >&2
    # Use exec to replace the bash script with the python process
    exec "${INSTALL_DIR}/.venv/bin/python" -m scripts.mcp_launcher "${ARGS[@]}"
}


# --- Main Logic ---

if [ ! -d "${INSTALL_DIR}" ]; then
    first_time_setup
fi

run_existing
