## 요약

self blocked edge / logical gap hold를 `즉시 차단` 중심으로 다시 정렬했습니다. 정상 self block 경로에서는 local hold와 clip으로 먼저 막고, warp는 clip 시작 실패나 실제 leak 복구 같은 예외 상황에서만 fallback으로 남기도록 바꿨습니다.

Windows native self update check fallback도 다시 정리했습니다. `status 0`을 단순 실패로만 보정하는 대신, 왜 그런 값이 보였는지 더 아래 계층까지 추적해서 `-UseBasicParsing` 누락과 구형 PowerShell의 `ConvertFrom-Json -AsHashtable` 비호환 문제를 함께 해결했습니다.

## 사용자 체감 변경사항

- self dead edge / logical gap에서 마우스가 먼저 넘어갔다가 다시 돌아오는 듯 보이던 체감을 줄였습니다.
- 정상 self block에서는 clip/hold가 먼저 작동하고, warp는 예외 상황에서만 수행됩니다.
- Windows self update check에서 PowerShell 보안 경고에 가까운 동작과 status 0 계열 오작동 가능성을 줄였습니다.

---

## 내부 변경사항

- [routing/edge_actions.py](C:/Users/User/Desktop/미르/개인/codex/multi-controller/routing/edge_actions.py)
  self block 경로의 immediate warp를 제거하고, `_begin_edge_hold()`가 성공하면 clip/hold만 세운 채 현재 샘플을 소비하도록 변경했습니다.
- [runtime/http_utils.py](C:/Users/User/Desktop/미르/개인/codex/multi-controller/runtime/http_utils.py)
  Windows native fallback에 `-UseBasicParsing`을 추가하고, `ConvertFrom-Json -AsHashtable` 의존을 제거했습니다.
- [runtime/http_utils.py](C:/Users/User/Desktop/미르/개인/codex/multi-controller/runtime/http_utils.py)
  `WindowsNativeRequestError`를 도입해 `missing_http_status`, `invalid_http_status`, `unexpected_http_status`, `transport_failure`를 구분해 보존합니다.
- [runtime/settings_page.py](C:/Users/User/Desktop/미르/개인/codex/multi-controller/runtime/settings_page.py)
  version check 결과 payload에 `error_kind`, `status_code`를 함께 싣도록 보강했습니다.

---

## 수정 사항

### 문제 1
문제:
self blocked edge에서 사용자가 체감하기에 “먼저 통과했다가 나중에 warp로 복구되는” 듯 보이는 동작이 남아 있었습니다.

원인:
self block 정상 경로 안에 local hold/clip 직후 immediate warp가 포함되어 있었고, stale move suppression도 그 warp를 전제로 동작하고 있었습니다.

대응:
정상 self block은 `hold/clip 먼저, warp 없음`으로 재정렬했습니다. 이제 warp는 local clip 시작 실패나 hold leak 복구 같은 예외 상황에서만 fallback으로 수행됩니다.

### 문제 2
문제:
Windows native update fallback에서 `status 0`이 보일 수 있었고, UI와 로그에서 왜 그런 값이 나왔는지 설명하기 어려웠습니다.

원인:
PowerShell helper가 `StatusCode`를 바로 `[int]`로 캐스팅해서 `$null -> 0` 같은 값이 생길 수 있었고, 동시에 이 PC에서는 `ConvertFrom-Json -AsHashtable` 자체가 지원되지 않아 더 아래 단계에서 먼저 실패할 수 있었습니다.

대응:
`StatusCode` 존재 여부를 먼저 확인하고, status가 없으면 `missing_http_status`로 올리도록 바꿨습니다. 또 `-UseBasicParsing`을 추가하고 `-AsHashtable` 의존을 제거해 구형 PowerShell에서도 fallback이 정상 동작하도록 보강했습니다.

---

## 회귀 위험

- self block의 정상 경로에서 warp가 빠졌기 때문에, 특정 Windows 환경에서 `ClipCursor` 자체가 불안정하면 fallback repair 경로 의존도가 상대적으로 더 중요해질 수 있습니다.
- Windows native fallback의 `failure_kind` 분류 중 일부는 stderr 문구에 의존하므로, 장기적으로는 더 명시적인 exit/payload contract로 올리는 편이 안전합니다.

---

## 검증

자동 검증:
- `python -m pytest -q`
- `python -m ruff check .`
- `python -m pytest -q tests/test_http_utils.py tests/test_settings_page.py`

실기기/실행 확인:
- 앱과 유사한 `powershell.exe -NoProfile -NonInteractive` fallback 조건으로 직접 재현
- 결과:
  - `STATUS=200`
  - `TYPE=Microsoft.PowerShell.Commands.BasicHtmlWebResponseObject`
  - `HAS_STATUS=True`
  - `EXIT=0`

빌드 검증:
- `powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1 -Version 0.3.49`

---

## 영향 파일/모듈

- `routing/edge_actions.py`
- `runtime/http_utils.py`
- `runtime/settings_page.py`
- `tests/test_edge_actions.py`
- `tests/test_auto_switch.py`
- `tests/test_http_utils.py`
- `tests/test_settings_page.py`
