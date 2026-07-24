from __future__ import annotations

"""한국어 조사 처리 유틸.

코드가 조립하는 자연문에서 `f"{name}은"` 류의 고정 조사가 받침에 따라 틀리는 문제를
막는다. 마지막 글자가 한글이 아니면(영문·숫자·기호) 모음형을 기본값으로 쓴다.
"""

_JOSA_PAIRS = {
    "은는": ("은", "는"),
    "이가": ("이", "가"),
    "을를": ("을", "를"),
    "과와": ("과", "와"),
    "으로로": ("으로", "로"),
}


def _has_batchim(char: str) -> bool | None:
    code = ord(char)
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 != 0
    return None


def josa(word: str, pair: str) -> str:
    """단어에 맞는 조사를 반환한다. 예: josa("잠실 관광특구", "은는") -> "는"."""
    consonant_form, vowel_form = _JOSA_PAIRS[pair]
    text = str(word).strip()
    if not text:
        return vowel_form
    # 괄호·따옴표·마침표로 끝나는 고유명사는 실제 마지막 음절을 기준으로
    # 조사한다. 예: "교대역(법원.검찰청)" + 은/는 -> "은".
    text = text.rstrip(" \t\r\n)]}〉》」』】”’\"'.,!?;:")
    if not text:
        return vowel_form
    batchim = _has_batchim(text[-1])
    if batchim is None:
        return vowel_form
    if pair == "으로로" and batchim:
        # ㄹ 받침은 "로"를 쓴다 (예: 서울로).
        if (ord(text[-1]) - 0xAC00) % 28 == 8:
            return vowel_form
    return consonant_form if batchim else vowel_form


def with_josa(word: str, pair: str) -> str:
    """단어+조사 결합 문자열. 예: with_josa("명동", "은는") -> "명동은"."""
    return f"{word}{josa(word, pair)}"
