import logging
from datetime import datetime
from pathlib import Path


def setup_logging(*, debug: bool = False, log_dir: str | Path | None = None) -> Path | None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_path: Path | None = None
    if debug and log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"debug-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
        handlers=handlers,
    )
    return log_path
