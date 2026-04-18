## 요약

원격 업데이트 요청이 조용히 끊겨 보이던 경로를 줄였습니다. 업데이트 가능 배너의 보조 문구에서 버전 중복 표시를 제거했고, 원격 대상 노드가 busy 상태일 때 requester가 바로 이유를 볼 수 있도록 피드백을 보강했으며, 원격 상태 전송/수신 누락 지점을 추적할 수 있는 진단 로그를 추가했습니다.

## 사용자 체감 변경사항

- 새 업데이트 배너의 두 번째 줄에서 현재/대상 버전이 반복 표기되지 않고 더 짧은 안내 문구만 표시됩니다.
- 원격 업데이트 요청 대상이 이미 업데이트 확인 또는 설치 작업 중이면 requester에 즉시 안내 메시지가 표시됩니다.
- 원격 업데이트 상태가 끊겼을 때 로그에서 어느 단계에서 누락됐는지 더 쉽게 확인할 수 있습니다.

## 내부 변경사항

- `runtime/update_domain.py`
  - 업데이트 가능 배너의 기본 detail 문구를 버전 비노출 형태로 단순화했습니다.
  - 원격 업데이트 실패 detail 중 busy 전용 문구를 별도로 해석해 requester 메시지를 구체화했습니다.
- `runtime/settings_page.py`
  - 원격 업데이트 시작 시 busy 상태면 requester에게 즉시 실패 상태를 회신하도록 변경했습니다.
  - 원격 상태 emit 직전과 emit 누락 시점에 진단 로그를 추가했습니다.
- `runtime/status_window.py`
  - 원격 상태를 coordinator로 전송할 때 성공/실패 및 retry 진입 로그를 추가했습니다.
- `coordinator/client.py`
  - requester 불일치나 coordinator epoch 불일치로 원격 상태 frame을 버릴 때 디버그 로그를 추가했습니다.
- `tests/test_settings_page.py`
  - remote update busy 상태 회신 테스트를 추가/갱신했습니다.
- `tests/test_status_notifications.py`
  - 업데이트 배너 detail 문구 변경 기대값을 갱신했습니다.
- `tests/test_status_window.py`
  - requester busy 안내 메시지와 공용 배너 기대값을 갱신했습니다.
- `tests/test_update_domain.py`
  - self/remote update 메시지 생성 기대값을 갱신했습니다.

## 수정 사항

문제:
원격 업데이트 요청을 보낸 뒤 requester에는 "업데이트 요청 전송"만 보이고, 대상 노드가 busy였는지 상태 전송이 끊겼는지 구분하기 어려웠습니다. 또한 업데이트 가능 배너의 보조 문구에는 메인 문구와 동일한 버전 정보가 다시 노출됐습니다.

원인:
대상 노드의 `start_remote_update()`는 busy 상태에서 조용히 return하는 경로가 있었고, 원격 상태 emit/전송/수신 드롭 지점에는 원인 추적용 로그가 충분하지 않았습니다. 배너 detail 생성도 메인 문구와 별도로 버전 문자열을 다시 포함하고 있었습니다.

대응:
busy 상태에서는 requester에게 즉시 실패 상태와 전용 안내 문구를 보내도록 바꾸고, 원격 상태 경로 주요 지점에 진단 로그를 추가했습니다. 업데이트 가능 배너 detail은 버전 중복 없이 짧은 안내 문구로 통일했습니다.

## 회귀 위험

- requester busy 안내는 현재 `failed` status + detail 해석 방식으로 표현되므로, 장기적으로 별도 stage를 도입하면 더 명확할 수 있습니다.
- coordinator client의 일부 원격 상태 드롭 로그는 `debug` 레벨이라 기본 로그 설정에서는 바로 보이지 않을 수 있습니다.
- 실제 2대 PC 환경에서 busy 안내와 진단 로그가 기대한 순서로 남는지는 실기기 확인이 필요합니다.

## 검증

- 자동화 테스트
  - `python -m pytest .\tests\test_settings_page.py -k "update_notice or remote_update"`
  - `python -m pytest .\tests\test_status_notifications.py -k update`
  - `python -m pytest .\tests\test_status_window.py -k "update_banner or remote_update"`
  - `python -m pytest .\tests\test_update_domain.py`
  - `python -m pytest .\tests\test_coordinator_client.py -k remote_update_status`
- 빌드
  - `powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_installer.ps1 -Version 0.3.57`
- 수동 확인
  - 미실행: 실제 2대 PC 간 원격 업데이트 요청/상태 전파 확인

## 영향 파일/모듈

- `runtime/app_identity.py`
- `runtime/update_domain.py`
- `runtime/settings_page.py`
- `runtime/status_window.py`
- `coordinator/client.py`
- `tests/test_settings_page.py`
- `tests/test_status_notifications.py`
- `tests/test_status_window.py`
- `tests/test_update_domain.py`
