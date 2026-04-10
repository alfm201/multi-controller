# multi-controller 수동 검증 체크리스트

이 문서는 자동 테스트로 모두 대체하기 어려운 Windows 실환경 검증 절차를 정리한 체크리스트입니다.

권장 순서:

1. `python main.py --diagnostics` 로 현재 장비 상태를 먼저 기록
2. 필요하면 `python main.py --layout-diagnostics` 로 해석된 레이아웃과 모니터 맵도 함께 기록
3. 같은 설정 파일로 controller/target 두 장비를 실행
4. 기본 제어권 전환 확인
5. 혼합 DPI / 멀티 모니터 검증
6. 관리자 권한 앱 상호작용 검증
7. coordinator 장애 및 장시간 soak 검증

## 사전 준비

- 두 장비 이상이 같은 LAN에 연결되어 있어야 합니다.
- 모든 장비는 같은 `config.json`을 사용해야 합니다.
- 검증 결과는 장비 이름, 해상도, Windows 배율, 관리자 권한 여부와 함께 기록하는 것을 권장합니다.
- 빠른 재현용 예제는 `examples/configs/` 아래 설정 파일을 참고할 수 있습니다.

기본 기록 항목:

- 장비명
- Windows 버전
- 주 모니터 해상도
- 보조 모니터 해상도와 배치
- 각 모니터 배율
- `python main.py --diagnostics` 출력
- `python main.py --layout-diagnostics` 출력
- 기본 GUI 실행 또는 `--tray` 사용 여부
- GUI 탭 사용 여부와 `레이아웃` 탭의 배율/viewport 상태

## 1. 기본 연결 검증

목표:
- peer 연결
- coordinator 선출
- target 전환
- lease 기반 active/pending 상태

절차:

1. 장비 A에서 `python main.py --node-name A`
2. 장비 B에서 `python main.py --node-name B`
3. GUI 또는 `Ctrl+Alt+Q` / `Ctrl+Alt+E`로 target 전환
4. 필요하면 `Ctrl+Alt+Esc`로 로컬 입력 캡처가 즉시 중지되는지도 확인
5. coordinator, active target, peer 연결 상태가 기대대로 보이는지 확인

기대 결과:

- 온라인 노드 중 가장 작은 `node_id`가 coordinator로 표시됨
- target 선택 시 `pending -> active` 전환이 보임
- target 측 로그에 허용 controller가 반영됨
- 선택 해제 시 lease가 비워짐

## 2. 혼합 DPI / 멀티 모니터 검증

권장 조합:

- A 장비: 100% 배율 단일 모니터
- B 장비: 150% 또는 175% 배율
- 가능하면 한 장비는 좌측 보조 모니터가 있어 virtual screen `left < 0` 환경 포함

절차:

1. 각 장비에서 `python main.py --diagnostics` 실행
2. `dpi_awareness_mode`, `primary_screen`, `virtual_screen` 값을 기록
3. controller 장비에서 화면 네 구석과 중앙으로 마우스 이동
4. target 장비에서 포인터가 같은 상대 위치로 이동하는지 확인
5. 주 모니터와 보조 모니터 경계 근처를 천천히 왕복
6. 음수 좌표 영역이 있는 모니터 배치라면 좌측/상단 끝점도 확인

체크 포인트:

- 포인터가 반대쪽 장비에서도 같은 상대 위치에 도달하는가
- 모니터 경계에서 갑작스러운 점프가 없는가
- 클릭 위치가 커서 위치와 크게 어긋나지 않는가
- 스크롤과 드래그 중 좌표 튐이 없는가

실패 시 같이 기록할 것:

- 어느 장비가 controller였는지
- 양쪽 `--diagnostics` 출력
- 어긋난 위치가 중앙인지, 모서리인지, 보조 모니터 경계인지

## 3. 논리/물리 모니터 맵 및 드래그 연속성 검증

목표:
- 논리 모니터 배치와 물리 모니터 배치가 다른 경우에도 기대한 방향으로 전환되는지 확인
- 마우스 버튼을 누른 채 경계를 넘어갈 때 handoff가 자연스럽게 이어지는지 확인

권장 조합:

- 한 장비 이상에서 논리 배치와 물리 배치를 일부러 다르게 설정
- 예: 논리 `1x6`, 물리 `3x2`

절차:

