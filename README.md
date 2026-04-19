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

## 사용자 유형별 안내

이 문서는 두 경우를 함께 다룹니다.

- 일반 사용자
  - 설치 프로그램(`MultiScreenPass-Setup-<version>.exe`)으로 앱을 설치해서 사용하는 경우
- 개발자 / 소스 실행 사용자
  - 저장소를 직접 받아 `python main.py`로 실행하거나 테스트/빌드를 수행하는 경우

아래 섹션에서 어느 경우인지 구분해서 보시면 됩니다.

## 일반 사용자용 안내

### 요구 사항

- Windows 10 또는 Windows 11
- 같은 LAN에 연결된 PC

설치형 배포본 사용에는 Python이 필요하지 않습니다.

### 설치

배포된 설치 파일을 실행해 설치합니다.

- 설치 파일: `MultiScreenPass-Setup-<version>.exe`
- 기본 설치 위치: `C:\Program Files\Multi Screen Pass`

언인스톨러는 기본적으로 사용자 설정과 로그를 보존합니다.  
언인스톨 시 체크박스를 선택하면 `%LOCALAPPDATA%\MultiScreenPass\` 아래의 설정, 로그, 업데이트 캐시, tools, backup도 함께 삭제할 수 있습니다.

### 첫 실행

1. 각 PC에서 앱을 실행합니다.
2. 기본 실행은 GUI 모드입니다.
3. 설정 파일에 등록된 노드 정보와 현재 PC의 로컬 IP를 기준으로 self 노드를 자동 식별합니다.
4. 필요하면 노드 관리 탭에서 노드 추가/수정, 그룹 참여, 우선순위 조정을 진행합니다.

### 저장 위치

설치형 배포에서는 `%LOCALAPPDATA%\MultiScreenPass\config\` 아래에 설정이 생성됩니다.

구성 파일:

- `config.json`
  - 노드 목록
  - 앱 설정
  - 단축키
- `layout.json`
  - 노드 배치
  - 자동 경계 전환 설정
  - 노드별 모니터 맵
- `monitor_inventory.json`
  - 실제 감지한 모니터 정보 캐시
- `monitor_overrides.json`
  - 사용자가 보정한 물리 배치

참고:

- `node_id`는 내부 식별자입니다. 기본적으로 UI에는 노출하지 않으며, 설정/동기화/레이아웃 참조에 사용됩니다.
- `priority`는 코디네이터 선발 우선순위입니다. 숫자가 낮을수록 먼저 선발되며, `0` 또는 비워 둔 값은 가장 후순위로 취급됩니다.
- 앱 시작 시 `schema_version` 기준 마이그레이션이 자동 적용됩니다. 과거 설정의 `role` 제거, `node_id` 도입 같은 변경도 이 단계에서 정리됩니다.
- `config.json`을 수동으로 수정한 경우에도 시작 시 검증이 수행되며, 포트/우선순위 숫자 형식과 IP 주소(`x.x.x.x`, 각 자리 `0~255`)를 확인합니다.

### 업데이트

- 앱 시작 시 1회 업데이트 확인을 수행합니다.
- `자동 업데이트 확인`을 켜면 매일 0시에 최신 릴리스를 확인합니다.
- 자동 확인(`startup`, `auto`)은 최대한 조용하게 동작하며, 새 버전이 있어도 트레이 토스트를 따로 띄우지 않습니다.
- 새 버전이 있으면 설정 탭과 업데이트 전용 배너에서 설치를 진행할 수 있습니다.
- 원격 노드에도 업데이트 명령을 전달할 수 있으며, 요청자는 완료/실패/업데이트 없음 같은 핵심 결과만 받습니다.
- 여러 노드가 동시에 조회를 요청하면 coordinator가 조회를 묶어서 1회만 외부에 확인하고 결과를 fan-out 합니다.
- 여러 노드가 같은 설치 파일을 필요로 하면 coordinator가 다운로드 작업을 coalesce하고, 준비된 설치 파일은 LAN 공유로 순차 전송됩니다.
- 자동 업데이트/원격 업데이트/그룹 다운로드는 타임아웃과 request id를 기준으로 추적되며, 응답이 없으면 한국어 경고 메시지로 안내합니다.

### 노드 그룹 참여

- 노드 관리 탭에서 `그룹 참여`를 통해 다른 노드의 IP로 현재 그룹에 합류할 수 있습니다.
- 참여 과정은 비동기로 진행되며, 목록 동기화와 연결 재구성이 함께 이뤄집니다.
- 노드 비고 변경, 노드 목록 변경은 coordinator를 통해 전체 노드에 동기화됩니다.
- 노드 목록 변경은 revision 기반으로 보호되며, 오래된 스냅샷으로 저장을 시도하면 최신 상태로 다시 동기화한 뒤 재시도를 유도합니다.
- 앱 시작 후 또는 실행 중 self IP가 명확하게 변경되면 로컬 설정과 그룹 노드 정보가 함께 갱신됩니다. 애매한 경우에는 자동 전환하지 않고 경고만 표시합니다.

### GUI 구성

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

### 자동 경계 전환과 모니터 보정

- 실제 Windows 모니터 좌표와 감지된 모니터 인벤토리를 기준으로 경계 전환을 계산합니다.
- 논리 배치와 실제 물리 배치를 분리해서 관리할 수 있습니다.
- 모니터 맵 편집 결과는 `monitor_overrides.json`에 반영됩니다.

### 커서 복구와 안전 장치

- stale clip 정리
- 예외 종료 시 cleanup hook 실행
- watchdog companion으로 비정상 종료 감시
- 수동 복구 도구 포함
  - `[장애복구용] 마우스 잠금 해제.exe`

## 개발자 / 소스 실행 안내

### 요구 사항

- Windows 10 또는 Windows 11
- Python 3.11+
- 같은 LAN에 연결된 PC

Python 3.11+ 요구 사항은 소스 실행, 개발, 테스트, 빌드 환경 기준입니다.

### 개발 환경 설치

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

### 소스 실행

기본 실행은 GUI 모드입니다.

```bash
python main.py
```

예시:

```bash
python main.py
python main.py --tray
python main.py --console
python main.py --debug
```

### CLI 옵션

- `--tray`: 트레이 모드로 시작
- `--console`: GUI 없이 콘솔 모드로 실행
- `--debug`: 상세 로그 활성화
- `--config <path>`: 다른 설정 파일 사용
- `--active-target <node>`: 시작 시 초기 대상 지정
- `--init-config`: starter config 생성 후 종료
- `--migrate-config`: 기존 단일 config를 split config 구조로 변환
- `--validate-config`: 현재 설정 검증
- `--force`: `--init-config`, `--migrate-config`에서 기존 파일 덮어쓰기 허용
- `--status-interval <sec>`: 주기 상태 로그 간격 조정, `0`이면 비활성화
- `--diagnostics`: Windows / DPI / 권한 진단 출력
- `--layout-diagnostics`: 레이아웃 / 모니터 진단 출력

개발 환경에서는 repo 아래 `config/`를 우선 사용합니다.

### 설정 예시

`config.json` 예시:

```json
{
  "schema_version": 3,
  "nodes": [
    {
      "node_id": "5f4d2c66-3d9b-4bc8-8a9c-4d5a0f2f6a1b",
      "name": "A",
      "ip": "192.168.0.10",
      "port": 45873,
      "note": "메인 PC",
      "priority": 1
    },
    {
      "node_id": "8b1c9f12-7c87-4cf9-8e91-7ec7e6b2a5fb",
      "name": "B",
      "ip": "192.168.0.11",
      "port": 45873,
      "note": "서브 PC",
      "priority": 0
    }
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

### 프로젝트 구조

현재 코드는 역할 기준으로 패키지를 나눠 관리합니다.

- `app/`
  - 부트스트랩, 설정 로드/저장, UI, 로깅, 업데이트, 앱 메타데이터
- `control/`
  - 코디네이터, 라우팅, 런타임 상태 조립, 상태 투영
- `platform/`
  - Windows 훅, 입력 캡처/주입, 커서/클립보드 보조
- `transport/`
  - peer 연결, 프레임, 핸드셰이크, 전송 계층
- `model/`
  - 공통 이벤트, 레이아웃/디스플레이/모니터 모델

### 테스트

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

### 빌드

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
