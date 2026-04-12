# multi-controller

`multi-controller`는 같은 LAN 안의 여러 Windows PC 사이에서 키보드와 마우스를 공유하는 프로그램입니다.  
한 번에 하나의 controller만 target을 제어하도록 lease 기반 control plane을 사용하며, 현재 GUI는 `PySide6` 기반으로 동작합니다.

장기 방향은 [ROADMAP.md](C:/Users/User/Desktop/미르/개인/codex/multi-controller/docs/ROADMAP.md)에서 볼 수 있습니다.

## 한눈에 보기

- 같은 네트워크의 여러 PC를 하나의 작업 공간처럼 전환
- GUI에서 PC 배치, 모니터 물리 배치, 노드 목록, 핫키 설정 관리
- 실제 감지된 모니터 정보를 기준으로 모니터 맵 보정
- 시스템 트레이 실행 지원
- 커서 clip 복구용 watchdog / 수동 복구 도구 포함

## 요구 사항

- Windows 10/11
- Python 3.11+
- 같은 LAN에 있는 PC들

## 설치

```bash
python -m pip install -e .[dev]
```

런타임 의존성:

- `pynput`
- `PySide6`

개발용 도구:

- `pytest`
- `pytest-qt`
- `ruff`
- `pyinstaller`

## 빠른 시작

1. [config.json](C:/Users/User/Desktop/미르/개인/codex/multi-controller/config/config.json)을 준비합니다.
2. 각 PC에서 자기 이름에 맞는 `--node-name`으로 실행합니다.
3. 기본 실행은 GUI 모드입니다.

예시:

```bash
python main.py --node-name A
python main.py --node-name B
```

같은 PC에서 여러 인스턴스를 테스트할 때는 기본 포트를 먼저 시도하고, 같은 IP에서 충돌이 있으면 다음 포트로 자동 조정합니다.

## 설정 파일 구조

기본 설정은 `config/` 디렉토리 아래의 split config 구조를 사용합니다.

- 개발 환경에서 `python main.py`로 실행할 때는 repo 아래 `config/`를 사용합니다.
- 배포된 `MultiScreenPass.exe`는 기본적으로 `LocalAppData\\MultiScreenPass\\config\\` 아래에서 설정을 생성하고 관리합니다.

- [config.json](C:/Users/User/Desktop/미르/개인/codex/multi-controller/config/config.json): 노드 목록, 앱 설정, 핫키
- [layout.json](C:/Users/User/Desktop/미르/개인/codex/multi-controller/config/layout.json): PC 배치, 자동 전환 설정
- [monitor_inventory.json](C:/Users/User/Desktop/미르/개인/codex/multi-controller/config/monitor_inventory.json): 실제 감지된 모니터 정보 캐시
- `config/monitor_overrides.json`: 사용자가 저장한 물리 배치 보정

가장 단순한 예시는 아래와 같습니다.

```json
{
  "nodes": [
    {"name": "A", "ip": "192.168.0.10", "port": 45873},
    {"name": "B", "ip": "192.168.0.11", "port": 45873}
  ],
  "settings": {
    "hotkeys": {
      "previous_target": "Ctrl+Alt+Q",
      "next_target": "Ctrl+Alt+E",
      "toggle_auto_switch": "Ctrl+Alt+Z",
      "quit_app": "Ctrl+Alt+Esc"
    }
  }
}
```

모든 노드는 기본적으로 입력을 보내고 받을 수 있습니다. 포트는 기본적으로 고정값 `45873`을 사용하므로, 일반 사용자는 GUI에서 따로 입력하지 않습니다.

```json
{
  "nodes": [
    {"name": "A", "ip": "10.0.0.10", "port": 45873},
    {"name": "B", "ip": "10.0.0.20", "port": 45873}
  ]
}
```

### layout.json 예시

```json
{
  "nodes": {
    "A": {"x": -2, "y": 0, "width": 2, "height": 1},
    "B": {"x": 0, "y": 0, "width": 2, "height": 1}
  },
  "auto_switch": {
    "enabled": true,
    "cooldown_ms": 50,
    "return_guard_ms": 50
  }
}
```

- `nodes`: GUI 레이아웃 탭에서 편집한 PC 배치
- `auto_switch.enabled`: 화면 경계 자동 전환 사용 여부
- `auto_switch.cooldown_ms`: 연속 전환 방지 시간
- `auto_switch.return_guard_ms`: 방금 넘어온 경계로 즉시 되돌아가는 현상 방지 시간

## 실행 모드

기본 실행은 GUI 모드입니다.

```bash
python main.py --node-name A
```

추가 옵션:

