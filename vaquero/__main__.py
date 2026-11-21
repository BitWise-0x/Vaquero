import sys

if "--self-test" in sys.argv:
    from vaquero.selftest import run

    sys.exit(run())

from vaquero.app import main

main()
