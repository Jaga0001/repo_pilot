import logging
from fastapi import FastAPI
from routes.webhook import router as webhook_router
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = FastAPI(
    title="Sage — CI/CD AI Fix Agent",
    version="1.0.0",
    description="Receives CI/CD failure webhooks, analyses the error, fixes the code, and opens a PR.",
)

# Register routes
app.include_router(webhook_router)


@app.get("/")
def root():
    return {"message": "Sage CI/CD AI Agent is running"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)