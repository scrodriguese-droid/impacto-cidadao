import sys
from pathlib import Path

# Permite `import schemas`, `import simuladores`... a partir de backend/tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
