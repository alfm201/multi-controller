# multi-controller

`multi-controller`는 같은 LAN 안의 여러 장비 사이에서 키보드와 마우스 입력을 공유하는 프로그램입니다.  
현재 구현은 Windows 우선이며, lease 기반 control plane을 사용해서 한 시점에 하나의 controller만 target을 제어할 수 있습니다.

장기 방향과 남은 작업은 [docs/ROADMAP.md](/c:/Users/User/Desktop/미르/개인/codex/multi-controller/docs/ROADMAP.md)에서 확인할 수 있습니다.
실환경 점검 절차는 [docs/MANUAL_VALIDATION.md](/c:/Users/User/Desktop/미르/개인/codex/multi-controller/docs/MANUAL_VALIDATION.md)에서 확인할 수 있습니다.

## 현재 구조

- 설정에 들어 있는 모든 노드는 같은 그룹으로 간주합니다.
- 각 노드는 다른 모든 peer와 직접 연결을 시도합니다.
- 기본 역할은 `controller + target` 입니다.
- `roles`는 이제 선택 사항이며, `target` 전용 장치 같은 예외 케이스에만 사용하면 됩니다.
- 정적인 coordinator 우선순위 목록은 더 이상 사용하지 않습니다.
- coordinator는 현재 온라인인 그룹 멤버 중 자동으로 선출됩니다.
- 선출 기준은 현재 온라인 노드 중 `node_id`가 가장 작은 노드입니다.

즉, 기존처럼 “누가 coordinator 후보인지”를 미리 길게 적기보다, 같은 그룹에 있는 노드들이 서로 연결된 상태를 기준으로 리더를 정합니다.

## Lease 동작

- controller는 `ctrl.claim`으로 target 제어권을 요청합니다.
- 현재 선출된 coordinator가 lease를 승인하거나 거절합니다.
- 제어 중인 controller는 `1초`마다 `ctrl.heartbeat`를 보냅니다.
- lease TTL은 `3000ms`입니다.
- target은 `ctrl.lease_update`를 받아 현재 허용된 controller만 입력 주입을 허용합니다.
- coordinator가 바뀌면 client가 변경을 감지해서 `pending` 상태는 재-claim, `active` 상태는 재-heartbeat를 시도합니다.

## Router 상태

- `inactive`: 선택된 target이 없음
- `pending`: target 전환 요청은 했지만 아직 grant를 받지 못함
- `active`: grant를 받아 실제로 입력을 target으로 전달 중

`Ctrl+Shift+Tab`으로 target을 순환 전환할 수 있고, 이제는 `ctrl.grant`를 받은 뒤에만 실제 active 상태로 넘어갑니다.

## 좌표 정책

- 마우스 정규화 좌표는 주 모니터가 아니라 Windows `virtual desktop` 전체 기준으로 계산합니다.
- 왼쪽이나 위쪽에 붙은 보조 모니터처럼 음수 좌표가 있는 환경도 같은 기준으로 복원합니다.
- 프로세스는 가능한 경우 `Per-Monitor DPI Aware V2`로 올라가고, 실패하면 하위 DPI awareness로 순차 fallback 합니다.
- 화면 배치가 바뀌는 상황을 따라가기 위해 capture도 pointer 이벤트마다 현재 virtual desktop bounds를 다시 읽습니다.

## 권한 진단

- 시작 시 현재 프로세스가 관리자 권한인지 로그로 남깁니다.
- 관리자 권한 앱은 Windows integrity/UIPI 제약 때문에 비관리자 프로세스에서 capture 또는 injection이 막힐 수 있습니다.
- OS 주입이 `Access denied` 계열로 실패하면 권한 불일치 가능성을 직접 경고합니다.

## 설치

```bash
python -m pip install -e .[dev]
```

런타임 의존성:

- `pynput`
- `pystray`
- `Pillow`

개발용 도구:

- `pytest`
- `pyinstaller`

## 설정 파일

가장 단순한 `config.json` 예시는 아래와 같습니다.

```json
{
  "nodes": [
    {"name": "A", "ip": "192.168.0.10", "port": 5000},
    {"name": "B", "ip": "192.168.0.11", "port": 5000},
    {"name": "C", "ip": "192.168.0.12", "port": 5000}
  ]
}
```

`target` 전용 장치를 따로 두고 싶다면 이렇게 선택적으로 `roles`를 줄 수 있습니다.

```json
{
  "nodes": [
    {"name": "A", "ip": "10.0.0.10", "port": 5000},
    {"name": "HEADLESS", "ip": "10.0.0.20", "port": 5000, "roles": ["target"]}
  ]
}
```

현재 검증에서 막는 항목:

- 중복된 node name
- 잘못된 role 이름
- 누락되었거나 0 이하인 port
- 존재하지 않는 `--active-target`
- `target` 역할이 아닌 `--active-target`
- 자기 자신을 가리키는 `--active-target`

## 실행 예시

### 같은 PC에서 2개 인스턴스 실행

```bash
python main.py --node-name A --active-target B
python main.py --node-name B --active-target A
```

### 로컬 진단만 출력하고 종료

```bash
python main.py --diagnostics
```

`--diagnostics`는 현재 프로세스의 관리자 권한 상태, DPI awareness 모드, primary/virtual screen bounds를 JSON으로 출력합니다.

### 간단한 상태 창과 함께 실행

```bash
python main.py --node-name A --active-target B --gui
```

### 시스템 tray와 함께 실행

```bash
python main.py --node-name A --tray
```

`--gui`를 주면 현재 coordinator, peer 연결 상태, active target 상태를 보고 버튼 클릭으로 target 전환이나 선택 해제를 할 수 있습니다.
`--tray`를 주면 시스템 tray 아이콘에서 현재 상태를 보고 `Config Reload`, target 전환, 선택 해제, 종료를 빠르게 실행할 수 있습니다.
상태 창의 `Config Reload` 버튼으로 `config.json`을 다시 읽어 peer 목록을 반영할 수도 있습니다.

제한 사항:

- self 노드의 `name`, `ip`, `port`, `roles` 변경은 재시작 없이 반영하지 않습니다.
- reload 후 현재 선택 target이 사라졌거나 `target` 역할이 아니면 해당 선택은 자동 해제됩니다.

### 같은 LAN의 두 PC에서 실행

1. 두 장비에 같은 `config.json`을 둡니다.
2. 각 node의 IP를 실제 LAN 주소로 맞춥니다.
3. 각 장비에서 하나씩 실행합니다.

예:

```bash
python main.py --node-name A --active-target B
python main.py --node-name B
```

OS 입력 주입이 불가능한 환경이라면 target은 크래시하지 않고 logging injector로 폴백합니다.

## Control Frame 종류

- `ctrl.claim`
- `ctrl.release`
- `ctrl.heartbeat`
- `ctrl.grant`
- `ctrl.deny`
- `ctrl.lease_update`

## 패키징

```bash
pyinstaller --onefile main.py
```

생성된 실행 파일 옆 `dist/` 경로에 `config.json`을 같이 두면 됩니다.

## 테스트

```bash
python -m pytest -q
```

