import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Hermetic: whether a frontend build happens to exist in the checkout must not
# change what the API tests see. Tests of the dashboard serving set their own.
os.environ["APM_FRONTEND_DIST"] = str(Path(__file__).parent / "no-frontend-build")
