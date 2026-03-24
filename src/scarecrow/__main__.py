"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

import contextlib


def main() -> None:
    from scarecrow.app import ScarecrowApp

    app = ScarecrowApp()
    with contextlib.suppress(KeyboardInterrupt):
        app.run()


if __name__ == "__main__":
    main()
