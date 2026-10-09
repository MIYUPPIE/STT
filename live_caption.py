# live_caption.py — always-on Yoruba live captioning + correction + speech.
#
# Architecture (concurrent stages, mic is never deaf):
#   1. PortAudio callback        -> pushes 512-sample frames into raw_q
#   2. VAD / segmenter thread    -> streaming Silero VAD finds speech start/end,
#                                   emits interim "partial" snapshots + final segments
#   3. Decode thread             -> faster-whisper (N-ATLAS Yoruba ASR by default,
#                                   services/stt) captions each segment,
#                                   prints partials live (\r) and finalizes on pause
#   4a. Refiner thread (default) -> Grok (xAI) corrects the caption (suggestion)
#   4b. Brain thread (--chat)    -> Grok (xAI) *responds* to the utterance, so
#                                   the system becomes a Yoruba voice assistant
#                                   (speech -> Whisper -> Grok brain -> Yarn TTS).
#   5. TTS thread (--speak/--chat) -> YarnGPT API reads the reply/correction aloud.
#
# --chat replaces the corrector with a conversational brain and speaks its reply
# aloud by default. Grok (brain/refine) and YarnGPT (TTS) are cloud APIs keyed
# from .env; only Whisper + VAD run locally (--cpu keeps the GPU free). Speaking
# is half-duplex: the mic mutes during playback so the spoken audio isn't
# transcribed back into a feedback loop.
import os
import sys
import time
import queue
import argparse
import threading
from collections import deque
from math import gcd

import numpy as np
import sounddevice as sd
import torch
from faster_whisper import WhisperModel
from faster_whisper.vad import get_vad_model

from services.refine import config as refine_cfg
from services.refine.refiner import build_refiner
from services.refine.contract import RefineRequest
from services.brain import config as brain_cfg
from services.brain.brain import build_brain
from services.brain.contract import ChatRequest
from services.tts import config as tts_cfg
from services.tts.engine import build_tts, chunk_text
from services.tts.contract import SynthRequest
from services.tts.speak import play as tts_play
from services.servo import config as servo_cfg
from services.servo.controller import build_controller
from services.robot import config as robot_cfg
from services.robot.controller import build_controller as build_robot
from services.robot.intent import build_parser as build_robot_parser
from services.robot.ros_sink import build_ros_controller
from services.robot.responses import RESPONSES, response_for
from services.stt import config as stt_cfg
from services.stt.model import resolve as resolve_stt

# ---------------- config ----------------
SAMPLE_RATE = 16000
WINDOW = 512                      # Silero VAD window @16kHz (32 ms)
CONTEXT = 64                      # Silero context samples

VAD_THRESHOLD = 0.5              # prob >= -> speech
VAD_NEG_THRESHOLD = 0.35        # prob <  -> candidate silence
MIN_SILENCE_MS = 450            # trailing silence that ends an utterance
SPEECH_PAD_MS = 200             # pre-roll kept before detected speech onset
MIN_SPEECH_MS = 300             # drop blips shorter than this (kills noise hallucinations)
MAX_SEG_SEC = 20                # force-flush a monologue this long (bounds latency/memory)
PARTIAL_EVERY_MS = 700          # refresh the live partial caption this often

USE_GPU = torch.cuda.is_available()
COMPUTE_TYPE = "float16" if USE_GPU else "int8"

DECODE_OPTS = dict(
    language=stt_cfg.LANGUAGE,
    beam_size=1,
    without_timestamps=True,
    condition_on_previous_text=False,
    no_speech_threshold=0.6,
    log_prob_threshold=-1.0,
    vad_filter=False,            # we already segmented with VAD
)

