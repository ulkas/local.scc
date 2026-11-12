import sys
import time
from pathlib import Path

helper_dir = Path(__file__).resolve().parent / "sc-helper" / "service.sc.helper"
sys.path.insert(0, str(helper_dir))

from resources.lib import start_server

print("Starting SC Helper...", flush=True)

server, manager = start_server(port=0)
port = server.server_address[1]

print(f"SC HELPER READY: http://127.0.0.1:{port}", flush=True)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopping...", flush=True)
finally:
    try:
        manager.close_all_upstreams()
        manager.close_all()
        server.shutdown()
        server.server_close()
    except Exception as e:
        print("Cleanup:", e)
