# multi-controller

`multi-controller`는 같은 LAN 안의 여러 장비 사이에서 키보드와 마우스 입력을 공유하는 프로그램입니다.  
현재 구현은 Windows 우선이며, lease 기반 control plane을 사용해서 한 시점에 하나의 controller만 target을 제어할 수 있습니다.

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

## 설치

```bash
python -m pip install -e .[dev]
```

런타임 의존성:

- `pynput`

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