# ---------------- streaming VAD ----------------
class StreamingVAD:
    """Drives the bundled Silero onnx model window-by-window with persistent
    LSTM state, applying VADIterator-style hysteresis."""
    def __init__(self):
        self.sess = get_vad_model().session
        self.min_silence = int(MIN_SILENCE_MS / 1000 * SAMPLE_RATE)
        self.reset()

    def reset(self):
        self.h = np.zeros((1, 1, 128), "float32")
        self.c = np.zeros((1, 1, 128), "float32")
        self.ctx = np.zeros((1, CONTEXT), "float32")
        self.triggered = False
        self.temp_end = 0
        self.t = 0

    def __call__(self, window):
        """window: 512 float32 samples. Returns 'start', 'end', or None."""
        x = np.concatenate([self.ctx, window[None]], axis=1)
        out, self.h, self.c = self.sess.run(
            None, {"input": x, "h": self.h, "c": self.c}
        )
        self.ctx = window[None][:, -CONTEXT:]
        prob = float(out.reshape(-1)[0])
        self.t += WINDOW

        if prob >= VAD_THRESHOLD and self.temp_end:
            self.temp_end = 0
        if prob >= VAD_THRESHOLD and not self.triggered:
            self.triggered = True
            return "start"
        if prob < VAD_NEG_THRESHOLD and self.triggered:
            if not self.temp_end:
                self.temp_end = self.t
            if self.t - self.temp_end >= self.min_silence:
                self.temp_end = 0
                self.triggered = False
                return "end"
        return None


# ---------------- shared state ----------------
raw_q = queue.Queue()
final_q = queue.Queue()
refine_q = queue.Queue()          # finalized text awaiting LLM suggestion
chat_q = queue.Queue()            # finalized utterance awaiting a brain reply
servo_q = queue.Queue()           # finalized utterance awaiting a servo command
robot_q = queue.Queue()           # finalized utterance awaiting a robot command
tts_q = queue.Queue()             # text sentences awaiting synthesis (None = EOR)
audio_q = queue.Queue()           # synthesized audio chunks awaiting playback
partial_lock = threading.Lock()
partial = {"audio": None, "ver": 0}
stop = threading.Event()
speaking = threading.Event()      # set while TTS plays -> mic frames are dropped
busy = threading.Event()          # set for a whole command turn (recognize->move->
                                  # reply) so the mic ignores speech until it's done
command_mode = threading.Event()  # set when --robot/--servo: enables turn-based mic


# ---------------- speech queueing (sentence pipeline) ----------------
def enqueue_speech(text):
    """Queue a reply for speech, split into chunks (chunk_text) so TTS can
    synthesize+play the first chunk while the next is still synthesizing. A
    trailing None marks end-of-response so the player knows to reopen the mic."""
    chunks = chunk_text(text)
    if not chunks:
        return
    for chunk in chunks:
        tts_q.put(chunk)
    tts_q.put(None)


def enqueue_audio(audio, sr):
    """Play a pre-synthesized clip directly, bypassing YarnGPT synth, so fixed
    replies (robot confirmations) are instant. Trailing None ends the response and
    reopens the mic, exactly like enqueue_speech."""
    audio_q.put((audio, sr))
    audio_q.put(None)


# ---------------- console (single writer for all threads) ----------------
class Console:
    """All stdout goes through here under one lock so the async suggestion
    thread never tears the decoder's \\r partial line. Tracks the width of the
    in-progress partial so any writer can clear it before printing."""
    def __init__(self):
        self.lock = threading.Lock()
        self.live = 0             # width of the current partial line (0 = none)

    def partial(self, text):
        with self.lock:
            sys.stdout.write("\r\033[2m" + text.ljust(self.live) + "\033[0m")
            sys.stdout.flush()
            self.live = max(self.live, len(text))

    def final(self, text):
        with self.lock:
            if text:
                line = f"[{time.strftime('%H:%M:%S')}] {text}"
                sys.stdout.write("\r" + line.ljust(self.live) + "\n")
            else:
                sys.stdout.write("\r" + " " * self.live + "\r")   # drop blip
            sys.stdout.flush()
            self.live = 0

    def suggestion(self, text):
        with self.lock:
            if self.live:                      # wipe any partial under the cursor
                sys.stdout.write("\r" + " " * self.live + "\r")
                self.live = 0
            sys.stdout.write("\033[2m   ↳ " + text + "\033[0m\n")
            sys.stdout.flush()

    def reply(self, text):
        """The brain's spoken reply (--chat). Bright cyan so the dialogue reads
        as caption (what you said) then reply (what the assistant says back)."""
        with self.lock:
            if self.live:                      # wipe any partial under the cursor
                sys.stdout.write("\r" + " " * self.live + "\r")
                self.live = 0
            sys.stdout.write("\033[36m   ⟵ " + text + "\033[0m\n")
            sys.stdout.flush()

    def action(self, text):
        """A servo move (--servo). Bright yellow ⚙ so device actions stand out
        from captions and replies."""
        with self.lock:
            if self.live:                      # wipe any partial under the cursor
                sys.stdout.write("\r" + " " * self.live + "\r")
                self.live = 0
            sys.stdout.write("\033[33m   ⚙ " + text + "\033[0m\n")
            sys.stdout.flush()


