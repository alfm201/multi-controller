# multi-controller 설계 문서

키보드·마우스 입력을 공유하는 네트워크 프로그램.  
단일 실행 파일로 동작하며, 노드 역할(controller / target / coordinator)은 config.json 으로 결정한다.

---

## 역할 모델

| 역할 | 설명 |
|------|------|
| controller | 로컬 키보드·마우스 입력을 캡처해 현재 점유 중인 target 하나로 전송한다. 물리 입력장치가 달린 노드. |
| target | 다른 controller 로부터 받은 입력을 OS 에 주입한다. **물리 입력장치가 없어도 된다** (헤드리스 서버/VM 도 target 이 될 수 있음). |
| coordinator | control plane 만 담당. 어느 controller 가 어느 target 을 점유 중인지 lease 테이블로 관리. 데이터는 중계하지 않는다. `config.coordinator.candidates` 에서 자동 선출. |

### 배치 제약 없음

- 노드 개수에 고정된 상한선은 없다.
- **모든 노드가 controller 가 될 수 있다**. 사람이 앉아있는 자리라면 어디서든 다른 target 을 점유해 제어할 수 있다.
- 한 노드가 `controller` 와 `target` 역할을 동시에 가질 수 있다 (기본값). 이 경우 자기 입력을 다른 target 으로 보내면서 동시에 다른 controller 로부터 입력을 받아 자기 OS 에 주입할 수 있다.
- 순수 controller (입력만 보냄), 순수 target (입력만 받음, 헤드리스 가능), 순수 coordinator (control plane 전담) 모두 지원된다.
- 여러 controller 가 서로 다른 target 을 **동시에 독립적으로** 점유해 제어할 수 있다. coordinator 의 lease 가 "한 target 당 한 controller" 규칙을 유지한다.

---

## 두 개의 Plane

### Data Plane

`controller → target` 직접 TCP.  
coordinator 를 경유하지 않는다.

```
[controller] ──── TCP ────▶ [target]
  InputCapture                InputSink
  InputRouter                OSInjector (pynput) → 실제 OS 주입
```

### Control Plane

`controller ↔ coordinator` TCP (PeerConnection 재사용).  
누가 어느 target 을 점유 중인지, lease 의 획득·유지·반환을 관리한다.

```
[controller] ── ctrl.claim ──▶ [coordinator]
[controller] ◀─ ctrl.grant ── [coordinator]
[controller] ── ctrl.heartbeat ▶ [coordinator]
[controller] ── ctrl.release ──▶ [coordinator]
```

coordinator 는 config.coordinator.candidates 리스트에서 우선순위 순으로 자동 선출된다.

---

## 모듈 구조 및 파일별 책임

```
multi-controller/
├── main.py                      # 조립·수명주기만. 로직 없음.
├── config.json
│
├── runtime/
│   ├── config_loader.py         # CONFIG_PATH 탐지(onefile 지원), load/validate/save
│   ├── self_detect.py           # getaddrinfo 전용 self 탐지 (외부 probe 없음)
│   └── context.py               # NodeInfo, RuntimeContext 값 객체
│
├── network/
│   ├── frames.py                # wire 포맷: line-delimited JSON + 팩토리
│   ├── handshake.py             # 연결 수립 시 HELLO 송수신
│   ├── peer_connection.py       # 단일 TCP 소켓 양방향 래퍼 (send_lock + recv_thread)
│   ├── peer_registry.py         # node_id → PeerConnection, thread-safe
│   ├── peer_server.py           # accept 루프 + HELLO 핸드셰이크 → registry.bind
│   └── peer_dialer.py           # per-peer dial 루프 + retry backoff → registry.bind
│
├── network/dispatcher.py        # kind 기반 프레임 분기 (input sink / control handler)
│
├── routing/
│   ├── router.py                # InputRouter: capture queue → active target 하나로만 전송
│   └── sink.py                  # InputSink: 수신 이벤트 → OSInjector 에 위임
│
├── injection/
│   ├── os_injector.py           # OSInjector 인터페이스 + pynput/logging 구현 (target 만 필요)
│   └── key_parser.py            # wire 문자열 → pynput Key/Button 역변환 (pynput 필요)
│
├── coordinator/
│   ├── protocol.py              # ctrl.* 메시지 팩토리
│   ├── election.py              # coordinator 선출 (priority 기반 정적 선출)
│   ├── service.py               # CoordinatorService: claim/grant/deny/release 처리
│   └── client.py                # CoordinatorClient: claim 전송, grant 수신 → router 갱신
│
├── capture/
│   └── input_capture.py         # pynput 기반 로컬 키보드·마우스 캡처
│
├── core/
│   └── events.py                # 입력 이벤트 팩토리 (wire 직렬화와 분리)
│
└── utils/
    └── logger_setup.py
```