- `--tray`: 창을 숨긴 상태로 시스템 트레이에서 시작
- `--console`: GUI 없이 콘솔 모드로 실행
- `--debug`: 상세 디버그 로그 출력
- `--config <path>`: 다른 설정 파일 사용
- `--active-target <node>`: 시작 시 초기 target 지정

예시:

```bash
python main.py --node-name A --tray
python main.py --node-name A --console
python main.py --node-name A --debug
```

`--gui`는 기본 동작과 같습니다.  
즉, `--console`이나 `--tray`를 주지 않으면 GUI가 열립니다.

## 진단 / 설정 유틸리티

### 설정 초기화

```bash
python main.py --init-config
```

### 설정 마이그레이션

```bash
python main.py --migrate-config --config legacy.json
```

### 설정 검증

```bash
python main.py --validate-config
```

### 런타임 진단

```bash
python main.py --diagnostics
python main.py --node-name A --layout-diagnostics
python main.py --node-name A --diagnostics --layout-diagnostics
```

- `--diagnostics`: 권한, DPI, virtual desktop 정보를 출력
- `--layout-diagnostics`: 현재 PC 배치, 모니터 토폴로지, 자동 전환 관련 진단을 출력

## GUI 구성

현재 GUI는 아래 탭으로 구성됩니다.

- `개요`: 현재 대상, 연결 상태, 코디네이터 요약과 노드 목록
- `레이아웃`: PC 배치 편집, 팬/줌, 모니터 맵 편집 진입
- `연결 상태`: 고정 표로 보는 노드 연결 상태
- `노드 관리`: 노드 추가 / 수정 / 삭제 / 직전 저장 복구
- `설정`: 자동 전환 시간과 핫키 설정
- `고급 정보`: 진단 정보와 현재 실행 상태

### 레이아웃 편집

- 편집 권한을 얻은 뒤 PC 타일을 드래그해 배치를 수정합니다.
- 탭에 들어오면 기본적으로 `맞춤`과 같은 view 정렬이 한 번 적용됩니다.
- 배경을 직접 드래그할 때만 평면 view가 이동합니다.
- 선택한 PC에서 `모니터 맵`을 열어 실제 감지된 논리 배치를 기준으로 물리 배치를 보정할 수 있습니다.

### 모니터 맵 편집

- 실제 감지된 모니터만 표시합니다.
- 최초 보드는 감지된 논리 배치 크기에 맞춰 열립니다.
- 행/열 확장은 오른쪽 / 아래쪽만 허용합니다.
- 실제 변경은 마우스를 놓을 때 적용됩니다.

## 기본 핫키

- `Ctrl+Alt+Q`: 이전 온라인 PC로 전환
- `Ctrl+Alt+E`: 다음 온라인 PC로 전환
- `Ctrl+Alt+Z`: 화면 경계 자동 전환 켜기 / 끄기
- `Ctrl+Alt+Esc`: 앱과 트레이 함께 종료

핫키는 GUI의 `설정` 탭에서 바꿀 수 있습니다.

## 자동 전환과 모니터 보정

- Windows의 실제 디스플레이 좌표를 기준으로 현재 커서 위치를 판단합니다.
- self 내부 모니터 이동과 PC 간 이동 모두 실제 모니터 감지 정보를 바탕으로 계산합니다.
- `monitor_overrides.json`에는 사용자가 바꾼 물리 배치만 저장됩니다.
- 실제 감지 결과와 보정 정보가 다르면 GUI에서 차이를 확인할 수 있습니다.

## 커서 복구와 안전장치

프로그램은 커서 clip을 사용하므로, 종료 경로와 복구 도구를 같이 제공합니다.

- 시작 시 stale clip 자동 해제
- 예외 종료 시 cleanup hook 실행
- watchdog companion으로 비정상 종료 감시
- 수동 복구 도구: `[장애복구용] 마우스 잠금 해제.exe`

커서 이동이 제한된 것처럼 느껴질 때는 `[장애복구용] 마우스 잠금 해제.exe`를 실행하면 됩니다.

## 패키징

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_exe.ps1
powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1
```

- `build_windows_exe.ps1`: `MultiScreenPass.exe`, `[장애복구용] 마우스 잠금 해제.exe`, `MultiScreenPassRecoveryWatchdog.exe` 생성
- `build_windows_installer.ps1`: 위 두 exe를 포함한 Inno Setup installer 생성
- 설치형 배포에서는 config를 따로 묶지 않아도 됩니다. 첫 실행 시 설정은 `LocalAppData\MultiScreenPass\config\` 아래에 자동 생성됩니다.

## 테스트

전체 테스트:

```bash
python -m pytest -q
```

린트:

```bash
python -m ruff check .
```

스모크 테스트:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke.ps1
```
