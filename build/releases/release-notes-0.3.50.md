## 요약

Windows self update check fallback 호환성과 실패 추적을 추가로 보강했습니다. 구형 PowerShell 환경에서 `Invoke-WebRequest` fallback이 헤더 직렬화 단계에서 깨질 수 있던 문제를 줄였고, update check 실패가 UI 메시지에만 보이던 경로에는 구조화된 실패 로그를 추가했습니다.

## 사용자 체감 변경사항

- 일부 PC에서 self update check가 PowerShell fallback 경로에서 추가 호환성 문제로 실패하던 가능성을 줄였습니다.
- update check 실패 시 UI 경고만 뜨고 로그에는 남지 않던 경로를 보강해, 이후 원인 추적이 쉬워졌습니다.

---

## 내부 변경사항

- [runtime/http_utils.py](C:/Users/User/Desktop/미르/개인/codex/multi-controller/runtime/http_utils.py)
  PowerShell fallback의 헤더 직렬화를 `Headers.GetValues()` 의존 없이 동작하도록 바꿨습니다.
- [runtime/settings_page.py](C:/Users/User/Desktop/미르/개인/codex/multi-controller/runtime/settings_page.py)
  version check 실패 시 `trigger`, `error_kind`, `status_code`, `error`를 포함한 warning 로그를 남기도록 보강했습니다.

---

## 수정 사항

### 문제 1
문제:
일부 PC에서 Windows native fallback이 `mscorlib 4.0.0.0` 계열 헤더 컬렉션과 충돌해 `GetValues` 호출 단계에서 실패할 수 있었습니다.

원인:
fallback 스크립트가 `$resp.Headers.GetValues($name)` 메서드 존재를 전제로 헤더를 직렬화하고 있었습니다.

대응:
헤더 값을 `$resp.Headers[$name]`로 읽은 뒤 `null / 배열 / 단일 값`을 각각 안전하게 문자열 배열로 변환하도록 수정했습니다.

### 문제 2
문제:
self update check 실패가 UI 메시지로만 보이고, 로그에는 남지 않아 원인 추적이 어려웠습니다.

원인:
`SettingsPage._run_version_check()`가 예외를 payload로만 싣고, 별도 logging 경로를 두지 않았습니다.

대응:
실패 시점에 `[UPDATE] version check failed ...` warning 로그를 남기고, `trigger / error_kind / status_code / error`를 함께 기록하도록 보강했습니다.

---

## 회귀 위험

- PowerShell fallback은 여러 Windows/PowerShell 버전 차이를 계속 상대해야 하므로, 실제 헤더 타입이 더 특이한 환경에서는 추가 호환성 보강이 필요할 수 있습니다.
- 실패 로그는 이제 남지만, `failure_kind` 일부는 여전히 native stderr 문구 분류에 의존합니다.

---

## 검증

자동 검증:
- `python -m pytest -q`
- `python -m ruff check .`
- `python -m pytest -q tests/test_http_utils.py tests/test_settings_page.py`

실행 확인:
- 앱과 유사한 `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass` fallback 조건 재현
- `STATUS=200`
- `TYPE=Microsoft.PowerShell.Commands.BasicHtmlWebResponseObject`
- `HAS_STATUS=True`
- `EXIT=0`

빌드 검증:
- `powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1 -Version 0.3.50`

---

## 영향 파일/모듈

- `runtime/app_identity.py`
- `runtime/http_utils.py`
- `runtime/settings_page.py`
- `tests/test_http_utils.py`
- `tests/test_settings_page.py`