# ---------------- mic capture rate + resampling ----------------
# Silero VAD and Whisper both need 16 kHz mono. Many mics (e.g. the ALC257 analog
# codec) refuse a 16 kHz ALSA rate, so we capture at a rate the device supports and
# resample each block down to 16 kHz. Prefer integer-ratio rates (48k -> /3) so a
# capture block maps to exactly WINDOW samples.
CAPTURE_RATE = SAMPLE_RATE
_RESAMPLER = None                  # set in main() when capture rate != 16 kHz


def pick_capture_rate(device):
    """First rate the input device actually accepts, preferring ones that divide
    cleanly to 16 kHz. Falls back to the device's own default."""
    for sr in (SAMPLE_RATE, 48000, 32000, 44100, 96000):
        try:
            sd.check_input_settings(device=device, samplerate=sr,
                                    channels=1, dtype="float32")
            return sr
        except Exception:
            continue
    info = (sd.query_devices(device, "input") if device is not None
            else sd.query_devices(kind="input"))
    return int(info["default_samplerate"])


def make_resampler(capture_rate):
    """Return f(block)->block resampled to 16 kHz (length scales by the ratio).
    The segmenter buffers and slices into WINDOW-sized pieces."""
    from scipy.signal import resample_poly
    g = gcd(SAMPLE_RATE, capture_rate)
    up, down = SAMPLE_RATE // g, capture_rate // g

    def resample(x):
        return resample_poly(x, up, down).astype("float32")
    return resample


def audio_cb(indata, frames, t, status):
    # input_overflow is non-fatal and would spam every callback under CPU load;
    # surface anything else.
    if status and not status.input_overflow:
        print(status, file=sys.stderr)
    # Deaf while TTS plays (half-duplex) AND for the whole command turn (busy), so
    # a new utterance can't step on the command still being processed/spoken.
    if speaking.is_set() or busy.is_set():
        return
    # Keep the realtime callback trivial: just hand off the raw block. Resampling
    # to 16 kHz happens in the segmenter thread so heavy DSP never stalls capture.
    raw_q.put(indata[:, 0].copy())


# ---------------- stage 2: segmenter ----------------
def segmenter():
    vad = StreamingVAD()
    pad_windows = max(1, int(SPEECH_PAD_MS / 1000 * SAMPLE_RATE / WINDOW))
    partial_samples = int(PARTIAL_EVERY_MS / 1000 * SAMPLE_RATE)
    min_speech = int(MIN_SPEECH_MS / 1000 * SAMPLE_RATE)
    max_seg = MAX_SEG_SEC * SAMPLE_RATE

    ring = deque(maxlen=pad_windows)
    frames, collecting, since_partial = [], False, 0

    def flush_final():
        nonlocal frames
        seg = np.concatenate(frames) if frames else np.array([], "float32")
        frames = []
        with partial_lock:
            partial["audio"] = None       # invalidate stale partial
        if len(seg) >= min_speech:
            if command_mode.is_set():
                busy.set()                # turn starts: go deaf until it's handled
            final_q.put(seg)
        else:
            final_q.put(None)             # too short -> just clear the partial line

    def process_window(win):              # win: 512 samples @ 16 kHz
        nonlocal collecting, frames, since_partial
        ring.append(win)
        ev = vad(win)
        if ev == "start":
            collecting = True
            frames = list(ring)           # include pre-roll
            since_partial = 0
        elif collecting:
            frames.append(win)
            since_partial += WINDOW
            total = sum(len(f) for f in frames)
            if total >= max_seg:          # bound very long monologues
                flush_final()
                collecting = False
            elif since_partial >= partial_samples:
                with partial_lock:
                    partial["audio"] = np.concatenate(frames)
                    partial["ver"] += 1
                since_partial = 0
        if ev == "end" and collecting:
            collecting = False
            flush_final()

    # Capture arrives in large blocks (for overflow headroom); resample the block
    # to 16 kHz, buffer it, and feed the VAD exactly WINDOW-sized windows. The
    # buffer absorbs rates whose resample doesn't land on a WINDOW multiple.
    buf = np.empty(0, "float32")
    while not stop.is_set():
        try:
            block = raw_q.get(timeout=0.1)
        except queue.Empty:
            continue
        if _RESAMPLER is not None:
            block = _RESAMPLER(block)
        buf = block if buf.size == 0 else np.concatenate([buf, block])
        while len(buf) >= WINDOW:
            process_window(buf[:WINDOW])
            buf = buf[WINDOW:]


