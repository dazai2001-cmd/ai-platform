"""Use the shared encoder loader for both benchmarks and the local video feature."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.local_agent.layer_encoder import load_encoder

__all__ = ["load_encoder"]
