# GUI 리팩토링 실행 계획

## 구현 상태

2026-04-11 기준으로 이 문서의 범위는 구현 완료 상태다.

- GUI를 `요약 / 레이아웃 / 연결 상태 / 고급 정보` 탭 구조로 재편했다.
- 레이아웃 편집기를 별도 모듈로 분리하고, 확대/축소와 직접 pan viewport를 도입했다.
- `runtime/status_window.py`는 창 조립과 상위 refresh 중심 셸로 축소했다.
- 상태 helper, geometry, dialog, layout editor 테스트를 별도 파일로 분리했다.
- `python -m pytest`, `python -m ruff check .` 기준 검증을 마쳤다.

## 목적

이 문서는 현재 GUI와 레이아웃 편집기 리팩토링을 실제 작업 단위로 쪼개기 위한 실행 계획이다.

이번 계획은 아래 4가지를 직접 해결하는 데 초점을 둔다.

1. 한 화면에 정보와 기능이 몰려 있는 현재 GUI를 카테고리화한다.
2. 레이아웃 편집 영역을 더 크게 확보한다.
3. 레이아웃 편집기의 자동 평면 이동 느낌을 제거하고, 확대/축소와 직접 pan 기반으로 바꾼다.
4. 그 외 유지보수성과 UX 측면에서 함께 손볼 항목을 정리한다.

## 현재 구조 요약

현재 GUI 병목은 `runtime/status_window.py`에 집중되어 있다.

- 상태창 쉘과 전체 Tk 루프
- 사용자용 상태 문구 생성
- 레이아웃 캔버스 렌더링
- 레이아웃 드래그 입력 처리
- 자동 전환 설정 팝업
- 모니터 맵 편집 팝업
- 편집 락과 publish 흐름

현재 파일은 800줄이 넘고, 상태 표시와 편집기가 강하게 결합되어 있다.  
특히 레이아웃 캔버스는 매 렌더마다 `layout_bounds()` 기준으로 원점을 다시 잡기 때문에, 노드를 바깥으로 드래그하면 사용자는 캔버스가 자동으로 움직이는 것처럼 느끼게 된다.

## 리팩토링 목표

### 목표

- `status_window.py`를 창 조립과 생명주기 관리 위주로 축소한다.
- GUI를 `요약 / 레이아웃 / 연결 상태 / 고급 정보` 중심으로 분리한다.
- 레이아웃 편집기를 독립 컴포넌트처럼 다룰 수 있게 만든다.
- 레이아웃 편집은 `고정된 world 좌표 + viewport(zoom/pan)` 구조로 바꾼다.
- 기존 coordinator 기반 단일 편집 락, preview/persist 계약은 유지한다.
- 테스트를 구조에 맞게 재배치해 이후 리팩토링 비용을 낮춘다.

### 비목표

- coordinator protocol 자체를 새로 설계하지 않는다.
- grid snap 기반 PC 배치 모델은 이번 단계에서 유지한다.
- GUI 프레임워크를 Tkinter 밖으로 옮기지 않는다.
- 모니터 맵 편집기를 즉시 그래픽 편집기로 전면 교체하지 않는다.

## 대상 파일과 목표 책임

### 수정 대상

- `runtime/status_window.py`
  - 최종 목표: 창 조립, 탭 구성, refresh scheduling, 상위 수준 이벤트 연결만 담당
- `runtime/layouts.py`
  - 최종 목표: 도메인 모델과 순수 레이아웃 연산만 유지
- `coordinator/client.py`
  - 최종 목표: layout preview/persist publish 계약 유지
- `main.py`
  - 최종 목표: 새 모듈 import 및 wiring만 최소 수정
- `tests/test_status_window.py`
  - 최종 목표: 창 shell 수준 테스트만 남기고, 세부 책임 테스트는 분리
- `tests/test_coordinator_client.py`
  - 최종 목표: preview/persist 동작 회귀 방지
- `tests/test_layouts.py`
  - 최종 목표: 레이아웃 도메인 회귀 방지

### 생성 권장 파일

- `runtime/status_view.py`
  - `StatusView`, 사용자용 텍스트 helper, peer/target 표시용 순수 함수
- `runtime/layout_geometry.py`
  - world 좌표, canvas 좌표, viewport 변환과 node bounds 계산