---

## 핵심 설계 결정

### 1. PeerConnection: 양방향 소켓 재사용

기존 구조에서는 `accept` 로 들어온 소켓을 수신 전용으로만 사용했다.  
그 결과 "A가 먼저 실행되고 B가 3초 뒤 실행되면 B→A 는 즉시 연결되지만  
A→B 는 다음 retry tick 까지 지연된다" 문제가 있었다.

**해결책**: `PeerConnection` 은 소켓의 방향(inbound/outbound)을 구분하지 않는다.  
`send_frame()` 은 어떤 방향에서 만들어진 소켓에든 즉시 쓸 수 있다.

```
B가 A에 dial → A.server 가 accept → PeerConnection(peer='B')
→ A.registry.bind('B', conn)
→ A 는 이 순간부터 conn.send_frame() 으로 B에게 즉시 송신 가능
```

### 2. PeerRegistry: first-to-bind wins

두 노드가 동시에 서로 dial 하면 (dual-dial) 4개의 소켓이 생성된다.  
`PeerRegistry.bind()` 는 thread-safe 하게 "먼저 bind 한 쪽이 이긴다" 규칙을 적용한다.

- 진 쪽은 즉시 소켓 close.
- close 가 상대방 소켓의 EOF 를 유발해 짧은 churn 이 발생할 수 있으나,  
  다음 dial 주기에 재연결로 자기회복된다.
- 기동 직후 ~1ms 의 로그 노이즈. 기능 정합성에는 영향 없음.

향후 deterministic tie-break(큰 node_id 가 dial) 을 도입해 churn 을 완전히 제거할 수 있다.

### 3. fanout_loop 제거 → InputRouter

이전 구조는 캡처된 모든 이벤트를 연결된 모든 peer 에게 복사(broadcast) 했다.  
이는 "특정 target 하나를 제어한다" 는 최종 요구사항과 맞지 않는다.

`InputRouter` 는 `active_target_id` 하나만 유지한다.  
이벤트는 그 target 에 해당하는 `PeerConnection` 하나에만 전달된다.

```python
router.set_active_target('tgt05')   # 이후부터 tgt05 로만 전송
router.clear_active_target()        # 드롭 모드 (아무것도 전송 안 함)
```

### 4. tcp_receiver 직접 출력 제거 → InputSink → OSInjector

수신 경로와 처리 경로를 분리했다.

```
PeerConnection._recv_loop
  → FrameDispatcher.dispatch()
    → InputSink.handle(peer_id, event)
      → OSInjector.inject_key / inject_mouse_* / ...   ← 실제 OS 호출
```

`InputSink` 는 peer 별 "어떤 키/버튼이 눌린 상태인지" 만 추적하고, 실제 OS
호출은 `injection.OSInjector` 구현체에 위임한다.  
- `PynputOSInjector` : 프로덕션. `pynput.keyboard.Controller` / `pynput.mouse.Controller` 로 실제 주입.
- `LoggingOSInjector` : 테스트·dry-run 용. `[INJECT ...]` 로 로그만 남긴다.

