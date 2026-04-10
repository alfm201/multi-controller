# multi-controller 장기 로드맵

이 문서는 현재 프로젝트의 중장기 방향과 우선순위를 함께 보기 위한 작업 기준 문서입니다.

## 현재 단계 요약

1차 로드맵은 사실상 마무리 단계이고, 2차 로드맵의 6~11번 항목까지 현재 구현으로 완료했습니다.  
3차 로드맵의 12~14번 항목은 로컬 작업 영역에서 완료했고, 다음 단계는 실환경 검증 마감과 운영 안정화입니다.

## 목표

- 같은 LAN 환경에서 여러 Windows 장비 사이에 키보드/마우스 제어권을 안정적으로 넘긴다.
- 한 시점에 하나의 controller만 target을 제어하도록 lease 기반 control plane을 유지한다.
- 사용자는 정적 `config.json` 중심으로 구성을 관리하되, 레이아웃과 자동 전환 설정도 함께 저장할 수 있다.

## 구현 원칙

- GUI와 레이아웃 기능은 기존 lease/control plane 안정성을 해치지 않는 방식으로 확장한다.
- 노드 자동 탐지는 넣지 않는다.
- coordinator는 온라인 상태를 기준으로 자동 선출하되, split-brain 완화 장치를 유지한다.
- 자동 타겟 전환은 controller 측 선택 정책으로 구현하고, target 측은 승인된 controller 입력만 받는 현재 모델을 유지한다.
- Windows 또는 GPU 제어판의 논리 배치와 별도로, 앱 내부의 사용자 정의 배치 모델을 둘 수 있게 설계한다.
- 문서와 주석은 한국어 기준으로 유지한다.

## 1차 로드맵 정리

### 1. Control Plane 안정화

완료 상태:
- 완료

주요 결과:
- `ctrl.claim`, `ctrl.release`, `ctrl.heartbeat`, `ctrl.grant`, `ctrl.deny`, `ctrl.lease_update` 동작
- lease TTL 3000ms, heartbeat 1초
- router의 `inactive / pending / active` 상태 분리
- grant 수신 후에만 active 전환

### 2. Failover 및 split-brain 완화

완료 상태:
- 완료

주요 결과:
- online 노드 중 가장 작은 `node_id`를 coordinator로 자동 선출
- coordinator 변경 시 pending target 재-claim, active target 재-heartbeat
- `coordinator_epoch` 추가
- stale coordinator / stale epoch frame 무시

### 3. 운영 가시성 보강

완료 상태:
- 완료

주요 결과:
- 주기 상태 로그 추가
- coordinator 변경, peer 입출입, router 전이, lease 변경 이벤트 로그 추가

### 4. Windows 입력 품질 개선

완료 상태:
- 부분 완료

주요 결과:
- 마우스 이벤트에 `x_norm`, `y_norm` 추가
- target이 정규화 좌표를 우선 사용하도록 변경
- synthetic 입력 suppression guard 추가
- virtual desktop 기준 멀티 모니터 좌표 정규화/복원 적용
- DPI awareness fallback 경로 추가
- pointer 이벤트마다 최신 virtual desktop bounds 재조회
- privilege/display 진단과 `--diagnostics` 지원

남은 마감 작업:
- 혼합 DPI 환경 추가 검증
- 관리자 권한 앱 상호작용 실환경 수동 검증

### 5. 운영 UX 1차

완료 상태:
- 완료

주요 결과:
- 기본 GUI와 tray 지원
- active target / peer 상태 / config reload / target 해제 지원
- 상태 조회용 UI의 초벌 운영 패널 확보

## 2차 로드맵

### 6. GUI 디자인 및 UX 개선

완료 상태:
- 완료

주요 결과:
- 기존 상태창을 메인 사용자 화면으로 보지 않고 사용자용 기본 화면과 고급 정보 영역으로 분리
- 일반 사용자는 연결 상태, 현재 제어 대상, 쉬운 전환 흐름을 먼저 보게 하고
- coordinator / lease / router state / config 경로는 접을 수 있는 고급 정보로 이동
- 내부 상태 표현을 사용자 친화적인 문구로 재정리

