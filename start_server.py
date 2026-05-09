import os
os.environ["CARDPOOL_DISABLE_REVALIDATOR"] = "1"
os.environ["CARDPOOL_DISABLE_HOT_SETS"] = "1"
os.environ["CARDPOOL_EBAY_SKIP_VERIFY"] = "1"
import uvicorn
uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
