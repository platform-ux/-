"""style.yaml / samples.md 를 읽고 쓰는 얇은 저장소 계층."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).parent / "config"
STYLE_PATH = CONFIG_DIR / "style.yaml"
SAMPLES_PATH = CONFIG_DIR / "samples.md"

DEFAULT_STYLE: dict[str, Any] = {
    "tone": {
        "speech_level": "해요체",
        "closeness": 3,
        "emoji": 1,
        "length": "짧게",
        "energy": 3,
        "persona": "",
    },
    "rules": {"banned": [], "required": [], "extra": ""},
    "generation": {
        "model": "claude-opus-5",
        "candidates": 4,
        "effort": "medium",
        "max_tokens": 4000,
    },
}

STYLE_HEADER = """\
# ============================================================
#  내 말투 설정 (My Voice)
#  - 웹 UI의 "말투 설정" 탭에서 고치면 이 파일에 저장됩니다.
#  - 직접 고쳐도 되고, 서버 재시작 없이 바로 반영됩니다.
#
#  speech_level : 존댓말 | 해요체 | 반말
#  closeness    : 1(정중한 거리) ~ 5(절친)
#  emoji        : 0(안 씀) | 1(가끔) | 2(자주)
#  length       : 짧게(1~2문장) | 보통(2~3문장) | 길게(4문장+)
#  energy       : 1(차분) ~ 5(텐션 높음)
#  effort       : low | medium | high  (높을수록 정교, 대신 느리고 비쌈)
# ============================================================

"""

SAMPLES_HEADER = """<!--
  내가 실제로 썼던 댓글 샘플 모음입니다.
  모델이 "이 사람 말투"를 배우는 가장 강력한 재료예요. 10~30개 정도 쌓으면 확 좋아집니다.

  형식 (웹 UI에서 추가하면 자동으로 이 형식으로 저장됩니다):

  ### 샘플
  [원문]
  (댓글을 달았던 상대방 글. 모르면 통째로 생략 가능)

  [댓글]
  (내가 실제로 쓴 댓글)
-->
"""


class _StyleDumper(yaml.SafeDumper):
    """여러 줄 문자열을 따옴표 대신 블록(|) 형태로 써서 사람이 읽기 좋게 만든다."""


def _str_representer(dumper: yaml.Dumper, data: str):
    style = "|" if "\n" in data.strip() else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_StyleDumper.add_representer(str, _str_representer)


def _merge(base: Any, override: Any) -> Any:
    """override 에 없는 키는 base 값을 그대로 유지하는 얕은 재귀 병합."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    merged = dict(base)
    for key, value in override.items():
        merged[key] = _merge(base.get(key), value) if key in base else value
    return merged


merge = _merge  # 외부에서 임시 오버라이드를 합칠 때 쓴다


def load_style() -> dict[str, Any]:
    if not STYLE_PATH.exists():
        return {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULT_STYLE.items()}
    loaded = yaml.safe_load(STYLE_PATH.read_text(encoding="utf-8")) or {}
    return _merge(DEFAULT_STYLE, loaded)


def save_style(style: dict[str, Any]) -> dict[str, Any]:
    merged = _merge(load_style(), style)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(
        merged,
        Dumper=_StyleDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        indent=2,
    )
    STYLE_PATH.write_text(STYLE_HEADER + body, encoding="utf-8")
    return merged


# ── 샘플 ────────────────────────────────────────────────────

_BLOCK_RE = re.compile(r"^###\s*샘플\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"\[(원문|댓글)\]\s*\n(.*?)(?=\n\[(?:원문|댓글)\]|\Z)", re.DOTALL)


def load_samples() -> list[dict[str, str]]:
    if not SAMPLES_PATH.exists():
        return []
    text = SAMPLES_PATH.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    samples: list[dict[str, str]] = []
    for block in _BLOCK_RE.split(text)[1:]:
        parts = {name: body.strip() for name, body in _SECTION_RE.findall(block)}
        comment = parts.get("댓글", "")
        if comment:
            samples.append({"source": parts.get("원문", ""), "comment": comment})
    return samples


def save_samples(samples: list[dict[str, str]]) -> list[dict[str, str]]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    chunks = [SAMPLES_HEADER]
    for sample in samples:
        block = ["### 샘플"]
        if sample.get("source", "").strip():
            block.append("[원문]\n" + sample["source"].strip())
        block.append("[댓글]\n" + sample["comment"].strip())
        chunks.append("\n".join(block))
    SAMPLES_PATH.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    return samples


def add_sample(source: str, comment: str) -> list[dict[str, str]]:
    samples = load_samples()
    samples.append({"source": source.strip(), "comment": comment.strip()})
    return save_samples(samples)


def delete_sample(index: int) -> list[dict[str, str]]:
    samples = load_samples()
    if 0 <= index < len(samples):
        samples.pop(index)
    return save_samples(samples)
