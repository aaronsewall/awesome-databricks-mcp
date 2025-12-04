import subprocess
import os
import sys
import time
import signal
import socket
from typing import List

# --- Configuration ---
SERVER_HOST = "127.0.0.1"
# Using dynamic port allocation, so these are no longer constants.
# SERVER_PORT = 8000
# SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
# Using a unique log file per process to support concurrency.
LOG_FILE_PATH_TEMPLATE = "uvicorn_mcp_server_{pid}.log"
# This file will be created in the directory where uvx runs the script.

# Global variable to store the server process and its PID for signal handling
SERVER_PROCESS = None
LOG_FILE = None
LOG_FILE_PATH = None


def find_free_port():
    """Finds and returns an available TCP port on the host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((SERVER_HOST, 0))
        return s.getsockname()[1]


def cleanup_server():
    """Performs the robust process group cleanup."""
    global SERVER_PROCESS, LOG_FILE, LOG_FILE_PATH

    if LOG_FILE and not LOG_FILE.closed:
        LOG_FILE.close()
        # Optionally, you might want to remove the log file upon cleanup.
        # However, it can be useful for debugging, so we'll leave it.
        # if LOG_FILE_PATH and os.path.exists(LOG_FILE_PATH):
        #     os.remove(LOG_FILE_PATH)

    if SERVER_PROCESS and SERVER_PROCESS.poll() is None:
        print(
            f"\nSignal trapped. Attempting shutdown of FastAPI server (PID {SERVER_PROCESS.pid})...",
            file=sys.stderr,
        )

        try:
            pgid = os.getpgid(SERVER_PROCESS.pid)

            # 1. Send SIGTERM (Graceful Kill) to the entire process group
            os.killpg(pgid, signal.SIGTERM)

            # Wait briefly
            time.sleep(1)

            # 2. Check if the process is still alive and use SIGKILL if necessary
            if SERVER_PROCESS.poll() is None:
                print(
                    f"Graceful shutdown failed. Forcing kill on PGID {pgid}...",
                    file=sys.stderr,
                )
                os.killpg(pgid, signal.SIGKILL)
                SERVER_PROCESS.wait(timeout=1)
            else:
                print("Shutdown successful.", file=sys.stderr)

        except ProcessLookupError:
            pass  # Process already terminated
        except Exception as e:
            print(f"Error during server cleanup: {e}", file=sys.stderr)


def signal_handler(sig, frame):
    """Handler for SIGINT (Ctrl+C) and SIGTERM (Client kill)."""
    cleanup_server()
    # Re-raise the signal to allow the main script to exit cleanly
    sys.exit(0)


def launch_server_and_proxy(proxy_args: List[str] = None):
    """
    Starts the FastAPI server on a dynamic port, runs the proxy, and ensures server shutdown.
    It uses sys.executable to ensure both Uvicorn and the dba-mcp-proxy
    are run within the temporary, isolated environment created by uvx.
    """
    global SERVER_PROCESS, LOG_FILE, LOG_FILE_PATH
    proxy_exit_code = 1

    # --- Dynamic Port and Log File Allocation ---
    server_port = find_free_port()
    server_url = f"http://{SERVER_HOST}:{server_port}"
    LOG_FILE_PATH = LOG_FILE_PATH_TEMPLATE.format(pid=os.getpid())

    print(f"Starting FastAPI MCP server in background on {server_url}...")
    # 1. Register signal handlers immediately
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"Uvicorn logs redirected to: {LOG_FILE_PATH}")

    try:
        # 1. Start the Uvicorn/FastAPI server (Layer 1)
        # We use the Python executable from the uvx environment (sys.executable)
        # and run Uvicorn as a module (-m uvicorn) to guarantee it's available.
        LOG_FILE = open(LOG_FILE_PATH, "w")
        SERVER_PROCESS = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "server.app:app",
                "--host",
                SERVER_HOST,
                "--port",
                str(server_port),
            ],
            stdout=LOG_FILE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )

        time.sleep(2)  # Give the server a moment to start

        if SERVER_PROCESS.poll() is not None:
            # Try to read output to diagnose failure. Close the file first.
            LOG_FILE.close()
            with open(LOG_FILE_PATH, "r") as f:
                error_output = f.read()
            raise RuntimeError(
                f"Server failed to start. Exit code: {SERVER_PROCESS.returncode}. Output from {LOG_FILE_PATH}:\n{error_output}"
            )

        print(f"FastAPI server started with PID {SERVER_PROCESS.pid}.")

        # 2. Run the dba-mcp-proxy (Layer 2 - The blocking client call)
        print("Executing the dba-mcp-proxy...")
        os.environ["DATABRICKS_APP_URL"] = server_url

        # --- Dynamic URL for Proxy ---
        proxy_cmd = ["dba-mcp-proxy"] + (proxy_args or [])
        proxy_cmd.extend(["--databricks-app-url", server_url])

        print(f"Proxy Command: {' '.join(proxy_cmd)}")

        PROXY_PROCESS = subprocess.Popen(
            proxy_cmd,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

        # Block and wait for the proxy to finish (client closes connection or sends signal)
        proxy_exit_code = PROXY_PROCESS.wait()

    except SystemExit as e:
        # Catch the exit from the signal handler and honor it
        sys.exit(e.code)
    except Exception as e:
        print(f"An error occurred: {e}", file=sys.stderr)
        proxy_exit_code = 1

    finally:
        # 4. Fallback cleanup (in case the proxy exited naturally)
        cleanup_server()

    print(f"Shutdown complete. Final Exit Code: {proxy_exit_code}", file=sys.stderr)
    sys.exit(proxy_exit_code)


if __name__ == "__main__":
    try:
        launch_server_and_proxy(sys.argv[1:])
    except Exception as e:
        print(f"FATAL: Unhandled error in launcher: {e}", file=sys.stderr)
        sys.exit(1)
