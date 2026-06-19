import asyncio
import os
import sys

# psycopg async mode is incompatible with the Windows-default ProactorEventLoop.
# This must be set BEFORE uvicorn creates the event loop. No-op on Linux/macOS.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "supervisor.app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8002")),
        reload=False,
        # uvicorn 0.36+ uses a loop_factory that explicitly creates ProactorEventLoop
        # on Windows, bypassing set_event_loop_policy(). Using loop="none" tells
        # uvicorn to pass loop_factory=None to asyncio.Runner, which then falls back
        # to the policy above and creates a SelectorEventLoop instead.
        loop="none" if sys.platform == "win32" else "auto",
    )
