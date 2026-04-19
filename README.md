# Multi Screen Pass

`Multi Screen Pass`는 같은 LAN에 있는 여러 Windows PC 사이에서 키보드와 마우스를 공유하는 앱입니다.  
한 번에 하나의 노드만 입력을 제어하도록 coordinator 기반 제어 평면을 사용하며, GUI는 `PySide6`로 구성되어 있습니다.

자세한 방향성은 [docs/ROADMAP.md](docs/ROADMAP.md)에서 확인할 수 있습니다.

## 주요 기능

- 여러 PC를 하나의 작업 공간처럼 오가며 입력 공유
- 개요 탭에서 노드 상태, 최근 연결, 버전, 모니터 배치 확인
- 레이아웃 탭에서 노드 배치와 모니터 맵 편집
- 노드 관리 탭에서 노드 추가, 수정, 삭제, 그룹 참여
- 설정 탭에서 자동 경계 전환, 백업/로그 정책, 앱 업데이트 확인
- 앱 내부 업데이트 확인 및 설치, 원격 업데이트 요청
- 트레이 모드, 최근 메시지 히스토리, 고급 로그 뷰 제공
- 커서 잠금 복구용 watchdog / 수동 복구 도구 포함

## 요구 사항

- Windows 10 또는 Windows 11
- Python 3.11+
- 같은 LAN에 연결된 PC

## 설치

개발 환경:

```bash
python -m pip install -e .[dev]
```

주요 의존성:

- `PySide6`
- `pynput`

개발 도구:

- `pytest`
- `pytest-qt`
- `ruff`
- `pyinstaller`

## 빠른 시작

1. 각 PC에서 앱을 실행합니다.
2. 기본 실행은 GUI 모드입니다.

예시:

```bash
python main.py
```

## 설정 파일 구조

기본 설정은 split config 구조를 사용합니다.

- 개발 환경에서는 repo 아래 `config/`를 우선 사용합니다.
- 설치형 배포에서는 `%LOCALAPPDATA%\MultiScreenPass\config\` 아래에 설정이 생성됩니다.

구성 파일:

- `config/config.json`
  - 노드 목록
  - 앱 설정
  - 단축키
- `config/layout.json`
  - 노드 배치
  - 자동 경계 전환 설정
  - 노드별 모니터 맵
- `config/monitor_inventory.json`
  - 실제 감지한 모니터 정보 캐시
- `config/monitor_overrides.json`
  - 사용자가 보정한 물리 배치

`config.json` 예시:

```json
{
  "nodes": [
    {"name": "A", "ip": "192.168.0.10", "port": 45873, "note": "메인 PC"},
    {"name": "B", "ip": "192.168.0.11", "port": 45873, "note": "서브 PC"}
  ],
  "settings": {
    "hotkeys": {
      "previous_target": "Ctrl+Alt+Q",
      "next_target": "Ctrl+Alt+E",
      "toggle_auto_switch": "Ctrl+Alt+R",
      "quit_app": "Ctrl+Alt+Esc"
    }
  }
}
```

`layout.json` 예시:

```json
{
  "nodes": {
    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
    "B": {"x": 1, "y": 0, "width": 1, "height": 1}
  },
  "auto_switch": {
    "enabled": true,
    "cooldown_ms": 250,
    "return_guard_ms": 350
  }
}
```

## 실행 옵션

기본 실행은 GUI 모드입니다.

```bash
python main.py
```

추가 옵션:

- `--tray`: 트레이 모드로 시작
- `--console`: GUI 없이 콘솔 모드로 실행
- `--debug`: 상세 로그 활성화
- `--config <path>`: 다른 설정 파일 사용
- `--active-target <node>`: 시작 시 초기 대상 지정
- `--init-config`: starter config 생성 후 종료
- `--migrate-config`: 기존 단일 config를 split config 구조로 변환
- `--validate-config`: 현재 설정 검증
- `--diagnostics`: Windows / DPI / 권한 진단 출력
- `--layout-diagnostics`: 레이아웃 / 모니터 진단 출력

예시:

```bash
python main.py --tray
python main.py --console
python main.py --debug
```

## GUI 구성

- `개요`
  - 요약 카드
  - 노드 목록
  - 최근 연결 / 버전 / 모니터 배치 표시
  - 원격 업데이트 요청
- `레이아웃`
  - 노드 배치 편집
  - 자동 경계 전환 토글
  - 모니터 맵 편집 진입
- `노드 관리`
  - 노드 추가 / 수정 / 삭제
  - 비고 편집
  - 그룹 참여
- `설정`
  - 자동 경계 전환 세부 옵션
  - 백업 / 로그 보관 정책
  - 업데이트 확인 / 설치
- `고급 정보`
  - 앱 로그
  - 로그 레벨 필터

## 업데이트

- 앱 시작 시 1회 업데이트 확인을 수행합니다.
- `자동 업데이트 확인`을 켜면 매일 0시에 최신 릴리스를 확인합니다.
- 새 버전이 있으면 설정 탭과 업데이트 전용 배너에서 설치를 진행할 수 있습니다.
- 원격 노드에도 업데이트 명령을 전달할 수 있습니다.

## 노드 그룹 참여

- 노드 관리 탭에서 `그룹 참여`를 통해 다른 노드의 IP로 현재 그룹에 합류할 수 있습니다.
- 참여 과정은 비동기로 진행되며, 목록 동기화와 연결 재구성이 함께 이뤄집니다.
- 노드 비고 변경은 coordinator를 통해 전체 노드에 동기화됩니다.

## 자동 경계 전환과 모니터 보정

- 실제 Windows 모니터 좌표와 감지된 모니터 인벤토리를 기준으로 경계 전환을 계산합니다.
- 논리 배치와 실제 물리 배치를 분리해서 관리할 수 있습니다.
- 모니터 맵 편집 결과는 `monitor_overrides.json`에 반영됩니다.

## 커서 복구와 안전 장치

- stale clip 정리
- 예외 종료 시 cleanup hook 실행
- watchdog companion으로 비정상 종료 감시
- 수동 복구 도구 포함:
  - `[장애복구용] 마우스 잠금 해제.exe`

## 빌드

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_exe.ps1
powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1
```

생성물:

- `MultiScreenPass.exe`
- `[장애복구용] 마우스 잠금 해제.exe`
- `MultiScreenPassRecoveryWatchdog.exe`
- `MultiScreenPassUpdater.exe`
- `MultiScreenPass-Setup-<version>.exe`

## 테스트

전체 테스트:

```bash
python -m pytest -q
```

정적 검사:

```bash
python -m ruff check .
```

스모크 테스트:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke.ps1
```
