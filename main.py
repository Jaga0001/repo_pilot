from fastapi import FastAPI
from routes.webhook import router as webhook_router
import uvicorn

app = FastAPI(
    title="CI/CD AI Agent",
    version="1.0.0"
)

# Register routes
app.include_router(webhook_router)


@app.get("/")
def root():
    return {"message": "CI/CD AI Agent is running"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)