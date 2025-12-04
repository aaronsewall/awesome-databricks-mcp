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

def launch_server_and_proxy(proxy_args: List[str] = None):
    """
    Starts the FastAPI server, runs the proxy, and ensures server shutdown.
    """
    server_process = None
    proxy_exit_code = 1
    
    print(f"Starting FastAPI MCP server in background on {SERVER_URL}...")
    
    try:
        # 1. Start the Uvicorn/FastAPI server (Layer 1)
        # Use subprocess.Popen to run the server asynchronously
        server_process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", 
                "server.app:app", 
                "--host", SERVER_HOST, 
                "--port", str(SERVER_PORT)
            ],
            # Suppress server output; client expects clean stdio from the proxy
            stdout=subprocess.PIPE,  
            stderr=subprocess.STDOUT,
            # Create a new process group for clean shutdown
            preexec_fn=os.setsid 
        )
        
        # Give the server a moment to spin up
        time.sleep(2)
        
        if server_process.poll() is not None:
            raise RuntimeError(f"Server failed to start. Exit code: {server_process.returncode}")
            
        print(f"FastAPI server started with PID {server_process.pid}.")

        # 2. Run the dba-mcp-proxy (Layer 2 - The blocking client call)
        print("Executing the dba-mcp-proxy...")

        # Tell the proxy where the running server is
        os.environ["DATABRICKS_APP_URL"] = SERVER_URL
        
        # Build the command: dba-mcp-proxy + any arguments passed by the client
        proxy_cmd = ["dba-mcp-proxy"] + (proxy_args or [])
        
        # Execute the proxy; this will block until the client (e.g., Claude) is done
        proxy_result = subprocess.run(
            proxy_cmd,
            # Pass Standard I/O streams directly to the proxy
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            check=False
        )
        
        proxy_exit_code = proxy_result.returncode

    except Exception as e:
        print(f"An error occurred: {e}", file=sys.stderr)
        proxy_exit_code = 1
        
    finally:
        # 3. Clean Up (Graceful Shutdown)
        if server_process and server_process.poll() is None:
            print(f"Proxy finished. Shutting down FastAPI server (PID {server_process.pid})...")
            # Kill the process group to ensure all child workers are terminated
            try:
                os.killpg(os.getpgid(server_process.pid), signal.SIGTERM)
                server_process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        
    print(f"Shutdown complete. Final Exit Code: {proxy_exit_code}")
    sys.exit(proxy_exit_code)

if __name__ == "__main__":
    # Pass all arguments (after the script name) to the proxy
    launch_server_and_proxy(sys.argv[1:])