- `runtime/layout_editor.py`
  - 레이아웃 캔버스 렌더링, zoom/pan, node drag, selection, overlap 표시
- `runtime/layout_dialogs.py`
  - 자동 전환 세부 설정 팝업, 모니터 맵 편집 팝업
- `tests/test_status_view.py`
  - 상태 문구와 helper 단위 테스트
- `tests/test_layout_editor.py`
  - viewport, drag, pan, zoom, rerender 회귀 테스트
- `tests/test_layout_dialogs.py`
  - 팝업 validation 로직 단위 테스트

## 작업 패키지

아래 패키지는 그대로 이슈 또는 PR 단위로 쪼갤 수 있게 구성한다.

### P0. 베이스라인 고정

#### 목표

현재 동작을 테스트로 먼저 고정해서, 구조 분리 중 의도치 않은 회귀를 막는다.

#### 세부 작업

- 기존 `tests/test_status_window.py`에서 순수 helper 테스트와 GUI shell 테스트를 구분한다.
- 레이아웃 drag preview, release persist, refresh 중 rerender 유지 테스트를 유지한다.
- coordinator preview/persist 계약 테스트를 확인하고, 이후 리팩토링에서 깨지지 않게 기준으로 삼는다.

#### 대상 파일

- 수정: `tests/test_status_window.py`
- 확인: `tests/test_coordinator_client.py`
- 확인: `tests/test_layouts.py`

#### 완료 기준

- 현재 GUI 동작을 설명하는 테스트 이름이 역할별로 정리되어 있다.
- 이후 파일 분리를 해도 실패 원인을 빠르게 찾을 수 있다.

---

### P1. `StatusWindow` 책임 분리

#### 목표

`runtime/status_window.py`를 더 이상 모든 UI 기능의 집합소로 두지 않고, 순수 helper와 편집기 책임을 바깥으로 뺀다.

#### 세부 작업

- 사용자용 텍스트 생성 함수와 view model을 `runtime/status_view.py`로 이동한다.
- 레이아웃 geometry 계산을 `runtime/layout_geometry.py`로 이동한다.
- 팝업 생성/검증 코드를 `runtime/layout_dialogs.py`로 이동한다.
- `runtime/status_window.py`는 Tk root 생성, Notebook 조립, 각 섹션 연결, 상위 refresh만 담당하게 축소한다.

#### 대상 파일

- 수정: `runtime/status_window.py`
- 생성: `runtime/status_view.py`
- 생성: `runtime/layout_geometry.py`
- 생성: `runtime/layout_dialogs.py`
- 수정: `main.py`
- 생성/수정: `tests/test_status_view.py`, `tests/test_status_window.py`, `tests/test_layout_dialogs.py`

#### 완료 기준

- `status_window.py`에서 순수 helper와 dialog 구현 상세가 빠져 있다.
- 새 파일 경계만 바뀌고, 외부 동작은 기존과 동일하다.
- `StatusWindow`는 “창을 조립하는 클래스”로 읽힌다.

---

### P2. 정보 구조 개편과 레이아웃 영역 확대

#### 목표

한 화면에 모든 정보를 늘어놓는 구조를 탭 중심 구조로 바꾸고, 레이아웃 편집기가 가장 큰 면적을 쓰게 한다.

#### 세부 작업

- 최상위 UI를 `ttk.Notebook` 기반으로 개편한다.
- 권장 탭 구성:
  - `요약`: 현재 제어 대상, 핵심 상태, 자주 쓰는 액션
  - `레이아웃`: 편집기, 편집 상태, 선택 노드 정보, 편집 도구
  - `연결 상태`: peer 목록과 운영 상태
  - `고급 정보`: coordinator, lease, router, config path 등
- 기본 창 크기를 확대하고, row/column weight를 다시 잡아 레이아웃 탭이 여유 공간을 최대한 사용하게 한다.
- 기존 “고급 정보 토글”은 탭 구조가 들어가면 제거하거나 단순화한다.

#### 대상 파일

- 수정: `runtime/status_window.py`
- 수정: `runtime/layout_editor.py`
- 수정: `tests/test_status_window.py`

#### 완료 기준

- 레이아웃 편집 화면이 현재보다 명확히 넓다.
- 사용자는 요약 정보와 고급 운영 정보 사이를 구조적으로 구분해 볼 수 있다.
- 레이아웃 편집 관련 버튼은 레이아웃 탭 내부에만 배치된다.

