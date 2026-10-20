import uvicorn

from app import settings

if __name__ == "__main__":
    # Loopback only: no public exposure before authentication exists.
    kwargs = {}
    if settings.is_production():
        # Access logging in production would log every request path
        # (including ones carrying attempted traversal payloads) to the
        # supervisor's stdout/log file; the app's own structured logging
        # covers what's needed.
        kwargs["access_log"] = False
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, **kwargs)
