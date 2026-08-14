# 다중 MCP 서버 프로젝트 스캐폴드 (검증용)

[내부구조 가이드](../MCP_프로젝트_내부구조_가이드.md)의 참조 구현.

```
mcp_platform/   플랫폼팀 소유 — registry(발견), db(DBHub 요약판)
servers/        담당자 소유 — sales/, hr/, broken/(실패 격리 검증용 고의 오류)
verify.py       6개 항목 검증 스크립트
```

실행: `python verify.py` (mcp SDK 필요)
- ① 발견 + 실패 격리  ② 서브셋 선택  ③ build() 규약
- ④ 의존 방향 검사(AST)  ⑤ 도구 단위 테스트  ⑥ 경계 일치(allowlist)

`servers/broken/`은 **의도적으로 import 오류**를 내는 서버 — 하나가 죽어도
나머지가 기동되는지 확인하는 용도다.