# ---------------- stage 3: decoder + printer ----------------
def decoder(model, console, suggestions_on, speak_on, chat_on, servo_on, robot_on):
    last_ver = 0

    def decode(audio):
        segs, _ = model.transcribe(audio, **DECODE_OPTS)
        return "".join(s.text for s in segs).strip()

    while not stop.is_set():
        try:
            seg = final_q.get(timeout=0.02)          # finals take priority
            text = "" if seg is None else decode(seg)
            console.final(text)                       # print raw caption now
            if text:
                if servo_on:
                    servo_q.put(text)                 # drive the servo (parallel path)
                if robot_on:
                    robot_q.put(text)                 # robot owns the turn: caption
                                                      # refine/TTS stay quiet so only
                                                      # the robot's reply is spoken
                elif chat_on:
                    chat_q.put(text)                  # brain replies -> speak async
                elif suggestions_on:
                    refine_q.put(text)                # refine -> (speak) async
                elif speak_on:
                    enqueue_speech(text)              # no refiner: speak raw
            elif command_mode.is_set():
                busy.clear()                          # empty decode -> end the turn now
            last_ver = partial["ver"]
            continue
        except queue.Empty:
            pass
        with partial_lock:
            aud, ver = partial["audio"], partial["ver"]
        if aud is not None and ver != last_ver:
            console.partial(decode(aud))
            last_ver = ver
        else:
            time.sleep(0.01)


# ---------------- stage 4: LLM refiner (suggests the right sentence) ----------
def refiner_thread(refiner, console, speak_on):
    """Pops each finalized caption, asks Grok to correct it, prints a dim '↳'
    suggestion, and hands the corrected text to TTS. Slow API call lives here so
    it never blocks the mic, segmenter, or live decode."""
    while not stop.is_set():
        try:
            text = refine_q.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            res = refiner.refine(RefineRequest(text=text))
        except Exception as e:
            print(f"[refiner error] {e}", file=sys.stderr)
            if speak_on:
                enqueue_speech(text)
            continue
        out = res.refined or text                     # refined, or raw on failure
        if res.ok and out.strip() != text.strip():
            console.suggestion(out)
        if speak_on:
            enqueue_speech(out)                       # speak the corrected line


# ---------------- stage 4b: LLM brain (responds to the utterance) -------------
def brain_thread(brain, console, speak_on):
    """Pops each finalized utterance, asks Grok for a Yoruba reply, prints it as
    a '⟵' line, and hands it to TTS to speak. Multi-turn memory lives in the
    brain object. The slow API call lives here so it never blocks the mic."""
    while not stop.is_set():
        try:
            text = chat_q.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            res = brain.respond(ChatRequest(text=text))
        except Exception as e:
            print(f"[brain error] {e}", file=sys.stderr)
            continue
        if res.ok and res.reply:
            console.reply(res.reply)
            if speak_on:
                enqueue_speech(res.reply)             # speak the reply aloud
        elif not res.ok:
            print(f"[brain error] {res.error}", file=sys.stderr)


