import uvicorn

if __name__ == "__main__":
    # Loopback only: no public exposure before authentication exists.
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