이 구조 덕분에 sink 자체는 pynput 에 직접 의존하지 않는다. pynput 이 없거나
OS 가 거부하면(`headless Linux`, `Wayland`, 권한 미허가) `main.py` 가 자동으로
`LoggingOSInjector` 로 fallback 한다 — 노드가 크래시하지 않고 수신 로그만 남는 상태로 계속 동작.

### 5. SenderWorker 재해석 → peer_* 3종

| 옛 역할 | 새 담당 |
|---------|---------|
| 연결 수명주기 관리 | `PeerDialer` (dial loop + backoff) |
| 소켓 I/O | `PeerConnection` (send_lock + recv_thread) |
| 이벤트 라우팅 | `InputRouter` (active target 조회 → send_frame) |
| 연결 조회 | `PeerRegistry` (node_id → PeerConnection) |

### 6. Coordinator: Data Plane 과 완전 분리

coordinator 는 **어떤 데이터도 중계하지 않는다**.  
실제 입력 이벤트는 controller ↔ target 직접 TCP 로 흐른다.

coordinator 의 역할은 lease 테이블 관리뿐이다:
- `ctrl.claim` / `ctrl.release` / `ctrl.heartbeat` 수신
- `ctrl.grant` / `ctrl.deny` 응답

coordinator 가 죽어도 **이미 grant 받은 controller 는 계속 target 으로 입력을 보낼 수 있다**.  
(v2 에서 lease 만료 후 자동 해제 로직 추가 예정)

### 7. CONFIG_PATH 탐지 (PyInstaller onefile 지원)

```python
# 탐지 순서 (explicit 경로 없을 때)
1. sys.frozen=True  → exe 와 같은 디렉터리의 config.json
2. 소스 레이아웃  → 프로젝트 루트의 config.json
3. CWD            → config.json
```

추후 GUI / CLI 에서 config 를 편집할 경우 `runtime.config_loader.save_config()` 를 사용한다.  
원자적 tmp-then-rename 방식으로 덮어쓴다.

### 8. self 탐지: getaddrinfo 전용

외부 UDP probe (8.8.8.8, 1.1.1.1) 를 제거했다.  
네트워크 차단·방화벽 환경에서도 부작용 없이 동작한다.

같은 PC 다중 인스턴스 테스트 시:

```bash
python main.py --node-name A --active-target B
python main.py --node-name B --active-target A
```

---

## config.json 스키마

```json
{
  "default_roles": ["controller", "target"],  // (선택) node.roles 생략 시 사용할 폴백
  "nodes": [
    {
      "name": "A",           // node_id. 전체에서 유일해야 함
      "ip": "192.168.1.10",  // listen IP (자기 자신) 또는 연결 대상 IP
      "port": 5000,
      "roles": ["controller", "target"]   // 생략 가능. 생략 시 default_roles → ["controller","target"]
    }
  ],
  "coordinator": {
    "candidates": ["A", "B"] // 우선순위 순서. 첫 번째가 coordinator.
  }
}
```

`roles` 필드 설명:
- `controller` : 로컬 입력 캡처 + InputRouter 활성화 (pynput 필요)
- `target`     : InputSink 활성화 (수신 후 주입). 물리 입력장치 불필요
- `coordinator` : 해당 노드가 coordinator 로 선출되었을 때 control plane 서비스 활성화  
  (현 단계에서는 `coordinator.candidates` 리스트로 선출되므로 roles 에 명시하지 않아도 동작)

**모든 조합이 유효하다**. 예:
- `["controller", "target"]` — 기본. 자기 입력을 보내면서 남의 입력도 받음
- `["controller"]` — 키보드·마우스 있는 순수 조작자. 자기 OS 에는 어떤 입력도 주입되지 않음
- `["target"]` — 헤드리스 서버. pynput 없어도 동작하며, 다른 controller 로부터 입력만 받음
- `["coordinator"]` — lease 관리만 담당하는 경량 노드

---

## 연결 수립 흐름