### 7. 기본 GUI 실행과 패키징 UX 정리

완료 상태:
- 완료

주요 결과:
- 기본 실행 시 GUI가 열리도록 변경
- `--console` 옵션 추가
- `--gui`는 호환성 유지용으로 계속 허용
- onefile GUI 배포 시 `--windowed` 권장 방식 문서화

### 8. 경계 기반 자동 타겟 전환 MVP

완료 상태:
- 완료

주요 결과:
- controller 쪽 mouse move 이벤트에서 화면 경계 접근을 감지
- 현재 선택 PC 또는 self PC를 기준으로 다음 인접 PC 계산
- 경계 진입 시 `claim -> grant -> active` 흐름으로 자동 타겟 전환 시도
- self 방향으로 되돌아갈 때는 target 해제로 복귀
- pointer warp와 cooldown을 이용해 기본적인 연속 이동 경험 확보
- 자동 전환은 기본값 off로 두고 GUI에서 켜서 저장할 수 있게 제공

현재 한계:
- 혼합 DPI, 관리자 권한 앱, 실환경 장시간 사용성은 수동 검증이 더 필요하다.

### 9. GUI 기반 가상 2D PC 레이아웃 편집기

완료 상태:
- 완료

주요 결과:
- GUI 캔버스에서 PC 타일을 2D 레이아웃으로 표시
- 편집 모드에서 드래그로 PC 위치 조정
- 저장 버튼 없이 변경 즉시 전체 노드와 `config.json`에 반영
- coordinator 기반 단일 편집 락으로 동시에 한 노드만 편집 가능
- `config.json`의 `layout.nodes`, `layout.auto_switch`로 저장 포맷 확장
- 겹치는 PC 배치 저장 방지
- 저장된 2D 레이아웃을 자동 타겟 전환 규칙 계산에 바로 사용

현재 한계:
- 현재는 grid snap 기반 PC 타일 편집이다.
- 자유 배치보다는 정렬된 그리드 편집에 가깝다.

## 다음 우선순위

1. 4번 항목의 실환경 검증 마감
2. 장시간 soak 테스트와 실제 장애 시나리오 재검증
3. 테스트 공백과 문서 정리 중심의 3차 로드맵 수립

### 10. 논리 모니터 배치와 물리 모니터 배치 분리 지원

완료 상태:
- 완료

목표:
- Windows 또는 그래픽 드라이버가 제공하는 논리적 모니터 배치와 실제 책상 위 물리적 배치를 분리해서 설정할 수 있게 한다.
- 예를 들어 논리적으로는 `1x6`, 물리적으로는 `3x2`인 경우에도 사용자가 기대하는 방향으로 이동할 수 있게 한다.

주요 결과:
- 각 PC마다 `logical layout`과 `physical layout` 개념을 별도로 둠
- raw capture와 OS injection은 기존처럼 논리 좌표를 기준으로 유지
- 경계 판정은 논리 모니터 기준, 다음 target과 다음 display 계산은 물리 배치 기준으로 재해석
- display id 기반 edge graph로 비정형 배치와 빈 칸 배치를 지원
- GUI에서 논리/물리 모니터 맵을 따로 편집하고 검증한 뒤 즉시 반영 가능
- `config.json` 검증 단계에서 논리/물리 display id 불일치, 중복, 빈 구성 등을 차단
- handoff anchor를 목적지 PC의 논리 모니터 안쪽으로 재배치해 기대한 방향의 이동 흐름 유지

### 11. 드래그/창 이동 연속성 보강

완료 상태:
- 완료

목표:
- 프로그램 창 드래그, 영역 선택 드래그, 마우스 버튼 홀드 상태에서도 전환이 자연스럽게 이어지게 한다.

