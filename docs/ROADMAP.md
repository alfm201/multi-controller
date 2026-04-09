# multi-controller 장기 로드맵

이 문서는 현재 프로젝트의 중장기 방향과 우선순위를 팀이 함께 볼 수 있도록 정리한 작업 기준 문서입니다.

## 목표

- 같은 LAN 환경에서 여러 Windows 장비 사이에 키보드/마우스 제어권을 안정적으로 넘긴다.
- 한 시점에 하나의 controller만 target을 제어하도록 lease 기반 control plane을 유지한다.
- 사용자는 정적 `config.json`만 관리하고, 노드 탐색은 브로드캐스트 없이 직접 구성 방식으로 유지한다.

## 구현 원칙

- GUI/배포보다 제어권 모델 안정화를 먼저 진행한다.
- 노드 자동 탐지는 넣지 않는다.
- coordinator는 온라인 상태를 기준으로 자동 선출하되, split-brain 완화 장치를 둔다.
- 문서와 주석은 한국어 기준으로 유지한다.

## 단계별 계획

### 1. Control Plane 안정화

목표:
- lease 만료와 heartbeat를 실제 동작 모델로 완성
- target도 coordinator와 control plane 연결 유지
- target이 허가된 controller의 입력만 받도록 보강

완료 상태:
- 완료

주요 결과:
- `ctrl.claim`, `ctrl.release`, `ctrl.heartbeat`, `ctrl.grant`, `ctrl.deny`, `ctrl.lease_update` 동작
- lease TTL 3000ms, heartbeat 1초
- router의 `inactive / pending / active` 상태 분리
- grant 수신 후에만 active 전환

### 2. Failover 및 split-brain 완화

목표:
- coordinator 장애 후 세션 복구
- 오래된 coordinator 메시지 무시
- leader 전환 중 stale authorization 제거

완료 상태:
- 완료

주요 결과:
- online 노드 중 가장 작은 `node_id`를 coordinator로 자동 선출
- coordinator 변경 시 pending target 재-claim, active target 재-heartbeat
- `coordinator_epoch` 추가
- stale coordinator / stale epoch frame 무시

### 3. 운영 가시성 보강

목표:
- 지금 시스템이 어떤 상태인지 운영 중 바로 알 수 있게 만들기

완료 상태:
- 완료

주요 결과:
- 주기 상태 로그 추가
- coordinator 변경, peer 입출입, router 전이, lease 변경 이벤트 로그 추가

### 4. Windows 입력 품질 개선

목표:
- 해상도나 DPI가 다른 장치 사이에서도 마우스 좌표 오차를 줄이기

완료 상태:
- 부분 완료

주요 결과:
- 마우스 이벤트에 `x_norm`, `y_norm` 추가
- target이 정규화 좌표를 우선 사용하도록 변경

남은 항목:
- 멀티 모니터 정책
- 혼합 DPI 환경 추가 검증
- 관리자 권한 앱 상호작용 점검

### 5. 운영 UX

목표:
- 사용자가 현재 상태를 쉽게 보고 제어할 수 있는 관리 인터페이스 제공

완료 상태:
- 미착수

후보 범위:
- tray 또는 간단한 GUI
- 현재 active target 표시
- 현재 coordinator 표시
- 연결 상태 표시
- 클릭 전환
- config reload

## 현재 우선순위

1. 간단한 Windows tray/GUI 관리 UX
2. 멀티 모니터 및 고DPI 실환경 보정
3. 장시간 soak 테스트와 실제 장애 시나리오 검증

## 범위 밖 항목

아래 항목은 현재 계획 범위에 넣지 않습니다.

- UDP broadcast / multicast 기반 자동 노드 탐지
- 인터넷 경유 TLS
- 사용자 인증과 세밀한 권한 모델
- 완전한 분산 합의 기반 coordinator HA

## 작업 인수인계 메모

- 현재 노드는 정적 `config.json` 기반으로만 구성한다.
- 같은 그룹의 온라인 노드 중 가장 작은 `node_id`가 coordinator다.
- 운영 로그와 테스트는 계속 함께 늘리는 방향을 유지한다.
- 새로운 기능은 가능하면 한국어 문서와 테스트를 같이 추가한다.