---

### P3. 레이아웃 편집기 viewport 재설계

#### 목표

현재의 “bounds 재계산 기반 화면 이동”을 없애고, 고정된 장면 위에서 사용자가 직접 확대/축소와 이동을 수행하도록 바꾼다.

#### 세부 작업

- `ViewportState`를 도입한다.
  - `zoom`
  - `pan_x`
  - `pan_y`
- node 좌표는 world 좌표로 유지하고, canvas 렌더링 시에만 viewport 변환을 적용한다.
- 드래그 동작을 구분한다.
  - node 위에서 drag: node 이동
  - 빈 공간에서 drag: 화면 pan
- 마우스 휠 zoom을 지원한다.
- `Fit to view`, `100%`, `Reset view` 액션을 제공한다.
- 드래그 중에는 viewport를 유지하고, node 이동 시에도 장면 원점이 바뀌지 않게 한다.
- 기존 자동 평면 이동 느낌은 제거한다.
- overlap 방지와 grid snap은 유지한다.

#### 대상 파일

- 생성/수정: `runtime/layout_editor.py`
- 생성/수정: `runtime/layout_geometry.py`
- 수정: `runtime/status_window.py`
- 확인: `runtime/layouts.py`
- 생성/수정: `tests/test_layout_editor.py`
- 수정: `tests/test_status_window.py`

#### 완료 기준

- node를 멀리 드래그해도 viewport가 자동으로 재정렬되지 않는다.
- zoom/pan 상태는 rerender 후에도 유지된다.
- 사용자는 레이아웃 캔버스를 직접 이동할 수 있다.
- 기존 preview/persist 정책은 유지된다.

#### 구현 메모

- UI 전용 viewport 계산은 `runtime/layouts.py`에 섞지 않고 `runtime/layout_geometry.py`에 둔다.
- `layout_bounds()`는 전체 scene 크기 계산에는 써도 되지만, viewport 원점을 강제로 다시 맞추는 용도로는 쓰지 않는다.

---

### P4. 편집 상태와 publish 흐름 정리

#### 목표

편집기 상태를 구조화하고, render/publish 경계를 명확히 해 유지보수성을 높인다.

#### 세부 작업

- 편집기 내부 상태를 명시적으로 분리한다.
  - `draft_layout`
  - `selected_node_id`
  - `viewport`
  - `drag_state`
  - `editing_lock_state`
- 캔버스 전체 삭제 후 재생성 방식이 필요한지 점검하고, 가능한 경우 부분 갱신 여지를 남긴다.
- drag preview는 “로컬 렌더 우선”으로 보고, coordinator publish는 현재 계약을 유지하되 필요하면 throttle 후보를 남긴다.
- dialog open/close와 edit mode 종료 시 정리 로직을 한곳으로 모은다.

#### 대상 파일

- 수정: `runtime/layout_editor.py`
- 수정: `runtime/status_window.py`
- 수정: `runtime/layout_dialogs.py`
- 필요 시 수정: `coordinator/client.py`
- 수정: `tests/test_layout_editor.py`
- 수정: `tests/test_coordinator_client.py`

#### 완료 기준

- 편집 상태가 명시적인 자료구조로 관리된다.
- edit mode 종료, dialog 종료, drag 종료 시 정리 동작이 일관된다.
- preview와 persist 흐름을 코드만 읽어도 구분할 수 있다.

---

### P5. 추가 UX 개선

#### 목표

이번 리팩토링에 묻어서 같이 고치면 체감이 큰 항목을 정리한다.

#### 세부 작업

- 레이아웃 탭 상단에 현재 배율, 선택 노드, 편집 락 상태를 짧게 노출한다.
- 선택 노드 정보는 별도 패널로 분리해 monitor topology, physical size, logical size를 읽기 쉽게 보여준다.
- 편집 불가 상태 메시지를 더 구체적으로 바꾼다.
  - 다른 editor가 점유 중인지
  - request pending인지
  - 선택된 노드가 없는지
- 모니터 맵 편집 팝업의 텍스트/버튼 배치를 정리한다.
- 키보드 shortcut 후보를 남긴다.
  - `+`, `-`: zoom
  - `0`: 100%
  - `f`: fit to view
  - `Esc`: edit mode 종료 또는 drag 취소

