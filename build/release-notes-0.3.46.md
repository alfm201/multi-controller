## 라우팅 / Edge Hold

- self dead-edge와 logical-gap에서 hold를 hysteresis 기반 상태 머신으로 유지하도록 정리해, 매우 느린 outward press 중 block/release/block 떨림이 줄어들도록 보강했습니다.
- hold가 살아 있는 동안에는 latched display context를 우선 사용하도록 바꿔, rebound/coerced self event 중에도 blocked display 문맥이 먼저 풀려버리지 않도록 맞췄습니다.
- 이전 샘플과 현재 샘플 사이의 선분이 edge를 가로지르는 경우를 추가로 판정해, 빠른 이동에서 blocked edge나 self-warp가 샘플 사이로 누락되던 경우를 보강했습니다.
- blocked anchor와 hold rect는 각 display의 실제 edge 좌표를 그대로 사용하도록 정리해, 우/하단에서만 2px 안쪽으로 hold되던 오프셋 문제를 제거했습니다.

## Synthetic / Update Fallback

- synthetic mouse move 기록에 per-entry tolerance를 줄 수 있게 바꿔, warp/clip 직후 1px 수준의 잔여 echo만 좁게 억제하도록 보강했습니다.
- `LocalCursorController`가 `SetCursorPos`와 `ClipCursor` 성공 직후 현재 포인터 위치를 synthetic guard에 기록하도록 보완해 clip/warp 이후 잔여 move noise를 더 안정적으로 정리합니다.
- Windows native HTTP fallback은 PowerShell 인자를 직접 붙이지 않고 environment + `-EncodedCommand` 방식으로 넘기도록 바꿔, 헤더/URL/출력 경로 인용 문제에 더 강하게 만들었습니다.

## 검증

- `python -m pytest -q`
- `python -m ruff check .`
- `powershell -ExecutionPolicy Bypass -File scripts\build_windows_installer.ps1 -Version 0.3.46`
