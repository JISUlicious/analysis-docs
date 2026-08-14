"""DBHub (요약판) — 실제로는 앞선 설계 문서의 구현."""
class DBHub:
    def __init__(self, targets=None, access_plan=None):
        self._targets, self._plan = targets or {}, access_plan or {}
    async def fetch_all(self, target, sql, *, server, row_limit=500):
        allowed = self._plan.get(server)
        if allowed is not None and target not in allowed:
            raise PermissionError(f"{server} → {target} 금지")
        return {"rows": [{"demo": f"{target}:{sql[:20]}"}], "truncated": False}
hub = DBHub(targets={"sales": "...", "hr": "..."},
            access_plan={"sales": {"sales"}, "hr": {"hr"}})
