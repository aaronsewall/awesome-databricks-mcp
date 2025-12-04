import subprocess
import os
import sys
import time
import signal
from typing import List

# --- Configuration ---
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
LOG_FILE_PATH = "uvicorn_mcp_server.log"
# This file will be created in the directory where uvx runs the script.

# Global variable to store the server process and its PID for signal handling
SERVER_PROCESS = None
LOG_FILE = None


def cleanup_server():
    """Performs the robust process group cleanup."""
    global SERVER_PROCESS, LOG_FILE

    if LOG_FILE and not LOG_FILE.closed:
        LOG_FILE.close()

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
            pass
        except Exception as e:
            print(f"Error during server cleanup: {e}", file=sys.stderr)


def signal_handler(sig, frame):
    """Handler for SIGINT (Ctrl+C) and SIGTERM (Client kill)."""
    cleanup_server()
    # Re-raise the signal to allow the main script to exit cleanly
    sys.exit(0)


def launch_server_and_proxy(proxy_args: List[str] = None):
    """
    Starts the FastAPI server, runs the proxy, and ensures server shutdown.
    It uses sys.executable to ensure both Uvicorn and the dba-mcp-proxy
    are run within the temporary, isolated environment created by uvx.
    """
    global SERVER_PROCESS, LOG_FILE
    proxy_exit_code = 1

    print(f"Starting FastAPI MCP server in background on {SERVER_URL}...")
    # 1. Register signal handlers immediately
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"Uvicorn logs redirected to: {LOG_FILE_PATH}")

    try:
        # 1. Start the Uvicorn/FastAPI server (Layer 1)
        # We use the Python executable from the uvx environment (sys.executable)
        # and run Uvicorn as a module (-m uvicorn) to guarantee it's available.
        # 1. Start the Uvicorn/FastAPI server (Layer 1)
        print(f"Uvicorn logs redirected to: {LOG_FILE_PATH}")

        # Open the log file for writing (will be closed in the finally block)
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
                str(SERVER_PORT),
            ],
            stdout=LOG_FILE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )

        time.sleep(2)

        if SERVER_PROCESS.poll() is not None:
            # Try to read output to diagnose failure
            error_output = SERVER_PROCESS.communicate()[0].decode()
            raise RuntimeError(
                f"Server failed to start. Exit code: {SERVER_PROCESS.returncode}. Output:\n{error_output}"
            )

        print(f"FastAPI server started with PID {server_process.pid}.")

        # 2. Run the dba-mcp-proxy (Layer 2 - The blocking client call)
        print("Executing the dba-mcp-proxy...")
        os.environ["DATABRICKS_APP_URL"] = SERVER_URL

        # --- FIX STARTS HERE ---

        # 1. Start with the command itself and the arguments passed by the user/client
        proxy_cmd = ["dba-mcp-proxy"] + (proxy_args or [])

        # 2. Explicitly ADD the required --databricks-app-url argument here.
        proxy_cmd.extend(["--databricks-app-url", SERVER_URL])

        # 3. Print the command being run for debugging (optional but helpful)
        print(f"Proxy Command: {' '.join(proxy_cmd)}")

        # --- FIX ENDS HERE ---

        # Execute the proxy; this will block until the client (e.g., Claude) is done
        proxy_result = subprocess.run(
            proxy_cmd,
            # Pass Standard I/O streams directly to the proxy
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            check=False,
        )

        proxy_exit_code = proxy_result.returncode

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
