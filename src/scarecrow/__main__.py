"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""


def main() -> None:
    from scarecrow.app import ScarecrowApp

    app = ScarecrowApp()
    app.run()


if __name__ == "__main__":
    main()
