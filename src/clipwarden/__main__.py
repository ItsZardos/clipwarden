import sys

from . import __version__


def main() -> int:
    print(f"ClipWarden {__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
