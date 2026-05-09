import os
os.environ.setdefault("CARDPOOL_DISABLE_JOBS", "1")
import uvicorn
uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