```
[A 기동]                       [B 기동 (3초 후)]
PeerServer.listen(:5000)
                               PeerServer.listen(:5001)
                               PeerDialer → connect(A:5000)
accept()  ◀────── TCP ─────────
send HELLO('A') ──────────────▶
          ◀────── HELLO('B') ──
PeerRegistry.bind('B', conn)
                               PeerRegistry.bind('A', conn)
[즉시 양방향 사용 가능]
```

PeerDialer 는 이미 registry 에 살아있는 conn 이 있으면 dial 을 건너뛴다.

---

## 단계별 구현 로드맵

| 단계 | 내용 | 상태 |
|------|------|------|
| v0 | pynput 캡처, TCP JSON, broadcast fanout | 완료 (구버전) |
| v1 | PeerConnection 양방향, Registry, Router, Sink, Coordinator stub | 완료 |
| v2 | InputSink → OSInjector 위임 + PynputOSInjector 실제 주입 | 완료 |
| v4 | Ctrl+Shift+Tab 핫키로 active target 순환 | **현재** |
| v3 | CoordinatorService lease 만료 타이머 + heartbeat 주기 전송 | 미착수 |
| v5 | coordinator liveness-aware 선출 (상위 후보 복귀 시 재선출) | 미착수 |
| v6 | 동적 config 편집 (GUI / tray icon) | 미착수 |

---

## 테스트 방법 (v4 현재)

### 같은 PC 2-인스턴스

```bash
# 터미널 1
python main.py --node-name A --active-target B

# 터미널 2
python main.py --node-name B --active-target A
```

A 에서 입력하면 B 의 로그에 `[SINK KEY DOWN]` + `[INJECT KEY DOWN]` 이 출력되고,
B 에서 입력하면 A 의 로그에 동일하게 출력된다.

pynput 이 설치돼 있으면 B 의 실제 텍스트 에디터에 A 에서 친 키가 입력된다
(`[INJECTOR] pynput OS injection enabled` 로그 확인).
pynput 이 없거나 OS 가 거부하면 `[INJECTOR] pynput unavailable ... using logging injector`
경고 후 노드는 로그-only 모드로 계속 동작한다.

### 핫키로 active target 전환 (v4)

`controller` 역할 노드가 기동되면 자동으로 `Ctrl+Shift+Tab` 핫키가 등록된다.
누를 때마다 config 에 선언된 `target` 역할 peer 를 순서대로 순환한다.
(마지막 target 에서 누르면 첫 번째로 wrap-around.)

```
[HOTKEY] Ctrl+Shift+Tab → cycle active target
...
[HOTKEY] cycle-target matched
[HOTKEY CYCLE] B -> C
[ROUTER ACTIVE] B -> C
```

target 이 하나뿐이면 매번 같은 대상을 유지하며, target 이 없으면
`no target-role peers available` 로그만 남긴다.

coordinator 가 구성돼 있으면 전환 시 `ctrl.claim` 을 먼저 보내고 로컬 router
를 즉시 전환한다(낙관적 전환). coordinator 연결이 끊겨 있어도 로컬 전환은
계속되므로 UI 반응성이 유지된다.

### 다른 PC

config.json 의 nodes IP 를 실제 IP 로 수정 후 각각 실행.  
`--active-target` 생략 시 입력을 보내지 않고 수신만 대기한다.

### 헤드리스 target (물리 입력장치 없는 노드)

config.json 에서 해당 노드의 roles 를 `["target"]` 으로만 지정하면 pynput 이 로드되지 않고
수신·주입만 담당한다. 서버·VM·모니터 없는 박스에서도 동작한다.

```json
{
  "nodes": [
    {"name": "A", "ip": "10.0.0.10", "port": 5000, "roles": ["controller", "target"]},
    {"name": "HEADLESS", "ip": "10.0.0.20", "port": 5000, "roles": ["target"]}
  ],
  "coordinator": {"candidates": ["A"]}
}
```