1. `레이아웃` 탭으로 이동해 `편집 모드`를 켠다
2. 마우스 휠로 확대/축소, 빈 공간 drag로 pan, `맞춤` / `100%` / `초기화` 버튼이 기대대로 동작하는지 먼저 확인한다
3. 대상 PC를 선택한 뒤 `모니터 맵 편집`을 열어 논리/물리 배치를 다르게 입력한다
4. `검증`으로 문법과 display id 일치 여부를 먼저 확인한다
5. `적용` 후 다른 장비 GUI에도 모니터 경계 표시가 즉시 반영되는지 본다
6. PC 타일을 drag해도 viewport가 자동으로 밀리지 않고, 사용자가 옮긴 view가 유지되는지 확인한다
7. 논리 배치의 좌측하단, 상단, 우측 경계를 천천히 넘어가며 어떤 PC/모니터로 이어지는지 확인한다
8. 창 제목 표시줄 드래그, 파일 드래그, 영역 선택처럼 버튼 홀드 상태로 같은 경계를 넘겨 본다
9. 경계를 넘은 직후 즉시 반대 방향으로 떨리듯 되돌아가지 않는지 확인한다
10. 필요하면 `Esc`로 drag 취소 또는 편집 모드 종료가 기대대로 동작하는지 확인한다

체크 포인트:

- 논리 배치 기준으로 넘은 경계가 물리 배치 기준의 인접 모니터/인접 PC로 해석되는가
- viewport를 직접 옮긴 뒤에도 다음 drag/zoom 동작이 일관적인가
- node drag 중 화면이 자동으로 재정렬되지 않는가
- handoff 직후 포인터가 목적지 모니터 안쪽에 자연스럽게 재배치되는가
- 드래그 중에도 버튼이 풀리지 않고 계속 이어지는가
- 같은 경계에서 빠르게 왕복해도 즉시 역전환 루프가 생기지 않는가
- 편집 중인 노드가 있을 때 다른 노드에서는 편집 모드가 잠기는가

실패 시 같이 기록할 것:

- 논리 모니터 맵
- 물리 모니터 맵
- 어떤 경계에서 기대와 다른 이동이 발생했는지
- 버튼 홀드 여부와 사용한 앱 종류

## 4. 관리자 권한 앱 상호작용 검증

목표:
- 관리자 권한 앱에서 capture/injection 제한이 어떻게 보이는지 확인

권장 조합:

- 일반 권한으로 실행한 `multi-controller`
- 관리자 권한으로 실행한 메모장, PowerShell, 설정 앱 또는 테스트용 관리자 프로그램

절차:

1. 일반 권한으로 `multi-controller` 실행
2. target 장비에서 관리자 권한 앱을 실행
3. controller에서 해당 앱으로 입력 전달 시도
4. 로그에 `PRIVILEGE` 경고 또는 `Access denied` 계열 주입 실패가 나오는지 확인
5. 이후 `multi-controller`도 관리자 권한으로 다시 실행
6. 동일 시나리오 재시도

기대 결과:

- 일반 권한 실행 시 관리자 앱 상호작용 제한 가능성이 로그에 드러남
- 같은 권한 수준으로 실행하면 증상이 완화되거나 재현 양상이 달라짐

## 5. Coordinator 장애 검증

목표:
- coordinator 변경 후 active target 세션 복구

절차:

1. 세 장비 A, B, C를 실행
2. B가 C를 active target으로 잡은 상태를 만든다
3. 현재 coordinator 장비를 종료하거나 네트워크를 분리한다
4. 새로운 coordinator가 선출되는지 확인
5. B가 다시 heartbeat 또는 reclaim으로 세션을 복구하는지 확인

기대 결과:

- stale authorization이 오래 남지 않음
- 새 coordinator 기준으로 lease가 다시 잡힘
- active 상태가 복구되거나, 복구 실패 시라도 명확히 inactive/pending으로 정리됨

## 6. 장시간 Soak 검증

목표:
- 반복 전환과 장시간 heartbeat 중 상태 누수 여부 확인

권장 시간:

- 최소 30분
- 가능하면 2시간 이상

절차:

1. controller와 target을 연결한 채 유지
2. 5분 간격으로 target 전환, 선택 해제, 재선택 반복
3. 중간에 config reload도 1~2회 수행
4. GUI/tray 사용 시 상태 표시가 계속 일관적인지 확인
5. 마지막에 로그에서 예외, stuck key, authorization 꼬임 여부 확인

관찰 항목:

- lease가 이유 없이 사라지지 않는가
- coordinator 변경 후 stale controller가 남지 않는가
- 키/마우스 버튼이 눌린 상태로 고착되지 않는가
- config reload 이후 peer 목록과 target 목록이 일관적인가

## 7. 결과 판정

다음 조건을 만족하면 현재 로드맵 범위에서 실환경 검증 완료로 볼 수 있습니다.

- 기본 연결과 target 전환이 정상 동작
- 혼합 DPI / 멀티 모니터 환경에서 상대 좌표 오차가 허용 범위 내
- 관리자 권한 앱 상호작용 제한이 로그로 식별 가능
- coordinator 장애 후 stale authorization 없이 복구
- soak 중 예외나 stuck input 없이 안정적으로 유지

## 후속 기록 템플릿

아래 형식으로 간단히 남기면 추적하기 좋습니다.

```text
검증 일시:
장비 조합:
Windows 버전:
배율/모니터 배치:
diagnostics 요약:
기본 연결:
혼합 DPI 결과:
관리자 앱 결과:
failover 결과:
soak 결과:
비고:
```
