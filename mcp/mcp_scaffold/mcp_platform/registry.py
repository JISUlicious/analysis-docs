"""서버 발견 — 디렉터리 스캔 + 명시적 opt-in + 실패 격리."""
import importlib, logging, pkgutil
log = logging.getLogger(__name__)

def discover(package="servers", only=None):
    """servers/<name>/ 각 패키지의 build()를 수집. 실패는 격리·기록."""
    pkg = importlib.import_module(package)
    found, failed = {}, {}
    for mi in pkgutil.iter_modules(pkg.__path__):
        if not mi.ispkg or mi.name.startswith("_"):
            continue
        if only and mi.name not in only:
            continue
        try:
            mod = importlib.import_module(f"{package}.{mi.name}")
            found[mi.name] = mod.build          # 규약: build() -> FastMCP
        except Exception as e:                  # ★ 한 서버 실패가 전체를 막지 않음
            failed[mi.name] = f"{type(e).__name__}: {e}"
            log.error("server '%s' load failed: %s", mi.name, e)
    return found, failed
