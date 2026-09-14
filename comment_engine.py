"""글을 읽고 '내 말투'로 공감 댓글 후보를 만들어내는 핵심 엔진."""

from __future__ import annotations

import json
import os
from typing import Any

import anthropic

import store

# 후보 댓글 응답 스키마. output_config.format 으로 강제해서 파싱 실패를 없앤다.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reading": {
            "type": "object",
            "properties": {
                "emotion": {"type": "string", "description": "글쓴이의 지금 감정 (한국어 한 문장)"},
                "situation": {"type": "string", "description": "어떤 상황인지 (한국어 한 문장)"},
                "wants": {
                    "type": "string",
                    "description": "글쓴이가 댓글에서 바랄 법한 반응 (한국어 한 문장)",
                },
                "sensitivity": {
                    "type": "string",
                    "enum": ["일반", "민감"],
                    "description": "상실·질병·사고·이별 등 무게 있는 상황이면 '민감'",
                },
            },
            "required": ["emotion", "situation", "wants", "sensitivity"],
            "additionalProperties": False,
        },
        "comments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "실제로 복사해서 붙여넣을 댓글 본문"},
                    "angle": {
                        "type": "string",
                        "description": "이 후보가 잡은 각도를 8자 내외로 (예: 고생 알아주기)",
                    },
                },
                "required": ["text", "angle"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reading", "comments"],
    "additionalProperties": False,
}

_LENGTH_GUIDE = {
    "짧게": "1~2문장. 군더더기 없이.",
    "보통": "2~3문장.",
    "길게": "4문장 이상. 다만 늘어지지 않게.",
}
_EMOJI_GUIDE = {
    0: "이모지와 이모티콘을 쓰지 않는다.",
    1: "이모지는 꼭 필요할 때만 하나 정도. 없어도 된다.",
    2: "이모지를 편하게 쓴다. 다만 한 댓글에 2개를 넘기지 않는다.",
}


def _tone_block(tone: dict[str, Any]) -> str:
    closeness = int(tone.get("closeness", 3))
    energy = int(tone.get("energy", 3))
    emoji = int(tone.get("emoji", 1))
    lines = [
        f"- 말투: {tone.get('speech_level', '해요체')}",
        f"- 거리감: 5점 만점에 {closeness}점 "
        f"({'정중하고 조심스럽게' if closeness <= 2 else '편하지만 예의는 지키게' if closeness == 3 else '친한 사이처럼 편하게'})",
        f"- 텐션: 5점 만점에 {energy}점 "
        f"({'차분하고 담백하게' if energy <= 2 else '적당한 리액션' if energy == 3 else '리액션 크게'})",
        f"- 이모지: {_EMOJI_GUIDE.get(emoji, _EMOJI_GUIDE[1])}",
        f"- 길이: {_LENGTH_GUIDE.get(str(tone.get('length', '짧게')), _LENGTH_GUIDE['짧게'])}",
    ]
    persona = str(tone.get("persona", "")).strip()
    if persona:
        lines.append(f"- 내 캐릭터:\n{_indent(persona)}")
    return "\n".join(lines)


def _rules_block(rules: dict[str, Any]) -> str:
    lines: list[str] = []
    banned = [str(w).strip() for w in (rules.get("banned") or []) if str(w).strip()]
    if banned:
        lines.append(
            "- 아래 표현은 어떤 경우에도 쓰지 않는다 (변형·띄어쓰기만 바꾼 형태도 금지):\n"
            + "\n".join(f"    · {w}" for w in banned)
        )
    required = [str(w).strip() for w in (rules.get("required") or []) if str(w).strip()]
    if required:
        lines.append(
            "- 아래 표현은 어색하지 않은 선에서 가급적 포함한다:\n"
            + "\n".join(f"    · {w}" for w in required)
        )
    extra = str(rules.get("extra", "")).strip()
    if extra:
        lines.append("- 그 밖의 내 습관:\n" + _indent(extra))
    return "\n".join(lines) if lines else "- (추가 규칙 없음)"


def _samples_block(samples: list[dict[str, str]]) -> str:
    if not samples:
        return (
            "(아직 등록된 샘플이 없다. 위의 말투 설정만으로 최대한 자연스럽게 쓸 것.)"
        )
    chunks = []
    for i, sample in enumerate(samples, 1):
        chunk = [f"<샘플 {i}>"]
        if sample.get("source"):
            chunk.append(f"상대 글: {sample['source']}")
        chunk.append(f"내 댓글: {sample['comment']}")
        chunk.append(f"</샘플 {i}>")
        chunks.append("\n".join(chunk))
    return "\n\n".join(chunks)


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.strip().splitlines())


