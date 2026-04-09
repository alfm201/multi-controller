"""
config.json 로딩/검증/저장.

탐지 순서 (명시 경로 없을 때):
1. PyInstaller onefile 로 실행된 경우 -> exe 옆의 config.json
2. 소스 배치 레이아웃 -> 프로젝트 루트(= 이 파일의 상위) 옆의 config.json
3. 마지막으로 현재 작업 디렉터리의 config.json

저장 함수(save_config)는 추후 GUI/CLI 에서 config 를 수정/추가/삭제할 때
원자적으로 덮어쓰기 위한 자리 확보 목적.
"""

import json
import logging
import os
import sys
from pathlib import Path


def _candidate_paths(explicit_path=None):
    if explicit_path:
        yield Path(explicit_path)
        return

    # PyInstaller onefile: sys.executable 은 추출된 exe 의 경로
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        yield exe_dir / "config.json"

    # 소스 레이아웃: multi-controller/runtime/config_loader.py -> 프로젝트 루트
    project_root = Path(__file__).resolve().parent.parent
    yield project_root / "config.json"

    # 현재 작업 디렉터리
    yield Path.cwd() / "config.json"


def resolve_config_path(explicit_path=None):
    tried = []
    for candidate in _candidate_paths(explicit_path):
        tried.append(str(candidate))
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "config.json 을 찾을 수 없습니다. 시도한 경로: " + ", ".join(tried)
    )


def load_config(explicit_path=None):
    path = resolve_config_path(explicit_path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    validate_config(data)
    logging.info(f"[CONFIG] loaded from {path}")
    return data, path


def save_config(config, path):
    """원자적 저장 (tmp -> rename). 추후 in-app 편집 기능을 위한 유틸."""
    validate_config(config)
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("config 루트는 객체여야 합니다.")

    default_roles = config.get("default_roles")
    if default_roles is not None and not isinstance(default_roles, list):
        raise ValueError("config.default_roles 는 리스트여야 합니다.")

    nodes = config.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("config.nodes 는 비어있지 않은 리스트여야 합니다.")

    seen_names = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"nodes[{i}] 는 객체여야 합니다.")
        for key in ("name", "ip", "port"):
            if key not in node:
                raise ValueError(f"nodes[{i}].{key} 가 없습니다.")
        name = node["name"]
        if not isinstance(name, str) or not name:
            raise ValueError(f"nodes[{i}].name 은 비어있지 않은 문자열이어야 합니다.")
        if name in seen_names:
            raise ValueError(f"nodes[{i}].name 이 중복됩니다: {name}")
        seen_names.add(name)

        roles = node.get("roles")
        if roles is not None and not isinstance(roles, list):
            raise ValueError(f"nodes[{i}].roles 는 리스트여야 합니다.")

    coord = config.get("coordinator")
    if coord is not None:
        if not isinstance(coord, dict):
            raise ValueError("config.coordinator 는 객체여야 합니다.")
        candidates = coord.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("config.coordinator.candidates 는 리스트여야 합니다.")
        for c in candidates:
            if c not in seen_names:
                raise ValueError(
                    f"coordinator.candidates 의 '{c}' 는 nodes 에 존재하지 않습니다."
                )
