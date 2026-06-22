"""Allow ``python -m deskbreak`` to behave like the ``deskbreak`` console script.

launchd is given ``<python> -m deskbreak run`` rather than the console-script
path so it does not depend on the user's PATH being set up inside the launchd
environment.
"""

from deskbreak.cli import main

if __name__ == "__main__":
    main()
