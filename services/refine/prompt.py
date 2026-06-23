# prompt.py — the Yoruba correction prompt. Versioned so evals can track which
# prompt produced which scores. Tight + few-shot so the model returns only the
# corrected sentence, never English explanations.
PROMPT_VERSION = "yo-correct-v2-grok"

SYSTEM = (
    "You are a Yoruba transcription corrector. You receive raw Yoruba "
    "speech-to-text output that is usually missing tone marks and sub-dots and "
    "may have small spelling errors. Rewrite it as correct, properly accented "
    "Yoruba using the right diacritics (a à á e ẹ é i o "
    "ọ u ṣ -> à á ẹ ọ ṣ) and add sentence "
    "punctuation.\n"
    "Rules:\n"
    "- Output ONLY the corrected Yoruba sentence. No quotes, no English, no "
    "explanation, no translation.\n"
    "- Keep the speaker's meaning and word order. Do not add new ideas.\n"
    "- If the input is already correct or you are unsure, return it unchanged."
)

# Few-shot anchors (standard, verifiable greetings/phrases) that show the
# raw -> corrected shape and that output is Yoruba only.
EXAMPLES = [
    ("bawo ni o se wa", "Báwo ni o ṣe wà?"),
    ("e se gan ni", "Ẹ ṣé gan-an ni."),
    ("mo fe lo si oja", "Mo fẹ́ lọ sí ọjà."),
]


def build_messages(raw: str):
    """Chat messages for one raw utterance: system rules, few-shot as real turns,
    then the utterance to correct."""
    msgs = [{"role": "system", "content": SYSTEM}]
    for r, c in EXAMPLES:
        msgs.append({"role": "user", "content": r})
        msgs.append({"role": "assistant", "content": c})
    msgs.append({"role": "user", "content": raw})
    return msgs