# ---------------- stage 4c: servo controller (Yoruba command -> ESP32) --------
def servo_thread(controller, console):
    """Pops each finalized caption, parses it into a servo intent (offline rules
    first, Grok fallback for the rest), and drives the ESP32 over HTTP. Only
    prints when a real move happens or a command failed, so ordinary speech that
    isn't a command stays silent. Lives on its own thread so the network round-
    trip never blocks the mic, segmenter, or decode."""
    while not stop.is_set():
        try:
            text = servo_q.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            res = controller.handle(text)
        except Exception as e:
            print(f"[servo error] {e}", file=sys.stderr)
            continue
        if res.moved:
            console.action(f"{res.intent.action} -> {res.angle}°  "
                           f"({res.intent.source}, {res.latency_ms:.0f} ms)")
        elif not res.ok:
            print(f"[servo error] {res.error}", file=sys.stderr)


# ---------------- stage 4d: robot controller (Yoruba command -> ESP32, WiFi/USB) -
def robot_thread(controller, console, speak_on, robot_audio):
    """Continuous voice control: a direction command latches motion (the robot keeps
    moving until another command or 'dúró'); the confirmation is a pre-cached clip
    played instantly (no per-command cloud synth), so the loop stays snappy. Only
    reacts to real commands; ordinary speech stays silent."""
    while not stop.is_set():
        try:
            text = robot_q.get(timeout=0.1)
        except queue.Empty:
            continue
        intent = controller.parser.parse(text)       # rules first, Grok fallback
        action = intent.action
        if action == "none":
            busy.clear()                             # not a command -> end the turn
            continue
        try:
            if action == "stop":
                ok, ack, err = controller.halt()
            else:
                speed = (robot_cfg.DEFAULT_SPEED if intent.speed is None
                         else intent.speed)
                ok, ack, err = controller.drive(action, speed)  # latched, continuous
        except Exception as e:
            print(f"[robot error] {e}", file=sys.stderr)
            busy.clear()
            continue
        if ok:
            console.action(f"{action} ({intent.source})  ⟵ {response_for(action)}")
        else:
            console.action(f"{action} FAILED: {err}")
            busy.clear()
            continue
        clip = robot_audio.get(action) if speak_on else None
        if clip is not None:
            enqueue_audio(*clip)                     # instant cached confirmation
        else:
            busy.clear()                             # no clip -> end the turn now


# ---------------- stage 5: TTS, split into synth + play so they overlap -------
def tts_synth_worker(tts):
    """Synthesize each sentence via the YarnGPT API and forward the audio (in
    order) to the player. Runs ahead of playback so the next sentence is being
    synthesized while the current one is spoken. None = end-of-response marker."""
    while not stop.is_set():
        try:
            item = tts_q.get(timeout=0.1)
        except queue.Empty:
            continue
        if item is None:
            audio_q.put(None)                        # forward EOR to the player
            continue
        res = tts.synth(SynthRequest(text=item))
        if res.ok and len(res.audio):
            audio_q.put((res.audio, res.sample_rate))
        elif not res.ok:
            print(f"[tts error] {res.error}", file=sys.stderr)


def tts_play_worker():
    """Play audio chunks in order. Half-duplex: the mic stays muted for the whole
    spoken response (first chunk until the end-of-response marker), including the
    gaps between sentences, so we never transcribe our own voice."""
    playing = False
    while not stop.is_set():
        try:
            item = audio_q.get(timeout=0.1)
        except queue.Empty:
            continue
        if item is None:                             # response finished
            if playing:
                with raw_q.mutex:
                    raw_q.queue.clear()
                speaking.clear()
                playing = False
            busy.clear()                             # end the command turn: mic listens
            continue                                 # again (even if synth made no audio)
        audio, sr = item
        if not playing:                              # first chunk -> mute the mic
            speaking.set()
            with raw_q.mutex:
                raw_q.queue.clear()
            playing = True
        try:
            tts_play(audio, sr)
        except Exception as e:
            print(f"[tts playback error] {e}", file=sys.stderr)


