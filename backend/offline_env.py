"""
Process-wide environment that stops third-party libraries using the network.

Importing this module applies the settings. It must be imported before any of
the machine-learning libraries, because several of them read these variables
once, at import time.

Found by running the speech pipeline on a real recording with outbound
connections blocked: pyannote.audio 4.x ships with usage telemetry enabled by
default and repeatedly tried to send OpenTelemetry traces to its vendor's
server (otel.pyannote.ai). Nothing failed, which is why it went unnoticed; on a
connected machine those requests would have succeeded.

Values are forced rather than defaulted. On a secured installation a stray
environment variable must not be able to re-enable telemetry.
"""
import os

OFFLINE_ENV = {
    # Hugging Face: never contact the Hub; models load from local directories.
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    # pyannote.audio usage telemetry, enabled by its default config.
    "PYANNOTE_METRICS_ENABLED": "false",
    # Any OpenTelemetry SDK use by a dependency becomes a no-op.
    "OTEL_SDK_DISABLED": "true",
    # ChromaDB telemetry; the client is also constructed with it disabled.
    "ANONYMIZED_TELEMETRY": "False",
    # Honoured by a number of Python tools as a general opt-out.
    "DO_NOT_TRACK": "1",
}


def apply() -> None:
    os.environ.update(OFFLINE_ENV)


apply()
