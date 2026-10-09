from fastapi import FastAPI

from .api import router

app = FastAPI(title="3D Topo Print API", version="0.1.0")
app.include_router(router)
