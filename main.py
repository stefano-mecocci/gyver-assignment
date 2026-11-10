from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from database import init_db
from routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    init_db()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(router)


def main():
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
