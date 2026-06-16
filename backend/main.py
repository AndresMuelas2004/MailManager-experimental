from __future__ import annotations

import uvicorn

from api.app import app
from api.logging_config import configure_logging


def main() -> None:
    configure_logging()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)


if __name__ == "__main__":
    main()
