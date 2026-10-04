"""Development entry point: python run.py"""

import uvicorn

if __name__ == "__main__":
    # Proxy headers are interpreted by backend.tracking only for configured peers.
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True,
                proxy_headers=False)
