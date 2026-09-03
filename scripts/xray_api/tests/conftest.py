import sys
from pathlib import Path

api_dir = Path(__file__).parent.parent.resolve()
if str(api_dir) not in sys.path:
    sys.path.insert(0, str(api_dir))

gen_dir = api_dir / "generated"
if str(gen_dir) not in sys.path:
    sys.path.insert(1, str(gen_dir))
