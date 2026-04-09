# multi-controller 설계 문서

키보드·마우스 입력을 공유하는 네트워크 프로그램.  
단일 실행 파일로 동작하며, 노드 역할(controller / target / coordinator)은 config.json 으로 결정한다.

---

## 목표 구성

| 역할 | 대수 | 설명 |
|------|------|------|
| controller | ~4 | 실제 키보드·마우스가 달린 사용자 PC. 입력을 캡처해 target 으로 전송 |
| target | ~20–30 | 입력을 수신해 OS 에 주입하는 PC (원격 제어 대상) |
| coordinator | 1 (자동 선출) | control plane 만 담당. 데이터는 중계하지 않음 |

4명이 동시에 서로 다른 target 들을 독립적으로 제어할 수 있어야 한다.

---

## 두 개의 Plane

### Data Plane

`controller → target` 직접 TCP.  
coordinator 를 경유하지 않는다.

```
[controller] ──── TCP ────▶ [target]
  InputCapture                InputSink
  InputRouter               (logging → 나중에 OS injection)
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
│   └── sink.py                  # InputSink: 수신 이벤트 처리 (현재 log, 추후 OS injection)
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

### 4. tcp_receiver 직접 출력 제거 → InputSink 콜백

수신 경로와 처리 경로를 분리했다.

```
PeerConnection._recv_loop
  → FrameDispatcher.dispatch()
    → InputSink.handle(peer_id, event)   ← 여기만 교체하면 됨
```

`InputSink.handle` 은 현재 로깅만 한다.  
OS 레벨 입력 주입(pynput.Controller, SendInput, uinput 등) 으로 전환할 때  
이 파일 하나만 수정하면 된다.

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
  "nodes": [
    {
      "name": "A",           // node_id. 전체에서 유일해야 함
      "ip": "192.168.1.10",  // listen IP (자기 자신) 또는 연결 대상 IP
      "port": 5000,
      "roles": ["controller", "target"]   // 생략 시 기본값: ["controller", "target"]
    }
  ],
  "coordinator": {
    "candidates": ["A", "B"] // 우선순위 순서. 첫 번째가 coordinator.
  }
}
```

`roles` 필드 설명:
- `controller` : 로컬 입력 캡처 + InputRouter 활성화
- `target`     : InputSink 활성화 (수신 후 주입)
- (향후) `coordinator_only` : data plane 비활성화

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
| v1 | PeerConnection 양방향, Registry, Router, Sink, Coordinator stub | **현재** |
| v2 | InputSink → OS 실제 주입 (pynput.Controller / uinput) | 미착수 |
| v3 | CoordinatorService lease 만료 타이머 + heartbeat 주기 전송 | 미착수 |
| v4 | controller UI: 핫키로 active target 전환 | 미착수 |
| v5 | coordinator liveness-aware 선출 (상위 후보 복귀 시 재선출) | 미착수 |
| v6 | 동적 config 편집 (GUI / tray icon) | 미착수 |

---

## 테스트 방법 (v1 현재)

### 같은 PC 2-인스턴스

```bash
# 터미널 1
python main.py --node-name A --active-target B

# 터미널 2
python main.py --node-name B --active-target A
```

A 에서 입력하면 B 의 로그에 `[SINK KEY DOWN]` 등이 출력되고,  
B 에서 입력하면 A 의 로그에 출력된다.

### 다른 PC

config.json 의 nodes IP 를 실제 IP 로 수정 후 각각 실행.  
`--active-target` 생략 시 입력을 보내지 않고 수신만 대기한다.

### PyInstaller 빌드

```bash
pip install pyinstaller
pyinstaller --onefile main.py
# dist/main.exe 옆에 config.json 을 두면 자동 탐지
```

---

## 알려진 제한 및 향후 과제

- **OS 입력 주입 미구현**: `routing/sink.py` 의 `InputSink.handle` 이 현재 로깅만 함.
- **Coordinator heartbeat 미구현**: lease 만료 없음. coordinator 재시작 후 수동 재-claim 필요.
- **Dual-dial 기동 churn**: 두 노드가 동시에 서로 dial 하면 ~1ms 간 연결 churn 발생 후 자기회복.  
  deterministic tie-break(큰 node_id 가 작은 쪽을 dial) 으로 향후 제거 가능.
- **같은 IP 다중 노드**: 같은 PC 의 여러 노드는 `--node-name` 으로 구분해야 한다.
- **TCP 순서 보장**: key_down/up 순서 보장이 중요하므로 현재 TCP 유지.  
  QUIC/WebSocket 전환은 규모 검증 후 판단.

---

## 의존성

```
pynput     # 로컬 입력 캡처 (capture/input_capture.py)
```

표준 라이브러리만으로 네트워크/coordinator 동작.  
OS 주입 단계에서 `pynput.Controller` 또는 플랫폼별 라이브러리 추가 예정.