주요 결과:
- 눌린 마우스 버튼 상태를 유지한 채 다음 target으로 handoff
- target 전환 직후 anchor dead-zone과 return guard를 적용해 경계 떨림과 즉시 역전환을 완화
- 레이아웃 자동 전환과 버튼 홀드 handoff 회귀 테스트 추가
- 창 이동, selection drag, drag-and-drop에 필요한 기본 연속성 정책을 GUI와 router 레벨에서 보강

## 3차 로드맵

### 12. 레이아웃/모니터 진단 CLI

완료 상태:
- 완료

목표:
- GUI 없이도 현재 해석된 PC 레이아웃, 논리/물리 모니터 맵, 자동 전환 파라미터를 바로 확인할 수 있게 한다.

주요 결과:
- `--layout-diagnostics` CLI 추가
- 현재 `config.json` 기준으로 해석된 노드 배치, 논리/물리 모니터 맵, auto-switch 설정, 겹침 여부, 노드/display 인접 관계를 JSON으로 출력
- `--diagnostics --layout-diagnostics` 조합으로 런타임 진단과 레이아웃 진단을 함께 출력 가능
- onefile 패키징이나 원격 지원 상황에서도 바로 사용할 수 있는 읽기 전용 진단 흐름 확보

### 13. GUI 자동 전환 세부 파라미터 편집

완료 상태:
- 완료

목표:
- 실환경에서 경계 감도와 handoff 보정 값을 GUI에서 바로 조정하고 즉시 검증할 수 있게 한다.

주요 결과:
- `edge_threshold`, `warp_margin`, `cooldown_ms`, `return_guard_ms`, `anchor_dead_zone`을 GUI 팝업에서 편집 가능
- 편집 락을 가진 노드만 값을 바꿀 수 있게 기존 정책 유지
- 수치 범위 검증 뒤 즉시 반영하는 흐름 추가
- 관련 상태창 단위 테스트 추가

### 14. 검증 자산 및 운영 문서 정리

완료 상태:
- 완료

목표:
- 실제 운영/검증에서 재현 가능한 예제 설정과 반복 가능한 스모크 절차를 남긴다.

주요 결과:
- 단순 선형 배치와 논리/물리 분리 배치를 위한 예제 `config.json` 추가
- 테스트/린트/레이아웃 진단/onefile 빌드를 반복 실행하는 로컬 스모크 스크립트 추가
- README와 수동 검증 문서를 예제 설정, 레이아웃 진단, 스모크 절차 기준으로 정리

## 범위 밖 항목

- UDP broadcast / multicast 기반 자동 노드 탐지
- 인터넷 경유 TLS
- 사용자 인증과 세밀한 권한 모델
- 완전한 분산 합의 기반 coordinator HA
- Windows 또는 GPU 제어판의 실제 모니터 설정을 프로그램이 직접 변경하는 기능

## 작업 인수인계 메모

- 현재 노드는 정적 `config.json` 기반으로만 구성한다.
- 같은 그룹의 온라인 노드 중 가장 작은 `node_id`가 coordinator다.
- 운영 로그와 테스트는 계속 함께 늘리는 방향을 유지한다.
- 기본 GUI는 사용자용 화면을 우선하고, coordinator/lease 같은 내부 상태는 고급 정보로 숨긴다.
- 자동 타겟 전환이 들어가도 lease 기반 승인 모델 자체는 유지한다.
- GUI/레이아웃 기능 확장 시에도 수동 target 전환 경로는 fallback으로 남겨 둔다.
- 논리/물리 레이아웃 분리 기능은 Windows 논리 좌표를 대체하는 것이 아니라, 앱 내부 해석 레이어를 추가하는 방향으로 설계한다.
- 혼합 DPI / 관리자 권한 / failover / soak 실환경 체크리스트는 `docs/MANUAL_VALIDATION.md`에 계속 정리한다.
- 새로운 기능은 가능하면 한국어 문서와 테스트를 같이 추가한다.
