from triaj.config import Settings
from triaj.models import Category
from triaj.projects import effective
from triaj.rules import DEFAULT_ENV_PATTERNS, rule_decision


def cat(status, msg=None, trace=None, flaky=False, patterns=DEFAULT_ENV_PATTERNS):
    return rule_decision(status, msg, trace, flaky, patterns)[0]


def test_rules_order():
    # flaky her şeyden önce
    assert cat("failed", "Connection refused", flaky=True) == Category.flaky
    # ortam kalıbı broken/failed'den önce; iz de taranır
    assert cat("broken", "x", "java.net.ConnectException: Connection refused") == Category.ortam
    assert cat("failed", "Bağlantı zaman aşımı") == Category.ortam
    assert cat("failed", "requests.exceptions.ConnectionError: boom") == Category.ortam
    assert cat("broken", "NullPointerException") == Category.test_kodu
    assert cat("failed", "expected 3 but was 4") == Category.urun
    assert cat("skipped") == Category.bilinmiyor


def test_rules_project_patterns():
    s = Settings()
    eff = effective({"env_patterns": ["DB kilitli"]}, s)
    assert eff.env_patterns == ["DB kilitli"]
    assert cat("failed", "DB kilitli", patterns=eff.env_patterns) == Category.ortam
    # varsayılan liste yerine geçti
    assert cat("failed", "TimeoutException", patterns=eff.env_patterns) == Category.urun

    eff2 = effective({"env_patterns_extra": ["Selenium grid dolu"]}, s)
    assert "TimeoutException" in eff2.env_patterns
    assert cat("broken", "Selenium grid dolu", patterns=eff2.env_patterns) == Category.ortam