def build_system_prompt(style: dict[str, Any], samples: list[dict[str, str]]) -> str:
    """말투 설정 + 샘플로 시스템 프롬프트를 조립한다. 요청마다 동일하므로 캐시가 잘 먹는다."""
    return f"""너는 '나'를 대신해서 댓글을 써주는 고스트라이터다.
사용자가 남의 글을 붙여넣으면, 그 글에 내가 직접 단 것처럼 보이는 공감 댓글 후보를 만들어라.

# 1. 먼저 글을 읽는다
댓글을 쓰기 전에 반드시 이걸 먼저 판단한다.
- 글쓴이가 지금 어떤 감정인가
- 어떤 상황에 놓여 있나
- 댓글에서 무엇을 바라고 있나 (알아주기 / 축하 / 그냥 들어주기 / 정보 / 웃음)
글쓴이가 명시적으로 말하지 않은 감정까지 읽되, 근거 없이 지어내지는 않는다.

# 2. 댓글 원칙
- 글에 실제로 적힌 구체적인 한 지점을 반드시 짚는다. 아무 글에나 붙는 댓글은 실패다.
- 요청받지 않은 조언·훈수·해결책 제시는 하지 않는다. 글쓴이가 명시적으로 물었을 때만 한다.
- 내 경험담으로 화제를 가로채지 않는다. 주인공은 글쓴이다.
- 감정을 단정하지 않는다. "~겠다", "~것 같다"처럼 여지를 둔다.
- 후보들은 서로 다른 각도로 접근한다. 시작 문구가 겹치면 안 된다.
- 평가하거나 가르치는 말투를 쓰지 않는다.
- 글쓴이가 쓴 단어를 한두 개 자연스럽게 되받아 쓰면 좋다.

# 3. 무게 있는 글은 톤을 낮춘다
상실·부고·질병·사고·이별·해고처럼 무거운 글이면 sensitivity 를 '민감'으로 판단하고,
아래 말투 설정보다 이 규칙을 우선한다.
- 이모지, 느낌표, 밝은 리액션을 쓰지 않는다
- 짧고 담백하게. 섣불리 위로를 완성하려 들지 않는다
- 상투적인 위로 문구를 피한다

# 4. 내 말투 (이게 제일 중요하다)
{_tone_block(style.get("tone", {}))}

## 반드시 지킬 규칙
{_rules_block(style.get("rules", {}))}

## 내가 예전에 쓴 실제 댓글
아래 샘플의 어미, 문장 길이, 리듬, 감탄사 습관을 그대로 따라 한다.
단, 문장을 통째로 베끼지는 말고 이 글에 맞는 새 문장을 쓴다.

{_samples_block(samples)}

# 5. 출력
정해진 JSON 스키마로만 답한다. comments[].text 에는 설명이나 따옴표 없이
바로 복사해서 붙여넣을 수 있는 댓글 본문만 담는다."""


def check_violations(text: str, style: dict[str, Any]) -> list[str]:
    """생성된 댓글이 금지어를 물고 나왔는지 클라이언트 쪽에서 한 번 더 검사한다."""
    rules = style.get("rules", {})
    normalized = text.replace(" ", "")
    hits = []
    for word in rules.get("banned") or []:
        word = str(word).strip()
        if word and word.replace(" ", "") in normalized:
            hits.append(f"금지어 '{word}' 포함")
    for word in rules.get("required") or []:
        word = str(word).strip()
        if word and word.replace(" ", "") not in normalized:
            hits.append(f"필수 표현 '{word}' 누락")
    return hits


def _client() -> anthropic.Anthropic:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError(
            "ANTHROPIC_API_KEY 가 설정되지 않았습니다. "
            ".env 파일에 키를 넣거나 환경변수로 내보낸 뒤 다시 실행해 주세요."
        )
    return anthropic.Anthropic()


def _call_model(client: anthropic.Anthropic, params: dict[str, Any]):
    """refusal 서버측 폴백을 먼저 시도하고, 베타를 못 쓰면 일반 경로로 떨어진다."""
    try:
        return client.beta.messages.create(
            **params,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except (anthropic.BadRequestError, anthropic.NotFoundError, TypeError):
        return client.messages.create(**params)


def generate(
    post: str,
    *,
    count: int | None = None,
    extra_instruction: str = "",
    style: dict[str, Any] | None = None,
    samples: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """글 하나를 받아 댓글 후보들을 돌려준다."""
    post = post.strip()
    if not post:
        raise ValueError("글 내용이 비어 있습니다.")

    style = style or store.load_style()
    samples = store.load_samples() if samples is None else samples
    gen = style.get("generation", {})

    n = max(1, min(8, int(count or gen.get("candidates", 4))))
    user_parts = [
        "아래 글에 달 댓글을 만들어 줘.",
        f"후보는 서로 다른 각도로 정확히 {n}개.",
        "",
        "<글>",
        post,
        "</글>",
    ]
    if extra_instruction.strip():
        user_parts += ["", "<이번 건 추가 주문>", extra_instruction.strip(), "</이번 건 추가 주문>"]

    params: dict[str, Any] = {
        "model": gen.get("model", "claude-opus-5"),
        "max_tokens": int(gen.get("max_tokens", 4000)),
        # 시스템 프롬프트는 요청마다 같으므로 캐시 해 둔다.
        "system": [
            {
                "type": "text",
                "text": build_system_prompt(style, samples),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": "\n".join(user_parts)}],
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": gen.get("effort", "medium"),
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
    }

    response = _call_model(_client(), params)

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        reason = getattr(detail, "explanation", None) or "모델이 이 글에 대한 응답을 거절했습니다."
        raise RuntimeError(f"댓글을 생성하지 못했습니다: {reason}")

    text = next((b.text for b in response.content if b.type == "text" and b.text), None)
    if not text:
        raise RuntimeError("모델이 빈 응답을 반환했습니다. 잠시 후 다시 시도해 주세요.")

    data = json.loads(text)
    for comment in data.get("comments", []):
        comment["warnings"] = check_violations(comment.get("text", ""), style)

    usage = getattr(response, "usage", None)
    data["usage"] = {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
    }
    return data