# ---------------- main ----------------
def parse_args():
    ap = argparse.ArgumentParser(
        description="Yoruba live captioning with optional Grok correction "
                    "(default) or a conversational Grok brain (--chat), plus "
                    "Yoruba TTS via the YarnGPT API.")
    ap.add_argument("--cpu", action="store_true",
                    help="force Whisper on CPU (Grok + TTS are cloud APIs)")
    ap.add_argument("--no-refine", action="store_true",
                    help="skip the Grok correction step")
    ap.add_argument("--chat", action="store_true",
                    help="conversational mode: Grok is the brain and replies to "
                         "each utterance; the reply is spoken aloud (Yoruba voice "
                         "assistant). Replaces the correction step.")
    ap.add_argument("--speak", action="store_true",
                    help="read each corrected caption aloud (half-duplex)")
    ap.add_argument("--servo", action="store_true",
                    help="drive the ESP32-S3 servo from Yoruba voice commands "
                         "(e.g. 'yà sí ọ̀tún', 'padà sí àárín', 'ọgọ́ta digiri'). "
                         "Works alongside captioning/refine/chat.")
    ap.add_argument("--robot", action="store_true",
                    help="drive a 2-wheel robot (ESP32-S3 over WiFi, or USB) from "
                         "Yoruba voice "
                         "commands: 'síwájú', 'sẹ́yìn', 'òsì', 'ọ̀tún', 'dúró'. Speaks "
                         "a Yoruba confirmation back with --speak.")
    ap.add_argument("--ros", action="store_true",
                    help="publish each Yoruba command onto ROS2 /cmd_vel via the "
                         "yoruba_robot voice_relay node (localhost TCP). Pair "
                         "with 'ros2 launch yoruba_robot sim.launch.py' or "
                         "'twin.launch.py'. If --robot is also on, voice_relay "
                         "owns the ESP32 (one writer).")
    ap.add_argument("--ros-host", default=None,
                    help="voice_relay host (default 127.0.0.1 or $ROS_SINK_HOST)")
    ap.add_argument("--ros-port", default=None, type=int,
                    help="voice_relay port (default 7447 or $ROS_SINK_PORT)")
    return ap.parse_args()


