"""Log yapılandırması: gizli değerler (API tokenları, LLM anahtarı) her kayıtta maskelenir."""

from __future__ import annotations

import logging

MASK = "***"


class RedactFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def _clean(self, s: str) -> str:
        for sec in self.secrets:
            s = s.replace(sec, MASK)
        return s

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secrets:
            return True
        msg = record.getMessage()
        if record.exc_info:
            # Biçimlenmiş izi önceden üretip temizle; orijinal exc_info düşürülür.
            fmt = logging.Formatter()
            msg = msg + "\n" + fmt.formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        record.msg = self._clean(msg)
        record.args = None
        return True


def setup_logging(secrets: list[str], level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(level)
    flt = RedactFilter(secrets)
    for h in root.handlers:
        for f in list(h.filters):
            if isinstance(f, RedactFilter):
                h.removeFilter(f)
        h.addFilter(flt)
    # Kütüphanelerin kendi handler'ları için de.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "openai"):
        for h in logging.getLogger(name).handlers:
            h.addFilter(flt)
