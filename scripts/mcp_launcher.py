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


def launch_server_and_proxy(proxy_args: List[str] = None):
    """
    Starts the FastAPI server, runs the proxy, and ensures server shutdown.
    It uses sys.executable to ensure both Uvicorn and the dba-mcp-proxy
    are run within the temporary, isolated environment created by uvx.
    """
    server_process = None
    proxy_exit_code = 1

    print(f"Starting FastAPI MCP server in background on {SERVER_URL}...")

    try:
        # 1. Start the Uvicorn/FastAPI server (Layer 1)
        # We use the Python executable from the uvx environment (sys.executable)
        # and run Uvicorn as a module (-m uvicorn) to guarantee it's available.
        # 1. Start the Uvicorn/FastAPI server (Layer 1)
        print(f"Uvicorn logs redirected to: {LOG_FILE_PATH}")

        # Open the log file for writing (will be closed in the finally block)
        log_file = open(LOG_FILE_PATH, "w")
        server_process = subprocess.Popen(
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
            stdout=log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )

        time.sleep(2)

        if server_process.poll() is not None:
            # Try to read output to diagnose failure
            error_output = server_process.communicate()[0].decode()
            raise RuntimeError(
                f"Server failed to start. Exit code: {server_process.returncode}. Output:\n{error_output}"
            )

        print(f"FastAPI server started with PID {server_process.pid}.")

        # 2. Run the dba-mcp-proxy (Layer 2 - The blocking client call)
        print("Executing the dba-mcp-proxy...")

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

    except Exception as e:
        print(f"An error occurred: {e}", file=sys.stderr)
        proxy_exit_code = 1

    finally:
        # 3. Clean Up (Robust PGID-based Shutdown)

        # Close the log file handle first
        if "log_file" in locals() and not log_file.closed:
            log_file.close()

        if server_process and server_process.poll() is None:
            print(
                f"Proxy finished. Attempting shutdown of FastAPI server (PID {server_process.pid})..."
            )

            try:
                pgid = os.getpgid(server_process.pid)

                # 1. Send SIGTERM (Graceful Kill) to the entire process group
                os.killpg(pgid, signal.SIGTERM)

                # Wait briefly
                time.sleep(1)

                # 2. Check if the process is still alive and use SIGKILL if necessary
                if server_process.poll() is None:
                    print(f"Graceful shutdown failed. Forcing kill on PGID {pgid}...")
                    os.killpg(pgid, signal.SIGKILL)
                    server_process.wait(timeout=1)
                else:
                    print("Shutdown successful.")

            except ProcessLookupError:
                # Process already died, which is the desired outcome
                pass
            except Exception as e:
                # Catch any unexpected errors during cleanup
                print(f"Error during server cleanup: {e}", file=sys.stderr)

    print(f"Shutdown complete. Final Exit Code: {proxy_exit_code}")
    sys.exit(proxy_exit_code)


if __name__ == "__main__":
    launch_server_and_proxy(sys.argv[1:])
