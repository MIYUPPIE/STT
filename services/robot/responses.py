# responses.py — the Yoruba spoken/printed confirmation for each move. This is the
# "response to it perfectly" half: the robot acts AND answers in Yoruba.
from .contract import FORWARD, BACKWARD, LEFT, RIGHT, STOP, NONE

RESPONSES = {
    FORWARD:  "Mo ń lọ síwájú.",      # I'm going forward
    BACKWARD: "Mo ń padà sẹ́yìn.",     # I'm going back
    LEFT:     "Mo yà sí òsì.",         # I turned left
    RIGHT:    "Mo yà sí ọ̀tún.",       # I turned right
    STOP:     "Mo dúró.",             # I stopped
}

# Spoken when the command was understood but the board didn't answer.
FAILED = "Ẹ̀rọ náà kò dáhùn."          # the device didn't respond


def response_for(action: str) -> str:
    """Yoruba confirmation for an executed action; '' for NONE (say nothing)."""
    if action == NONE:
        return ""
    return RESPONSES.get(action, "")
