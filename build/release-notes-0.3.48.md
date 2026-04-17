## 요약

edge hold 안정화, 최근 메시지 기록 일관성 정리, 원격 업데이트 메시지 흐름의 공통 업데이트 도메인 흡수를 함께 반영한 릴리즈입니다. 포커스 전환 직후 logical gap 누수와 메시지 기록 누락/혼선, 원격 업데이트 단계 관찰 불일치 문제를 구조적으로 보강했습니다.

## 사용자 체감 변경사항

- dead edge / logical gap hold 중 포커스 전환 직후 반대편 display로 잠깐 새는 현상이 줄었습니다.

- local hold 상태에서 안쪽으로 retreat할 때 edge에 붙잡히지 않고 더 자연스럽게 빠져나올 수 있습니다.

- 최근 메시지 기록에 동일한 메시지도 이벤트 단위로 안정적으로 남고, multi-line 메시지도 일관되게 표시됩니다.

- 배치 차이, 오래된 감지 같은 상태 요약성 passive alert는 상단 알림으로 띄우지 않도록 정리했습니다.

- 원격 업데이트 요청 이후 확인, 다운로드, 설치, 완료/실패 단계 메시지가 공통 이벤트 모델 위에서 이어지도록 정리했습니다.

- 업데이트 안내 문구가 단순히 `최신`이라고만 표시되지 않고, 현재 버전과 대상 버전이 더 명확히 드러납니다.

## 내부 변경사항

- `routing/edge_actions.py`, `routing/auto_switch.py`에서 local edge hold를 hysteresis/state-machine 기반으로 정리하고, focus-risk / clip mismatch / rebound 계열 복구 경로를 보강했습니다.

- `runtime/status_controller.py`, `runtime/status_window.py`, `runtime/status_tray.py`, `runtime/qt_app.py`에서 화면 표시 정책과 최근 메시지 기록 정책을 분리하고, recent history를 이벤트 단위 기록으로 통일했습니다.

- `runtime/update_domain.py`를 추가해 update target / action / stage / event / session 메타데이터를 공통 payload로 정의했습니다.

- `runtime/remote_update_status.py`는 원격 전용 중심 모듈에서 공통 업데이트 도메인 기반 호환 wrapper로 축소했습니다.

- `runtime/settings_page.py`, `runtime/status_window.py`, `runtime/qt_app.py`, `runtime/app_update.py`가 공통 업데이트 이벤트를 생성/소비하도록 연결했습니다.

## 수정 사항

### 1. 포커스 전환 직후 logical gap 누수

- 문제: 클릭 또는 `Win` 키 등으로 포커스가 전환된 직후 local hold가 흔들리면 첫 move가 release sample로 소비되며 logical gap 검사를 우회할 수 있었습니다.

- 원인: hold 유지/해제, clip 상태, rebound 해석이 여러 파일에 분산돼 있었고, 정상 상태와 복구 상태가 같은 분기 안에서 처리되고 있었습니다.

- 대응: local hold를 명시적 상태 머신으로 정리하고, focus-risk / clip loss / leak repair를 hold 문맥 안으로 모아 display authority와 복구 경로를 안정화했습니다.

---

### 2. recent history 누락 및 메시지 정책 혼선

- 문제: 일부 알림은 최근 메시지 기록에 남지 않거나, 동일 문자열 메시지가 기록 정책에 따라 간헐적으로 빠지는 경로가 있었습니다.

- 원인: 화면 표시 여부와 기록 저장 여부가 같은 호출 경로에 섞여 있었고, tray-only / status-only / combined notification 경로가 서로 다른 규칙으로 동작하고 있었습니다.

- 대응: `publish_message()` 중심의 단일 기록 정책으로 정리하고, 기록 단위를 `표시된 문구`가 아니라 `발생한 알림 이벤트`로 고정했습니다. multi-line 메시지도 정규화와 렌더 정책을 함께 맞췄습니다.

---

### 3. 원격 업데이트 메시지 모델의 중간 단계화

- 문제: 원격 업데이트 요청 이후 단계 메시지 개선이 들어가 있었지만, 구조가 원격 전용에 머물러 self/manual/scheduled 업데이트 흐름과는 따로 놀고 있었습니다.

- 원인: `remote_update_status`가 세션/event 메타데이터를 가지기 시작했지만, 여전히 원격 중심 naming과 소비 경계에 묶여 있어 전체 업데이트 기능의 공통 도메인으로 설명되기 어려웠습니다.

- 대응: 부분 롤백 대신 기존 메타데이터를 `update_domain` 공통 모델로 흡수했습니다. 이제 banner, 알림, 최근 메시지 기록, outcome replay가 같은 update event 축을 소비하도록 정리했습니다.

## 회귀 위험

- Windows 포커스/클립 타이밍은 환경차가 커서, 특정 앱 조합에서는 local hold 보정 동작을 실환경에서 한 번 더 확인하는 편이 안전합니다.

- 공통 업데이트 도메인은 helper 중심으로 정리된 1차 구조라, 앞으로 `selected_nodes`, `timeout`, `version_sync` 같은 축을 더 넓힐 때는 테스트를 같이 보강해야 합니다.

- 이번 배포에는 여러 미배포 변경이 함께 포함되므로, 라우팅/알림/업데이트 세 축에 대한 설치 후 smoke 확인이 권장됩니다.

## 검증

자동 검증:
- `python -m pytest -q`
- `python -m ruff check .`

빌드 검증:
- `powershell -ExecutionPolicy Bypass -File scripts/build_windows_exe.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1 -Version 0.3.48`

수동 검증:
- 포커스 전환 직후 dead edge / logical gap hold 유지 확인
- 동일 메시지 반복 / multi-line 메시지의 recent history 기록 확인
- 원격 업데이트 요청 이후 단계 메시지와 outcome replay 흐름 확인

## 영향 파일/모듈

- `runtime/app_identity.py`
- `routing/auto_switch.py`
- `routing/edge_actions.py`
- `runtime/status_controller.py`
- `runtime/status_tray.py`
- `runtime/status_view.py`
- `runtime/status_window.py`
- `runtime/qt_app.py`
- `runtime/settings_page.py`
- `runtime/app_update.py`
- `runtime/app_version.py`
- `runtime/update_domain.py`
- `runtime/remote_update_status.py`
- `coordinator/client.py`
- `coordinator/protocol.py`
- `coordinator/service.py`
- `main.py`
- `tests/test_auto_switch.py`
- `tests/test_edge_actions.py`
- `tests/test_status_controller.py`
- `tests/test_status_tray.py`
- `tests/test_status_view.py`
- `tests/test_status_window.py`
- `tests/test_status_notifications.py`
- `tests/test_update_domain.py`
- `tests/test_qt_app.py`
- `tests/test_settings_page.py`
- `tests/test_app_update.py`
- `tests/test_coordinator_client.py`
- `tests/test_coordinator_service.py`
- `tests/test_main_cli.py`
