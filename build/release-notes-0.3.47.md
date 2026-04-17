## 요약

포커스 전환 직후 self edge hold가 풀리며 logical gap이 일시적으로 통과되던 경로를 보강했습니다. local hold에서 실제 clip 영역을 display 전체로 정리하고, 클릭이나 Win 키처럼 포커스 변화를 유발하는 입력 직후에도 hold 문맥이 즉시 무너지지 않도록 안정성을 높였습니다.

## 사용자 체감 변경사항

dead edge 또는 logical gap hold 중 포커스 전환 직후 반대편 display로 새어 넘어가던 현상을 줄였습니다.
이미 hold 중인 display의 논리 문맥을 더 안정적으로 유지해, 간헐적으로 반대편 display edge 기준으로 hold가 뒤집히던 케이스를 완화했습니다.
local hold 상태에서 안쪽으로 retreat할 때 cursor clip 때문에 붙잡혀 있던 문제가 줄어들어, edge에서 자연스럽게 빠져나올 수 있게 했습니다.

## 내부 변경사항

local hold clip rect와 hold 판정 rect를 분리해, hold는 edge line 기준으로 유지하되 실제 ClipCursor는 display 전체 bounds를 사용하도록 정리했습니다.
edge crossing 복원을 강화해, 샘플이 neighbor edge에 떨어져도 이전 display의 crossed edge 문맥을 우선 해석하도록 보강했습니다.
local edge hold에 focus-risk guard를 추가해, clip 상실 또는 불확실 rebound가 감지된 첫 move를 차단하도록 hold 지속 로직을 보강했습니다.
Win 키 입력과 self clip refresh 경로를 연동해 포커스 전환 계열 입력 뒤 guard가 다시 장전되도록 했습니다.

## 수정 사항

문제:
focus in 직후 local hold가 흔들리면 첫 move가 release sample로 소비되며 logical gap 검사를 우회할 수 있었습니다.

원인:
hold 해제 직전의 불확실한 sample을 그대로 통과시키는 경로가 있었고, clip 상실이 포커스 전환과 겹칠 때 이 현상이 재현됐습니다.

대응:
focus-risk 이후 짧은 guard 구간을 두고, clip mismatch 또는 rebound가 보이면 hold를 유지한 채 첫 leak sample을 차단하도록 변경했습니다.

문제:
local self hold에서 ClipCursor가 edge line 자체에 걸려, 안쪽으로 move를 해도 실제로는 release 공간이 없는 상태가 될 수 있었습니다.

원인:
hold 상태 판정용 rect와 local clip rect를 같은 edge line rect로 사용하고 있었습니다.

대응:
hold rect는 edge line으로 유지하되, local clip은 display 전체 bounds를 사용하도록 분리했습니다.

문제:
경계 샘플이 neighbor display edge에 정확히 떨어질 때 crossed display가 아니라 landed display 기준으로 hold 문맥이 뒤집히는 경우가 있었습니다.

원인:
current point edge press가 previous display crossing보다 먼저 처리되는 경로가 있었습니다.

대응:
previous sample 기반 crossing 복원을 current point edge press보다 우선 적용하도록 조정했습니다.

## 회귀 위험

local hold release가 이전보다 약간 보수적으로 동작할 수 있습니다.
특정 앱의 포커스/클립 타이밍 차이에 따라 guard sample 수 추가 조정이 필요할 수 있습니다.
multi-display logical/physical 배치가 복잡한 환경에서는 focus 전환 직후 동작을 실환경에서 한 번 더 확인하는 것이 안전합니다.

## 검증

자동 검증:
- `python -m pytest -q`
- `python -m ruff check .`

수동 검증 권장:
- 클릭으로 focus in 발생 직후 outward move 지속 시 logical gap 차단 확인
- Win 키 2회 직후 outward move 지속 시 logical gap 차단 확인
- 이미 focus된 창 클릭 시 hold 유지 확인
- inward retreat 시 자연스러운 hold release 및 떨림 없음 확인
- installer 설치 후 실환경에서 self dead edge / logical gap 재확인

## 영향 파일/모듈

- `runtime/app_identity.py`
- `capture/input_capture.py`
- `main.py`
- `routing/display_state.py`
- `routing/auto_switch.py`
- `routing/edge_actions.py`
- `tests/test_display_state.py`
- `tests/test_auto_switch.py`
- `tests/test_edge_actions.py`
- `tests/test_input_capture_suppression.py`
