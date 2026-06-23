# services/refine

Turns a raw Yoruba caption into corrected, properly accented Yoruba using
**Grok (xAI)**. Used by `live_caption.py` to print a suggested sentence under
each caption.

## Contract (`contract.py`)

```python
RefineRequest(text: str)
RefineResult(raw, refined, ok, error, latency_ms)
```

Callers do:

```python
from services.refine.refiner import build_refiner
from services.refine.contract import RefineRequest

refiner = build_refiner()
if refiner.health():                      # real chat probe (valid key + reachable)
    res = refiner.refine(RefineRequest(text="bawo ni o se wa"))
    print(res.refined)                    # -> "Báwo ni o ṣe wà?"
```

Guarantee: if the API is unreachable or the key is bad, `refine()` returns
`ok=False` and `refined == raw`. Callers never crash and never lose a caption.

## CLI

```bash
python3 -m services.refine.refiner --health
python3 -m services.refine.refiner "bawo ni o se wa loni"
```

## Tests (gate — free, deterministic, <2s)

No network; the HTTP transport is injected with a fake.

```bash
python3 -m unittest services.refine.tests.test_refiner -v
```

## Evals (periodic — paid, needs XAI_API_KEY)

Hits the real model, scores Yoruba quality heuristically (no refusal, no English
drift, diacritics added), threshold 70%.

```bash
python3 services/refine/evals/eval_yoruba.py
```

## Config (env / .env)

| Var | Default | Meaning |
|-----|---------|---------|
| `XAI_API_KEY` | (from `.env`) | xAI / Grok key — required |
| `XAI_API_URL` | `https://api.x.ai/v1/chat/completions` | endpoint |
| `REFINE_MODEL` / `XAI_MODEL` | `grok-4.3` | correction model |
| `REFINE_ENABLED` | `1` | `0` = caption-only |
| `REFINE_TIMEOUT` | `30` | per-call seconds |
| `REFINE_HEALTH_TIMEOUT` | `30` | startup probe (1-token chat) |
| `REFINE_MAX_TOKENS` | `96` | output cap |
| `REFINE_TEMPERATURE` | `0` | deterministic correction |
