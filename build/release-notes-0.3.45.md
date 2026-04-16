## 라우팅
- self/target edge hold를 하나의 상태 객체로 통합해 `sync -> continue -> route` 순서로 처리하도록 정리했습니다.
- self dead edge와 self logical gap에서 반복 outward move가 들어와도 재-warp jitter 없이 hold를 유지하고, inward move가 들어오면 즉시 해제되도록 안정성을 보강했습니다.
- target dead edge와 target logical gap도 같은 hold continuation 경로를 사용하도록 확장해, remote inject 중 edge 근처에서 경계 통과나 떨림이 반복되던 흐름을 줄였습니다.

## Remote Inject 및 런타임
- active target으로 전환할 때 남아 있던 self edge hold를 먼저 정리하고, remote pointer 상태와 충돌하지 않도록 경계를 맞췄습니다.
- certifi/SSL 경로는 기존 bundle 탐색을 유지하면서, Windows에서 TLS/인증서 계열 실패가 나면 `Invoke-WebRequest` 기반 native fallback으로 업데이트/버전 체크를 한 번 더 시도하도록 보강했습니다.
- release 배포용 `scripts/publish_release.ps1`를 워크플로에 포함해 `build/release-notes-<version>.md`를 기준으로 빌드와 릴리즈를 묶어 실행할 수 있게 했습니다.

## 검증
- `python -m pytest -q tests\test_app_version.py tests\test_http_utils.py tests\test_injector_logging.py tests\test_sink_injector_wiring.py tests\test_clip_recovery.py tests\test_edge_actions.py tests\test_display_state.py tests\test_edge_routing.py tests\test_routing_table.py tests\test_auto_switch.py`
- 결과: `124 passed`
- `powershell -ExecutionPolicy Bypass -File scripts\build_windows_installer.ps1 -Version 0.3.45`로 `MultiScreenPass-Setup-0.3.45.exe` 빌드
