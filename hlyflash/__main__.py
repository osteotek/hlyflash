"""Allow python -m hlyflash as well as the installed command."""
from .cli import main

raise SystemExit(main())