A 에서 `--active-target HEADLESS` 로 실행하면 A 의 입력이 HEADLESS 로만 흐른다.

### PyInstaller 빌드

```bash
pip install pyinstaller
pyinstaller --onefile main.py
# dist/main.exe 옆에 config.json 을 두면 자동 탐지
```

---

## 알려진 제한 및 향후 과제

- **Controller 의 로컬 입력 suppress 미구현**: controller 노드가 캡처한 키는
  여전히 자기 OS 에도 남는다. 자기 자신이 `controller+target` 역할이면
  자기 입력이 자기 sink 로도 흘러들 수 있다 (루프 위험은 router 쪽 필터로 회피 중).
  pynput `listener.suppress=True` 는 플랫폼 종속성과 전체-억제 특성 때문에
  아직 도입하지 않았다 — v5 이후 검토.
- **v4 핫키의 modifier pass-through**: `Ctrl+Shift+Tab` 의 **Tab** 키 자체는
  트리거로 소비되어 원격 target 에 전달되지 않지만, **Ctrl/Shift** 는 그대로
  흘러간다. 결과적으로 target 측에는 짧은 `Ctrl+Shift` press/release 가 보인다.
  대부분의 에디터에서는 관찰 가능한 부작용이 없지만, `Ctrl+Shift` 조합에 의미
  있는 단축키가 걸린 앱이라면 의도치 않은 동작이 발생할 수 있다. 회피하려면
  target 활성 전환 후 곧장 키 입력을 시작하면 된다. 완전 억제는 `suppress=True`
  도입과 묶여 있으므로 위 항목과 함께 해결될 예정.
- **DPI / 해상도 불일치**: `pynput.mouse.Controller.position` 은 좌표를 그대로
  세팅한다. 두 노드의 DPI·해상도가 다르면 마우스 위치가 어긋날 수 있다.
- **macOS 접근성 권한**: target 노드에서 pynput 으로 주입하려면 `System Settings →
  Privacy & Security → Accessibility / Input Monitoring` 권한이 필요하다.
- **Linux Wayland 미지원**: pynput 는 Xorg 만 검증됨. Wayland 세션에서는
  `PynputOSInjector` 생성이 실패하고 `LoggingOSInjector` 로 fallback 된다.
- **Target 쪽 lease 검증 없음**: 현재 target sink 는 어떤 peer 로부터든 받은
  input 을 그대로 주입한다. coordinator lease 와 sink 를 묶는 것은 v3 의 범위.
- **Coordinator heartbeat 미구현**: lease 만료 없음. coordinator 재시작 후 수동 재-claim 필요.
- **Dual-dial 기동 churn**: 두 노드가 동시에 서로 dial 하면 ~1ms 간 연결 churn 발생 후 자기회복.  
  deterministic tie-break(큰 node_id 가 작은 쪽을 dial) 으로 향후 제거 가능.
- **같은 IP 다중 노드**: 같은 PC 의 여러 노드는 `--node-name` 으로 구분해야 한다.
- **TCP 순서 보장**: key_down/up 순서 보장이 중요하므로 현재 TCP 유지.  
  QUIC/WebSocket 전환은 규모 검증 후 판단.

---

## 의존성

```
pynput     # controller 역할 노드의 캡처 + target 역할 노드의 OS 주입에서 사용
```

- `controller` 역할 노드: `capture/input_capture.py` 가 lazy import.
- `target` 역할 노드: `injection/os_injector.py::PynputOSInjector` 가 생성 시점에 import.
  import 또는 Controller attach 가 실패하면 `LoggingOSInjector` 로 자동 fallback 되므로
  pynput 가 없는 환경에서도 노드는 (수신 로그만 남긴 채) 동작한다.

순수 `coordinator` 노드는 pynput 없이 동작한다. 순수 `target` 노드도 pynput 없이
기동 가능하지만, 그 경우 실제 OS 주입은 일어나지 않고 로그만 남는다.
