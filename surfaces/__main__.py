"""python3 -m surfaces"""

from investigation.env import load_dotenv

from .server import main

load_dotenv()
raise SystemExit(main())