#### 대상 파일

- 수정: `runtime/layout_editor.py`
- 수정: `runtime/layout_dialogs.py`
- 수정: `runtime/status_view.py`
- 수정: `tests/test_layout_editor.py`
- 수정: `tests/test_layout_dialogs.py`
- 수정 권장: `docs/MANUAL_VALIDATION.md`

#### 완료 기준

- 사용자가 현재 편집 상태를 메시지 문구에 의존하지 않고도 읽을 수 있다.
- 팝업과 레이아웃 탭의 행동이 더 예측 가능하다.

## 권장 구현 순서

1. `P0` 베이스라인 고정
2. `P1` 책임 분리
3. `P2` 탭 구조와 레이아웃 영역 확대
4. `P3` viewport 도입
5. `P4` 편집 상태 정리
6. `P5` 추가 UX 개선

이 순서를 권장하는 이유는 다음과 같다.

- `P1` 없이 `P3`부터 들어가면 `status_window.py` 안에서 변화 범위가 너무 커진다.
- `P2`와 `P3`는 사용자가 체감하는 개선이 크지만, 먼저 파일 경계를 정리해 두어야 구현과 테스트가 덜 꼬인다.
- `P4`와 `P5`는 핵심 구조가 자리 잡은 뒤에 들어가는 편이 낫다.

## 권장 PR 단위

### PR 1. 테스트 가드레일 + 파일 분리

- 범위: `P0`, `P1`
- 목표: 동작은 유지하고 구조만 나눈다.

### PR 2. Notebook 기반 UI 재배치

- 범위: `P2`
- 목표: 카테고리화와 화면 면적 확보

### PR 3. 레이아웃 편집기 viewport 도입

- 범위: `P3`
- 목표: zoom/pan, 자동 평면 이동 제거

### PR 4. 상태 정리와 UX 마감

- 범위: `P4`, `P5`
- 목표: 상태 모델 정리, 메시지와 팝업 polish, 문서 업데이트

## 테스트 계획

### 자동 테스트

우선 검증 대상:

- `tests/test_status_view.py`
- `tests/test_layout_editor.py`
- `tests/test_layout_dialogs.py`
- `tests/test_status_window.py`
- `tests/test_coordinator_client.py`
- `tests/test_layouts.py`

핵심 회귀 포인트:

- layout drag preview는 release 전까지 preview publish만 수행한다.
- drag release 시 persist가 한 번만 일어난다.
- zoom/pan 이후 rerender가 viewport를 보존한다.
- node drag 중 viewport가 자동 이동하지 않는다.
- edit lock이 없는 상태에서 편집 액션이 차단된다.
- auto-switch / monitor map dialog validation이 유지된다.

### 수동 검증

- 창 크기를 키웠을 때 레이아웃 편집 영역이 함께 확장되는지 확인
- 빈 공간 drag로 pan이 되는지 확인
- wheel zoom이 기대한 중심점 주변으로 동작하는지 확인
- node drag와 pan gesture가 충돌하지 않는지 확인
- 다른 node가 편집 중일 때 편집 잠금 표시가 분명한지 확인
- 모니터 맵 편집 팝업이 선택 노드와 일관되게 연동되는지 확인

## 구현 시 주의사항

- 기존 `coord_client.publish_layout(..., persist=False/True)` 계약은 유지한다.
- `ctx.layout`와 로컬 `draft_layout` 동기화 시점은 drag 중과 drag 외를 구분한다.
- viewport 상태는 `ctx.layout`와 독립적이어야 한다.
- UI 계산용 좌표 변환 로직을 도메인 모델 파일로 다시 밀어 넣지 않는다.
- 기존 한국어 문구 스타일은 유지하되, 더 짧고 분명한 표현으로 다듬는다.

## 완료 정의

다음 조건을 만족하면 이번 리팩토링 계획의 구현이 완료된 것으로 본다.

- GUI가 탭 구조로 재편되었다.
- 레이아웃 편집기가 현재보다 넓고, 사용자가 직접 pan/zoom 할 수 있다.
- node 드래그 시 화면이 자동으로 밀리는 느낌이 사라졌다.
- `status_window.py`가 조립용 쉘 수준으로 축소되었다.
- 관련 테스트가 구조에 맞게 재배치되고 핵심 회귀를 막고 있다.