def main():
    args = parse_args()
    use_gpu = USE_GPU and not args.cpu
    device = "cuda" if use_gpu else "cpu"
    compute = "float16" if use_gpu else "int8"

    # STT model: N-ATLAS Yoruba ASR by default (built offline from the HF cache on
    # first run if needed), legacy whisper-small-yoruba as fallback.
    stt = resolve_stt()
    print(f"STT model: {stt.note}")

    def load_whisper(dev, comp):
        m = WhisperModel(stt.path, device=dev, compute_type=comp)
        list(m.transcribe(np.zeros(SAMPLE_RATE, "float32"), **DECODE_OPTS)[0])  # warmup
        return m

    print(f"Loading whisper on {device} ({compute})...")
    try:
        model = load_whisper(device, compute)
    except Exception as e:
        if device != "cuda":
            raise
        # GPU present but its CUDA libs (e.g. libcublas) aren't loadable -> don't
        # crash; fall back to CPU. Grok + TTS are cloud, so CPU is fine for control.
        print(f"  GPU load failed: {e.__class__.__name__}: {e}")
        print("  Falling back to CPU (int8). Pass --cpu to skip the GPU attempt.")
        device, compute = "cpu", "int8"
        model = load_whisper(device, compute)

    console = Console()

    # The Grok stage. --chat makes it a conversational brain (responds);
    # otherwise it's the refiner (corrects). Either way, probe with a real chat
    # call; if it can't run, fall back to plain captioning.
    refiner = brain = None
    suggestions_on = chat_on = False
    if args.chat:
        brain = build_brain()
        if brain_cfg.ENABLED:
            print(f"Checking Grok brain ({brain_cfg.MODEL})...", end=" ", flush=True)
            if brain.health():
                print("ok. Conversational mode on.")
                chat_on = True
            else:
                print("unavailable - captioning only.")
                print(f"  reason: {brain.last_error}")
        else:
            print("Brain disabled. Captioning only.")
    else:
        refiner = build_refiner()
        if refine_cfg.ENABLED and not args.no_refine:
            print(f"Checking Grok refiner ({refine_cfg.MODEL})...", end=" ", flush=True)
            if refiner.health():
                print("ok. Suggestions on.")
                suggestions_on = True
            else:
                print("unavailable - captioning only.")
                print(f"  reason: {refiner.last_error}")
        else:
            print("Refiner off. Captioning only.")

    # Yoruba TTS (YarnGPT API). --chat speaks the brain's reply by default;
    # --speak (in refine mode) speaks the correction. Probe with one real synth;
    # if the API is unreachable, no speech (chat still prints replies).
    tts = None
    speak_on = False
    want_tts = tts_cfg.ENABLED and (chat_on or (args.speak and not args.chat))
    if want_tts:
        print(f"Checking TTS (YarnGPT, voice={tts_cfg.VOICE})...", end=" ", flush=True)
        tts = build_tts()
        warm = tts.synth(SynthRequest(text="Báwo ni"))   # verify key + reachable
        if warm.ok:
            what = "replies" if chat_on else "corrections"
            print(f"ok. Speaking {what} aloud.")
            speak_on = True
        else:
            print("unavailable - no speech.")
            print(f"  reason: {tts.last_error}")

    # Servo control (ESP32-S3 over HTTP). --servo parses each caption into a servo
    # command (offline Yoruba rules first, Grok fallback for the rest) and drives
    # the board. Probe it once; if the board is unreachable, warn and keep going
    # (captioning still works) instead of crashing.
    controller = None
    servo_on = False
    if args.servo and servo_cfg.ENABLED:
        print(f"Checking servo board ({servo_cfg.HOST})...", end=" ", flush=True)
        controller = build_controller()
        if controller.health():
            print(f"ok. Servo control on (angle={controller.angle}°).")
            servo_on = True
        else:
            print("unreachable - servo control off.")
            print(f"  reason: {controller.last_error}")
            print(f"  fix: flash firmware/esp32s3_servo, or set SERVO_HOST in .env")
    elif args.servo:
        print("Servo disabled (SERVO_ENABLED=0).")

    # Robot control (ESP32-S3 on USB; this laptop is the server). --robot parses
    # each caption into a movement command and drives the board over serial,
    # speaking a Yoruba confirmation back. Probe the serial link once; if the board
    # can't be opened, warn and keep going (captioning still works).
    robot = None
    robot_on = False
    if args.robot and robot_cfg.ENABLED:
        print(f"Finding robot (link={robot_cfg.LINK}: WiFi then USB)...",
              end=" ", flush=True)
        robot = build_robot()
        if robot.health():
            how = getattr(robot.link.transport, "found_by", "")
            print(f"ok on {robot.port}{f' via {how}' if how else ''}. "
                  "Robot control on.")
            robot_on = True
        else:
            print("unavailable - robot control off.")
            print(f"  reason: {robot.last_error}")
            print("  fix: power the robot and put this laptop on the robot's WiFi "
                  "(or set ROBOT_HOST=<ip> in .env); or plug in USB")
    elif args.robot:
        print("Robot disabled (ROBOT_ENABLED=0).")

    # --ros: publish each parsed command onto /cmd_vel via the voice_relay node.
    # If --robot is also on, voice_relay (not us) owns the ESP32: the real robot
    # and Gazebo twin both subscribe to the same /cmd_vel. If --robot is off,
    # --ros alone drives only the sim.
    if args.ros:
        parser_ = (robot.parser if robot_on and robot is not None
                   else build_robot_parser())
        print("Connecting ROS voice_relay...", end=" ", flush=True)
        ros_ctrl = build_ros_controller(parser_, args.ros_host, args.ros_port)
        if ros_ctrl.health():
            print(f"ok on {ros_ctrl.port}.")
            if robot_on and robot is not None and robot is not ros_ctrl:
                try:
                    robot.halt(); robot.stop_keepalive(); robot.close()
                except Exception:
                    pass
            robot = ros_ctrl
            robot_on = True
        else:
            print("unavailable - ROS control off.")
            print(f"  reason: {ros_ctrl.last_error}")
            print("  fix: ros2 launch yoruba_robot sim.launch.py "
                  "(or twin.launch.py), then: "
                  "ros2 run yoruba_robot voice_relay")

    # Pre-synthesize the fixed robot confirmations once (forward/back/left/right/
    # stop) so each spoken reply is instant instead of a per-command YarnGPT call,
    # and start the continuous-drive keepalive (a direction command latches motion).
    robot_audio = {}
    if robot_on:
        if speak_on and tts is not None:
            print("Caching robot voice replies...", end=" ", flush=True)
            for act, phrase in RESPONSES.items():
                r = tts.synth(SynthRequest(text=phrase))
                if r.ok and len(r.audio):
                    robot_audio[act] = (r.audio, r.sample_rate)
            print(f"{len(robot_audio)}/{len(RESPONSES)} cached.")
        robot.start_keepalive()

    threads = [
        threading.Thread(target=segmenter, daemon=True),
        threading.Thread(target=decoder,
                         args=(model, console, suggestions_on, speak_on, chat_on,
                               servo_on, robot_on),
                         daemon=True),
    ]
    if servo_on:
        threads.append(threading.Thread(
            target=servo_thread, args=(controller, console), daemon=True))
    if robot_on:
        threads.append(threading.Thread(
            target=robot_thread, args=(robot, console, speak_on, robot_audio),
            daemon=True))
    if chat_on:
        threads.append(threading.Thread(
            target=brain_thread, args=(brain, console, speak_on), daemon=True))
    elif suggestions_on:
        threads.append(threading.Thread(
            target=refiner_thread, args=(refiner, console, speak_on), daemon=True))
    if speak_on:
        threads.append(threading.Thread(
            target=tts_synth_worker, args=(tts,), daemon=True))
        threads.append(threading.Thread(
            target=tts_play_worker, daemon=True))
    for t in threads:
        t.start()

    banner = ("Yoruba voice assistant. Speak Yoruba; the brain replies"
              + (" aloud" if speak_on else "") + ". Ctrl+C to stop."
              if chat_on else
              "Yoruba live captioning. Speak freely. Ctrl+C to stop.")
    if servo_on:
        banner += "\nServo: say 'yà sí ọ̀tún' / 'òsì' / 'padà sí àárín' / '<n> digiri' / 'dúró'."
    if robot_on:
        banner += ("\nRobot (continuous): 'síwájú'/'sẹ́yìn'/'òsì'/'ọ̀tún' drive until "
                   "you say 'dúró' (add 'kíákíá'/'díẹ̀díẹ̀' for speed).")
    print(banner + "\n")

    if robot_on:
        command_mode.set()            # turn-based mic: each command is fully handled
                                      # (recognized -> moved -> reply spoken) before
                                      # the mic listens for the next one

    # Pick a capture rate the mic accepts; resample to 16 kHz if it isn't native.
    global CAPTURE_RATE, _RESAMPLER
    mic = os.environ.get("MIC_DEVICE") or None
    if mic is not None and mic.lstrip("-").isdigit():
        mic = int(mic)
    CAPTURE_RATE = pick_capture_rate(mic)
    # Capture several VAD windows per callback (big block) + a deep buffer so a
    # CPU spike (e.g. Whisper decoding) can't overflow the mic and drop audio.
    BLOCK_WINDOWS = 8                            # ~256 ms per callback
    blocksize = round(WINDOW * CAPTURE_RATE / SAMPLE_RATE) * BLOCK_WINDOWS
    if CAPTURE_RATE != SAMPLE_RATE:
        _RESAMPLER = make_resampler(CAPTURE_RATE)
        print(f"Mic capturing at {CAPTURE_RATE} Hz, resampling to {SAMPLE_RATE} Hz.\n")

    try:
        with sd.InputStream(samplerate=CAPTURE_RATE, channels=1, dtype="float32",
                            blocksize=blocksize, device=mic, latency=0.4,
                            callback=audio_cb):
            while True:
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop.set()
        if robot_on:
            robot.stop_keepalive()
            try:
                robot.halt()             # never leave the robot driving on exit
            except Exception:
                pass
        for t in threads:
            t.join(timeout=1)
        print("Stopped.")


if __name__ == "__main__":
    main()
